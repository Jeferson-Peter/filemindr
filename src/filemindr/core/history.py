from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from filemindr.core.config import expand_path
from filemindr.core.operations import (
    OperationPlan,
    PartialOperationError,
    execute_operation,
    file_identity,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def history_root() -> Path:
    return expand_path("~/.filemindr/history")


def runs_root() -> Path:
    return history_root() / "runs"


def retention_days_default() -> int:
    return 7


@dataclass(frozen=True)
class HistoryRun:
    run_id: str
    profile: str | None
    command: str | None
    config_path: str
    source: str
    dry_run: bool
    started_at: str
    events_path: Path
    meta_path: Path


class HistoryWriter:
    def __init__(
        self,
        *,
        profile: str | None,
        command: str | None,
        config_path: Path,
        source: Path,
        dry_run: bool,
    ) -> None:
        run_id = uuid.uuid4().hex[:12]
        started = _utc_now()
        run_dir = runs_root() / started.strftime("%Y-%m-%d") / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        self.run = HistoryRun(
            run_id=run_id,
            profile=profile,
            command=command,
            config_path=str(config_path),
            source=str(source),
            dry_run=dry_run,
            started_at=started.isoformat(),
            events_path=run_dir / "events.jsonl",
            meta_path=run_dir / "meta.json",
        )
        self._counts: dict[str, int] = {}
        self._metrics: dict[str, Any] | None = None
        self._write_meta(status="running")

    def _write_meta(
        self, *, status: str, finished_at: str | None = None, error: str | None = None
    ) -> None:
        payload = {
            "run_id": self.run.run_id,
            "profile": self.run.profile,
            "command": self.run.command,
            "config_path": self.run.config_path,
            "source": self.run.source,
            "dry_run": self.run.dry_run,
            "started_at": self.run.started_at,
            "finished_at": finished_at,
            "status": status,
            "counts": self._counts,
            "error": error,
        }
        if self._metrics is not None:
            payload["metrics"] = self._metrics
        fd, temporary = tempfile.mkstemp(
            prefix=".meta-", suffix=".tmp", dir=self.run.meta_path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, indent=2))
                stream.flush()
            os.replace(temporary, self.run.meta_path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def record(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("event", "unknown"))
        self._counts[event_type] = self._counts.get(event_type, 0) + 1

        payload = {
            "timestamp": _utc_now().isoformat(),
            **event,
        }
        with self.run.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=True) + "\n")
        # JSONL is the live audit log. Rewrite aggregate metadata only on finish.

    def complete(self, *, metrics: dict[str, Any] | None = None) -> None:
        self._metrics = metrics
        self._write_meta(status="completed", finished_at=_utc_now().isoformat())

    def fail(self, error: str, *, metrics: dict[str, Any] | None = None) -> None:
        self._metrics = metrics
        self._write_meta(
            status="failed", finished_at=_utc_now().isoformat(), error=error
        )


def load_run_meta(meta_path: Path) -> dict[str, Any]:
    return json.loads(meta_path.read_text(encoding="utf-8"))


def iter_run_meta() -> list[dict[str, Any]]:
    root = runs_root()
    if not root.exists():
        return []

    items: list[dict[str, Any]] = []
    for meta_path in sorted(root.glob("*/*/meta.json"), reverse=True):
        if (
            meta_path.is_symlink()
            or not _real_directory(meta_path.parent)
            or not _real_directory(meta_path.parent.parent)
        ):
            continue
        try:
            item = load_run_meta(meta_path)
            if not isinstance(item, dict) or not isinstance(item.get("run_id"), str):
                continue
            item["_meta_path"] = str(meta_path)
            item["_events_path"] = str(meta_path.with_name("events.jsonl"))
            items.append(item)
        except Exception:
            continue
    items.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    return items


def find_run(run_id: str) -> dict[str, Any] | None:
    for item in iter_run_meta():
        if item.get("run_id") == run_id:
            return item
    return None


def load_run_events(run_id: str) -> list[dict[str, Any]]:
    item = find_run(run_id)
    if not item:
        raise FileNotFoundError(run_id)

    events_path = Path(item["_events_path"])
    if events_path.is_symlink():
        raise ValueError("Refusing symlinked history events")
    if not events_path.exists():
        return []

    events: list[dict[str, Any]] = []
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1 and not line.endswith("\n"):
                break  # A crash can leave the final append incomplete.
            raise
        if not isinstance(event, dict):
            raise ValueError("History events must be objects")
        events.append(event)
    return events


