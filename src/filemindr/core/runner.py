from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

from loguru import logger

from filemindr.core.config import expand_path
from filemindr.core.history import HistoryWriter, prune_history, retention_days_default
from filemindr.core.ignore import load_ignore_patterns, matches_ignore_pattern
from filemindr.core.operations import (
    INTERNAL_PREFIX,
    POLICIES,
    OperationPlan,
    PartialOperationError,
    destination_identity,
    execute_operation,
    same_file,
)
from filemindr.core.templating import render_name_template, render_target_template
from filemindr.core.validator import load_validated_config


@dataclass(frozen=True)
class Rule:
    name: str
    priority: int
    extensions: set[str]
    regex: re.Pattern | None
    older_than_days: int | None
    move_to: str | None
    copy_to: str | None
    rename_template: str | None
    conflict_policy: str | None


def _trash(path: Path) -> None:
    try:
        from send2trash import send2trash

        send2trash(str(path))
    except Exception as exc:
        raise RuntimeError(f"Failed to send '{path}' to trash") from exc


def _normalize_ext(ext: str) -> str:
    return ext.lower().lstrip(".")


def _load_rules(config: dict[str, Any], *, base_dir: Path | None = None) -> list[Rule]:
    rules: list[Rule] = []
    for item in config.get("rules") or []:
        match = item.get("match") or {}
        action = item.get("action") or {}
        if bool(action.get("move_to")) == bool(action.get("copy_to")):
            raise ValueError(
                f"Rule '{item.get('name', 'unnamed')}' must define exactly one of action.move_to or action.copy_to"
            )
        rules.append(
            Rule(
                name=item.get("name", "unnamed"),
                priority=int(item.get("priority", 0)),
                extensions={
                    _normalize_ext(ext) for ext in match.get("extensions") or []
                },
                regex=re.compile(match["regex"]) if match.get("regex") else None,
                older_than_days=int(match["older_than_days"])
                if match.get("older_than_days") is not None
                else None,
                move_to=action.get("move_to"),
                copy_to=action.get("copy_to"),
                rename_template=action.get("rename_template"),
                conflict_policy=action.get("conflict_policy"),
            )
        )
    return sorted(rules, key=lambda rule: rule.priority, reverse=True)


def _is_older_than(file: Path, days: int) -> bool:
    return time.time() - file.stat().st_mtime >= days * 86400


def _match_rule(file: Path, rules: list[Rule]) -> Rule | None:
    ext = _normalize_ext(file.suffix)
    for rule in rules:
        if rule.extensions and ext not in rule.extensions:
            continue
        if rule.regex and not rule.regex.search(file.name):
            continue
        if rule.older_than_days is not None and not _is_older_than(
            file, rule.older_than_days
        ):
            continue
        return rule
    return None


def _resolve_conflict(
    dest: Path, policy: str, *, reserved: set[Path] | None = None
) -> Path | None:
    policy = policy.lower()
    if policy not in POLICIES:
        raise ValueError(f"Invalid conflict policy: {policy}")
    reserved = reserved or set()
    if not dest.exists() and not dest.is_symlink() and dest not in reserved:
        return dest
    if dest.is_symlink() or (dest.exists() and not dest.is_file()):
        raise ValueError(f"Destination is not a regular file: {dest}")
    if policy in {"overwrite", "trash"}:
        return dest
    if policy == "skip":
        return None
    i = 1
    while True:
        candidate = dest.with_name(f"{dest.stem} ({i}){dest.suffix}")
        if (
            not candidate.exists()
            and not candidate.is_symlink()
            and candidate not in reserved
        ):
            return candidate
        i += 1


def _resolve_destination(
    file: Path,
    rule: Rule | None,
    *,
    default_target: str,
    base_dir: Path | None = None,
) -> Path:
    template = (rule.copy_to or rule.move_to) if rule else default_target
    directory = render_target_template(template, file=file, base_dir=base_dir)
    if directory == file.parent.resolve():
        raise ValueError(
            "Destination directory must differ from source (prevents repeated watch processing)"
        )
    name = (
        render_name_template(rule.rename_template, file=file)
        if rule and rule.rename_template
        else file.name
    )
    destination = directory / name
    if destination.is_symlink():
        raise ValueError(f"Destination symlinks are unsupported: {destination}")
    if same_file(file, destination):
        raise ValueError("Source and destination refer to the same file")
    return destination


