from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from filemindr.core.config import expand_path


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
        self._write_meta(status="running")

    def _write_meta(self, *, status: str, finished_at: str | None = None, error: str | None = None) -> None:
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
        self.run.meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def record(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("event", "unknown"))
        self._counts[event_type] = self._counts.get(event_type, 0) + 1

        payload = {
            "timestamp": _utc_now().isoformat(),
            **event,
        }
        with self.run.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=True) + "\n")
        self._write_meta(status="running")

    def complete(self) -> None:
        self._write_meta(status="completed", finished_at=_utc_now().isoformat())

    def fail(self, error: str) -> None:
        self._write_meta(status="failed", finished_at=_utc_now().isoformat(), error=error)


def load_run_meta(meta_path: Path) -> dict[str, Any]:
    return json.loads(meta_path.read_text(encoding="utf-8"))


def iter_run_meta() -> list[dict[str, Any]]:
    root = runs_root()
    if not root.exists():
        return []

    items: list[dict[str, Any]] = []
    for meta_path in sorted(root.glob("*/*/meta.json"), reverse=True):
        try:
            item = load_run_meta(meta_path)
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
    if not events_path.exists():
        return []

    events: list[dict[str, Any]] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        events.append(json.loads(line))
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
        if not day_dir.is_dir():
            continue
        try:
            day = datetime.strptime(day_dir.name, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            continue

        if day >= cutoff.replace(hour=0, minute=0, second=0, microsecond=0):
            continue

        for run_dir in day_dir.iterdir():
            if run_dir.is_dir():
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
        if not day_dir.is_dir():
            continue
        for run_dir in list(day_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            for child in list(run_dir.iterdir()):
                if child.is_file():
                    child.unlink()
            run_dir.rmdir()
            removed += 1
        if not any(day_dir.iterdir()):
            day_dir.rmdir()

    return removed


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

            if not current_path.exists():
                skipped += 1
                history.record(
                    {
                        "event": "undo_skipped",
                        "original_run_id": run_id,
                        "reason": "missing_current_path",
                        "source": str(current_path),
                        "destination": str(original_path),
                    }
                )
                continue

            if original_path.exists():
                skipped += 1
                history.record(
                    {
                        "event": "undo_skipped",
                        "original_run_id": run_id,
                        "reason": "original_path_already_exists",
                        "source": str(current_path),
                        "destination": str(original_path),
                    }
                )
                continue

            try:
                original_path.parent.mkdir(parents=True, exist_ok=True)
                current_path.rename(original_path)
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
                    }
                )

        history.complete()
    except Exception as exc:
        history.fail(str(exc))
        raise

    return UndoResult(
        run_id=run_id,
        undo_run_id=history.run.run_id,
        reverted=reverted,
        skipped=skipped,
        errors=errors,
    )