def prune_history(*, days: int) -> int:
    root = runs_root()
    if not root.exists():
        return 0

    if days <= 0:
        return clear_history()

    cutoff = _utc_now() - timedelta(days=days)
    removed = 0

    for day_dir in root.iterdir():
        if not _real_directory(day_dir):
            continue
        try:
            day = datetime.strptime(day_dir.name, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            continue

        if day >= cutoff.replace(hour=0, minute=0, second=0, microsecond=0):
            continue

        for run_dir in day_dir.iterdir():
            if _real_directory(run_dir):
                for child in run_dir.iterdir():
                    if child.is_file():
                        child.unlink()
                run_dir.rmdir()
                removed += 1
        if not any(day_dir.iterdir()):
            day_dir.rmdir()

    return removed


def clear_history() -> int:
    root = runs_root()
    if not root.exists():
        return 0

    removed = 0
    for day_dir in list(root.iterdir()):
        if not _real_directory(day_dir):
            continue
        for run_dir in list(day_dir.iterdir()):
            if not _real_directory(run_dir):
                continue
            for child in list(run_dir.iterdir()):
                if child.is_file():
                    child.unlink()
            run_dir.rmdir()
            removed += 1
        if not any(day_dir.iterdir()):
            day_dir.rmdir()

    return removed


def _real_directory(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink() and not path.is_junction()


@dataclass(frozen=True)
class UndoResult:
    run_id: str
    undo_run_id: str
    reverted: int
    skipped: int
    errors: int


def undo_run(run_id: str) -> UndoResult:
    item = find_run(run_id)
    if not item:
        raise FileNotFoundError(run_id)

    events = load_run_events(run_id)
    move_events = [event for event in events if event.get("event") == "moved"]
    already_reverted: set[tuple[str, str]] = set()
    for previous in iter_run_meta():
        if previous.get("command") != "undo":
            continue
        for event in load_run_events(previous["run_id"]):
            if (
                event.get("event") == "undo_moved"
                and event.get("original_run_id") == run_id
            ):
                already_reverted.add(
                    (str(event.get("destination")), str(event.get("source")))
                )

    history = HistoryWriter(
        profile=item.get("profile"),
        command="undo",
        config_path=Path(item.get("config_path") or "."),
        source=Path(item.get("source") or "."),
        dry_run=False,
    )

    reverted = 0
    skipped = 0
    errors = 0

    try:
        for event in reversed(move_events):
            current_path = Path(str(event["destination"]))
            original_path = Path(str(event["source"]))

            reason = None
            if (str(original_path), str(current_path)) in already_reverted:
                reason = "already_reverted"
            elif event.get("undo_supported") is not True or not isinstance(
                event.get("identity"), dict
            ):
                reason = "identity_unavailable_or_operation_not_reversible"
            elif not current_path.exists():
                reason = "missing_current_path"
            elif original_path.exists() or original_path.is_symlink():
                reason = "original_path_already_exists"
            else:
                try:
                    if file_identity(current_path) != event["identity"]:
                        reason = "file_identity_changed"
                except (OSError, ValueError):
                    reason = "file_identity_unverifiable"

            if reason:
                skipped += 1
                history.record(
                    {
                        "event": "undo_skipped",
                        "original_run_id": run_id,
                        "reason": reason,
                        "source": str(current_path),
                        "destination": str(original_path),
                    }
                )
                continue

            try:
                execute_operation(
                    OperationPlan(current_path, original_path, "MOVE", "skip", None),
                    expected_source=event["identity"],
                )
                reverted += 1
                history.record(
                    {
                        "event": "undo_moved",
                        "original_run_id": run_id,
                        "source": str(current_path),
                        "destination": str(original_path),
                    }
                )
            except Exception as exc:
                errors += 1
                history.record(
                    {
                        "event": "undo_error",
                        "original_run_id": run_id,
                        "source": str(current_path),
                        "destination": str(original_path),
                        "error": str(exc),
                        "recovery_path": str(exc.recovery_path)
                        if isinstance(exc, PartialOperationError)
                        else None,
                    }
                )

        if errors:
            history.fail(f"{errors} undo operation(s) failed")
        else:
            history.complete()
    except BaseException as exc:
        history.fail(str(exc))
        raise

    return UndoResult(
        run_id=run_id,
        undo_run_id=history.run.run_id,
        reverted=reverted,
        skipped=skipped,
        errors=errors,
    )
