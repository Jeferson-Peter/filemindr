from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from threading import Event
from typing import Any

from loguru import logger

from filemindr.core.history import HistoryWriter, prune_history, retention_days_default
from filemindr.core.operations import (
    INTERNAL_PREFIX,
    OperationPlan,
    PartialOperationError,
    execute_operation,
)
from filemindr.core.planning import (
    DestinationReservations,
    ExecutionPlanner,
    Rule,
    is_older_than as _is_older_than,
    load_rules as _load_rules,
    matching_rules,
    normalize_ext as _normalize_ext,
    resolve_conflict,
    resolve_destination as _resolve_destination,
)

__all__ = [
    "_is_older_than",
    "_load_rules",
    "_normalize_ext",
    "_resolve_destination",
    "_resolve_conflict",
    "_match_rule",
    "Rule",
    "run_pipeline",
]


def _match_rule(file: Path, rules: list[Rule]) -> Rule | None:
    matched = matching_rules(file, rules)
    return matched[0] if matched else None


def _resolve_conflict(
    dest: Path, policy: str, *, reserved: set[Path] | None = None
) -> Path | None:
    reservations = DestinationReservations()
    for path in reserved or ():
        reservations.reserve(path)
    return resolve_conflict(dest, policy, reservations)


def _trash(path: Path) -> None:
    try:
        from send2trash import send2trash

        send2trash(str(path))
    except Exception as exc:
        raise RuntimeError(f"Failed to send '{path}' to trash") from exc


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
    started = time.perf_counter()
    planner = ExecutionPlanner(Path(config_path))
    cfg_abs = planner.config_path
    source = planner.source
    if report_path is not None and (report_path.exists() or report_path.is_symlink()):
        raise FileExistsError(f"Report path already exists: {report_path}")

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
    rule_matches: Counter[str] = Counter()
    skipped_by_reason: Counter[str] = Counter()
    errors_by_category: Counter[str] = Counter()
    operations_by_type: Counter[str] = Counter()
    planned: Counter[str] = Counter()
    completed: Counter[str] = Counter()
    per_rule: dict[str, Counter[str]] = defaultdict(Counter)
    events: list[dict[str, Any]] = []

    def metrics() -> dict[str, Any]:
        elapsed = max(time.perf_counter() - started, 1e-9)
        return {
            "duration_seconds": round(elapsed, 6),
            "files_per_second": round(summary["files_scanned"] / elapsed, 2),
            "rule_matches": dict(sorted(rule_matches.items())),
            "skipped_by_reason": dict(sorted(skipped_by_reason.items())),
            "errors_by_category": dict(sorted(errors_by_category.items())),
            "operations_by_type": dict(sorted(operations_by_type.items())),
            "planned": dict(sorted(planned.items())),
            "completed": dict(sorted(completed.items())),
            "per_rule": {
                name: dict(sorted(values.items()))
                for name, values in sorted(per_rule.items())
            },
        }

    def record(event: dict[str, Any]) -> None:
        if history:
            history.record(event)
        if report_path is not None:
            events.append(event)

    def candidates():
        items = sorted(only_paths) if only_paths is not None else source.iterdir()
        for file in items:
            if file.parent != source:
                continue
            if file.is_symlink():
                skipped_by_reason["unsupported_symlink"] += 1
                continue
            if not file.is_file():
                skipped_by_reason["not_regular_file"] += 1
                continue
            if file.name.startswith(INTERNAL_PREFIX) or file == cfg_abs:
                skipped_by_reason["internal_file"] += 1
                continue
            if report_path is not None and file == report_path.absolute():
                skipped_by_reason["report_file"] += 1
                continue
            yield file

    try:
        logger.info(f"Scanning: {source}")
        for item in planner.plan_files(
            candidates(), capture_destination_identity=not dry_run
        ):
            if stop_event is not None and stop_event.is_set():
                break
            if item.reason == "ignored_by_pattern":
                skipped_by_reason["ignored_by_pattern"] += 1
                continue

            summary["files_scanned"] += 1
            for name, _priority in item.matched_rules:
                rule_matches[name] += 1
                per_rule[name]["matched"] += 1
            rule_name = item.rule_name
            policy = item.policy
            event: dict[str, Any] = {
                "source": str(item.source),
                "rule": rule_name,
                "policy": policy,
            }
            if item.requested_destination is not None:
                event["destination"] = str(
                    item.destination or item.requested_destination
                )

            if item.status == "error":
                summary["errors"] += 1
                errors_by_category["PlanningError"] += 1
                per_rule[rule_name]["errors"] += 1
                record(event | {"event": "error", "error": item.error})
                logger.error(f"ERROR planning {item.source}: {item.error}")
                continue

            if item.status == "skipped":
                reason = item.reason or "skipped"
                skipped_by_reason[reason] += 1
                if reason == "conflict_policy_skip":
                    summary["skipped"] += 1
                    by_rule[rule_name] += 1
                    per_rule[rule_name]["skipped"] += 1
                    record(event | {"event": "skipped", "reason": reason})
                continue

            assert item.destination is not None and item.source_snapshot is not None
            action_key = "copies" if item.action == "COPY" else "moves"
            planned[action_key] += 1
            per_rule[rule_name][f"planned_{action_key}"] += 1
            if dry_run:
                summary[
                    "planned_copies" if action_key == "copies" else "planned_moves"
                ] += 1
                by_rule[rule_name] += 1
                record(
                    event
                    | {
                        "event": "planned_copy"
                        if action_key == "copies"
                        else "planned_move",
                        "action": item.action,
                    }
                )
                logger.debug(f"[DRY] {item.action} {item.source} -> {item.destination}")
                continue

            if history:
                record(
                    event
                    | {
                        "event": "operation_started",
                        "action": item.action,
                        "undo_supported": False,
                    }
                )
            try:
                result = execute_operation(
                    OperationPlan(
                        item.source,
                        item.destination,
                        item.action,
                        policy,
                        item.expected_destination,
                    ),
                    trash=_trash,
                    expected_source_snapshot=item.source_snapshot,
                )
                if result.replaced:
                    replacement = "trashed" if policy == "trash" else "overwritten"
                    summary[replacement] += 1
                    operations_by_type[replacement] += 1
                    record(
                        event
                        | {
                            "event": replacement,
                            "path": str(item.destination),
                            "undo_supported": False,
                        }
                    )
                completed[action_key] += 1
                operations_by_type["copy" if action_key == "copies" else "move"] += 1
                per_rule[rule_name][f"completed_{action_key}"] += 1
                result_key = "copied" if item.action == "COPY" else "moved"
                summary[result_key] += 1
                record(
                    event
                    | {
                        "event": result_key,
                        "identity": result.identity,
                        "undo_supported": item.action == "MOVE"
                        and not result.replaced
                        and result.identity["inode"] != 0,
                    }
                )
                logger.debug(f"{item.action} {item.source} -> {item.destination}")
                by_rule[rule_name] += 1
            except Exception as exc:
                summary["errors"] += 1
                category = type(exc).__name__
                errors_by_category[category] += 1
                per_rule[rule_name]["errors"] += 1
                failure = event | {
                    "event": "error",
                    "error": str(exc),
                    "error_category": category,
                    "undo_supported": False,
                }
                if isinstance(exc, PartialOperationError):
                    failure["recovery_path"] = str(exc.recovery_path)
                record(failure)
                logger.error(f"ERROR processing {item.source}: {exc}")

        final_metrics = metrics()
        if history:
            if stop_event is not None and stop_event.is_set():
                history.fail(
                    "Interrupted between file operations", metrics=final_metrics
                )
            elif summary["errors"]:
                history.fail(
                    f"{summary['errors']} file operation(s) failed",
                    metrics=final_metrics,
                )
            else:
                history.complete(metrics=final_metrics)
    except BaseException as exc:
        if history:
            history.fail(str(exc) or type(exc).__name__, metrics=metrics())
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
    logger.info(f"Files per second: {final_metrics['files_per_second']}")
    if report_path is not None:
        payload = dict(
            profile=profile,
            command=command or "run_pipeline",
            config_path=str(cfg_abs),
            source=str(source),
            dry_run=dry_run,
            summary=summary,
            by_rule=dict(by_rule),
            metrics=final_metrics,
            events=events,
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, indent=2))
    return summary
