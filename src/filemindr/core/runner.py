from __future__ import annotations

import re
import shutil
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from filemindr.core.config import expand_path
from filemindr.core.templating import render_name_template, render_target_template


def _p(value: str, *, base_dir: Path | None = None) -> Path:
    return expand_path(value, base_dir=base_dir)


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
    """
    Send a file to the OS trash/recycle bin.
    Raises if trash is not available or the operation fails.
    """
    try:
        from send2trash import send2trash
        send2trash(str(path))
    except Exception as exc:
        raise RuntimeError(f"Failed to send '{path}' to trash") from exc


def _normalize_ext(ext: str) -> str:
    return ext.lower().lstrip(".")


def _action_name(rule: Rule | None) -> str:
    return "COPY" if (rule and rule.copy_to) else "MOVE"


def _log_rule_choice(rule: Rule | None) -> str:
    return f"rule={rule.name} prio={rule.priority}" if rule else "rule=default"


def _load_rules(config: dict[str, Any], *, base_dir: Path | None = None) -> list[Rule]:
    rules_cfg = config.get("rules", [])
    rules: list[Rule] = []

    for rule_cfg in rules_cfg:
        name = rule_cfg.get("name", "unnamed")
        priority = int(rule_cfg.get("priority", 0))

        match = rule_cfg.get("match", {}) or {}
        exts = {_normalize_ext(ext) for ext in match.get("extensions", [])}
        regex_raw = match.get("regex")
        regex = re.compile(regex_raw) if regex_raw else None
        older = match.get("older_than_days")
        older_than_days = int(older) if older is not None else None

        action = rule_cfg.get("action", {}) or {}
        move_to_str = action.get("move_to")
        copy_to_str = action.get("copy_to")
        rule_policy = action.get("conflict_policy")

        if bool(move_to_str) == bool(copy_to_str):
            raise ValueError(
                f"Rule '{name}' must define exactly one of action.move_to or action.copy_to"
            )

        rules.append(
            Rule(
                name=name,
                priority=priority,
                extensions=exts,
                regex=regex,
                older_than_days=older_than_days,
                move_to=str(move_to_str) if move_to_str else None,
                copy_to=str(copy_to_str) if copy_to_str else None,
                rename_template=str(action.get("rename_template")) if action.get("rename_template") else None,
                conflict_policy=str(rule_policy) if rule_policy else None,
            )
        )

    rules.sort(key=lambda item: item.priority, reverse=True)
    return rules


def _is_older_than(file: Path, days: int) -> bool:
    cutoff_seconds = days * 24 * 60 * 60
    age_seconds = time.time() - file.stat().st_mtime
    return age_seconds >= cutoff_seconds


def _match_rule(file: Path, rules: list[Rule]) -> Rule | None:
    ext = _normalize_ext(file.suffix)
    filename = file.name

    for rule in rules:
        if rule.extensions and ext not in rule.extensions:
            continue
        if rule.regex and not rule.regex.search(filename):
            continue
        if rule.older_than_days is not None and not _is_older_than(file, rule.older_than_days):
            continue
        return rule

    return None


def _resolve_conflict(dest: Path, policy: str) -> Path | None:
    """
    Returns:
      - Path to use (possibly renamed), or
      - None if policy == skip and file exists.
    """
    if not dest.exists():
        return dest

    policy = policy.lower()

    if policy in {"overwrite", "trash"}:
        return dest
    if policy == "skip":
        return None

    stem, suffix = dest.stem, dest.suffix
    parent = dest.parent
    i = 1
    while True:
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def _resolve_destination(
    file: Path,
    rule: Rule | None,
    *,
    default_target: str,
    base_dir: Path | None = None,
) -> Path:
    target_template = (rule.copy_to or rule.move_to) if rule else default_target
    dest_dir = render_target_template(target_template, file=file, base_dir=base_dir)

    if rule and rule.rename_template:
        file_name = render_name_template(rule.rename_template, file=file)
    else:
        file_name = file.name

    return dest_dir / file_name


def run_pipeline(config_path: str, dry_run: bool = False, only_paths: set[Path] | None = None) -> None:
    cfg_abs = Path(config_path).expanduser().resolve()
    if not cfg_abs.exists():
        raise FileNotFoundError(config_path)

    config = yaml.safe_load(cfg_abs.read_text()) or {}
    base_dir = cfg_abs.parent

    source = _p(config["source"], base_dir=base_dir)
    default_target = str(config.get("default_target", str(source / "others")))
    global_policy = str(config.get("conflict_policy", "rename"))
    rules = _load_rules(config, base_dir=base_dir)

    total_files = 0
    moved = 0
    copied = 0
    skipped = 0
    overwritten = 0
    errors = 0

    by_rule = Counter()
    by_action = Counter()

    logger.info(f"Scanning: {source}")

    for file in source.iterdir():
        if not file.is_file():
            continue
        if file.resolve() == cfg_abs:
            continue
        if only_paths is not None and file not in only_paths:
            continue

        total_files += 1

        rule = _match_rule(file, rules)
        rule_name = rule.name if rule else "default"
        dest = _resolve_destination(file, rule, default_target=default_target, base_dir=base_dir)

        policy = (rule.conflict_policy if rule and rule.conflict_policy else global_policy).lower()
        resolved = _resolve_conflict(dest, policy)
        if resolved is None:
            skipped += 1
            by_rule[rule_name] += 1
            by_action["skipped"] += 1
            logger.debug(f"SKIP (exists): {dest}")
            continue

        action_name = _action_name(rule)
        chosen = _log_rule_choice(rule)
        will_replace = resolved.exists() and policy in {"overwrite", "trash"}

        if dry_run:
            logger.debug(f"[DRY] ensure dir: {resolved.parent}")

        if dry_run:
            by_rule[rule_name] += 1
            if action_name == "COPY":
                by_action["planned_copies"] += 1
            else:
                by_action["planned_moves"] += 1
            logger.debug(f"[DRY] {action_name} {chosen} | {file} -> {resolved}")
            continue

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)

            if will_replace:
                if policy == "trash":
                    _trash(resolved)
                    by_action["trashed"] += 1
                else:
                    resolved.unlink()
                    overwritten += 1
                    by_action["overwritten"] += 1

            if action_name == "COPY":
                shutil.copy2(file, resolved)
                copied += 1
                by_action["copied"] += 1
            else:
                file.rename(resolved)
                moved += 1
                by_action["moved"] += 1

            by_rule[rule_name] += 1
            logger.debug(f"{action_name} {chosen} | {file} -> {resolved}")
        except Exception as e:
            errors += 1
            by_action["errors"] += 1
            logger.exception(f"ERROR moving {file} -> {resolved}: {e}")

    logger.info("==== SUMMARY ====")
    logger.info(f"Dry-run: {dry_run}")
    logger.info(f"Files scanned: {total_files}")

    if dry_run:
        logger.info(f"Planned moves: {by_action.get('planned_moves', 0)}")
        logger.info(f"Planned copies: {by_action.get('planned_copies', 0)}")
    else:
        logger.info(f"Moved: {moved}")
        logger.info(f"Copied: {copied}")
        logger.info(f"Overwritten: {overwritten}")
        logger.info(f"Skipped: {skipped}")
        logger.info(f"Errors: {errors}")
        logger.info(f"Trashed: {by_action.get('trashed', 0)}")

    logger.info("By rule:")
    for rule_name, count in by_rule.most_common():
        logger.info(f"  - {rule_name}: {count}")