def run_pipeline(
    config_path: str,
    dry_run: bool = False,
    only_paths: set[Path] | None = None,
    *,
    profile: str | None = None,
    command: str | None = None,
    report_path: Path | None = None,
    stop_event: Event | None = None,
) -> dict[str, int]:
    cfg_abs = Path(config_path).expanduser().resolve()
    config = load_validated_config(cfg_abs)
    if report_path is not None and (report_path.exists() or report_path.is_symlink()):
        raise FileExistsError(f"Report path already exists: {report_path}")
    source = expand_path(config["source"], base_dir=cfg_abs.parent)
    default_target = config.get(
        "default_target", str(source / "others").replace("{", "{{").replace("}", "}}")
    )
    global_policy = str(config.get("conflict_policy", "rename"))
    ignore_patterns = load_ignore_patterns(config)
    rules = _load_rules(config)
    history = None
    if profile and command in {"run", "watch"}:
        prune_history(days=retention_days_default())
        history = HistoryWriter(
            profile=profile,
            command=command,
            config_path=cfg_abs,
            source=source,
            dry_run=dry_run,
        )

    summary = dict.fromkeys(
        (
            "files_scanned",
            "moved",
            "copied",
            "overwritten",
            "skipped",
            "errors",
            "trashed",
            "planned_moves",
            "planned_copies",
        ),
        0,
    )
    by_rule: Counter[str] = Counter()
    events: list[dict[str, Any]] = []
    reserved: set[Path] = set()

    def record(event: dict[str, Any]) -> None:
        if history:
            history.record(event)
        if report_path is not None:
            events.append(event)

    # Watch batches must not rescan a large directory for every few changed files.
    files = sorted(only_paths) if only_paths is not None else source.iterdir()
    logger.info(f"Scanning: {source}")
    try:
        for file in files:
            if stop_event is not None and stop_event.is_set():
                break
            if file.parent != source or file.is_symlink() or not file.is_file():
                continue
            if file.name.startswith(INTERNAL_PREFIX) or file == cfg_abs:
                continue
            if report_path is not None and file == report_path.absolute():
                continue
            if matches_ignore_pattern(file, ignore_patterns):
                continue
            summary["files_scanned"] += 1
            event: dict[str, Any] = {
                "source": str(file),
                "rule": "default",
                "policy": global_policy.lower(),
            }
            try:
                rule = _match_rule(file, rules)
                rule_name = rule.name if rule else "default"
                policy = str(
                    rule.conflict_policy
                    if rule and rule.conflict_policy
                    else global_policy
                ).lower()
                destination = _resolve_destination(
                    file, rule, default_target=default_target, base_dir=cfg_abs.parent
                )
                event.update(
                    rule=rule_name, policy=policy, destination=str(destination)
                )
                resolved = _resolve_conflict(destination, policy, reserved=reserved)
                if resolved is None:
                    summary["skipped"] += 1
                    by_rule[rule_name] += 1
                    record(event | {"event": "skipped"})
                    continue
                event["destination"] = str(resolved)
                action = "COPY" if rule and rule.copy_to else "MOVE"
                if dry_run:
                    reserved.add(resolved)
                    summary[
                        "planned_copies" if action == "COPY" else "planned_moves"
                    ] += 1
                    record(
                        event
                        | {
                            "event": "planned_copy"
                            if action == "COPY"
                            else "planned_move"
                        }
                    )
                    logger.debug(f"[DRY] {action} {file} -> {resolved}")
                else:
                    plan = OperationPlan(
                        file, resolved, action, policy, destination_identity(resolved)
                    )
                    if history:
                        record(
                            event
                            | {
                                "event": "operation_started",
                                "action": action,
                                "undo_supported": False,
                            }
                        )
                    result = execute_operation(plan, trash=_trash)
                    if result.replaced:
                        replaced_event = (
                            "trashed" if policy == "trash" else "overwritten"
                        )
                        summary[replaced_event] += 1
                        record(
                            event
                            | {
                                "event": replaced_event,
                                "path": str(resolved),
                                "undo_supported": False,
                            }
                        )
                    key = "copied" if action == "COPY" else "moved"
                    summary[key] += 1
                    record(
                        event
                        | {
                            "event": key,
                            "identity": result.identity,
                            "undo_supported": action == "MOVE"
                            and not result.replaced
                            and result.identity["inode"] != 0,
                        }
                    )
                    logger.debug(f"{action} {file} -> {resolved}")
                by_rule[rule_name] += 1
            except Exception as exc:
                summary["errors"] += 1
                if isinstance(exc, PartialOperationError):
                    event["recovery_path"] = str(exc.recovery_path)
                record(
                    event
                    | {"event": "error", "error": str(exc), "undo_supported": False}
                )
                logger.error(f"ERROR processing {file}: {exc}")
        if history:
            if stop_event is not None and stop_event.is_set():
                history.fail("Interrupted between file operations")
            elif summary["errors"]:
                history.fail(f"{summary['errors']} file operation(s) failed")
            else:
                history.complete()
    except BaseException as exc:
        if history:
            history.fail(str(exc) or type(exc).__name__)
        raise

    logger.info("==== SUMMARY ====")
    logger.info(f"Dry-run: {dry_run}")
    logger.info(f"Files scanned: {summary['files_scanned']}")
    for key, label in (
        (("planned_moves", "Planned moves"), ("planned_copies", "Planned copies"))
        if dry_run
        else (
            ("moved", "Moved"),
            ("copied", "Copied"),
            ("overwritten", "Overwritten"),
            ("skipped", "Skipped"),
            ("errors", "Errors"),
            ("trashed", "Trashed"),
        )
    ):
        logger.info(f"{label}: {summary[key]}")
    logger.info("By rule:")
    for name, count in by_rule.most_common():
        logger.info(f"  - {name}: {count}")
    if report_path is not None:
        payload = dict(
            profile=profile,
            command=command or "run_pipeline",
            config_path=str(cfg_abs),
            source=str(source),
            dry_run=dry_run,
            summary=summary,
            by_rule=dict(by_rule),
            events=events,
        )
        # Reports may not clobber any existing file, including source data/configs.
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, indent=2))
    return summary
