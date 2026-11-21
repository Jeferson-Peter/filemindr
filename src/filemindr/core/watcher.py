from __future__ import annotations

import signal
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from threading import Condition, Event, Thread, current_thread, main_thread

from loguru import logger
from watchfiles import Change, watch

from filemindr.core.ignore import matches_ignore_pattern
from filemindr.core.operations import INTERNAL_PREFIX, snapshot
from filemindr.core.runner import run_pipeline

DEFAULT_IGNORED_SUFFIXES = {".crdownload", ".part", ".aria2", ".tmp", ".temp"}
DEFAULT_IGNORED_PREFIXES = {"~$", INTERNAL_PREFIX}


@dataclass(frozen=True)
class WatchOptions:
    debounce_ms: int = 800
    stable_ms: int = 1500
    poll_interval_ms: int = 250
    ignore_suffixes: set[str] = field(
        default_factory=lambda: set(DEFAULT_IGNORED_SUFFIXES)
    )
    ignore_prefixes: set[str] = field(
        default_factory=lambda: set(DEFAULT_IGNORED_PREFIXES)
    )
    ignore_patterns: list[str] = field(default_factory=list)
    max_pending_paths: int = 10_000
    log_pending: bool = False
    main_loop_tick_ms: int = 250

    def __post_init__(self) -> None:
        if self.max_pending_paths <= 0:
            raise ValueError("max_pending_paths must be positive")
        if (
            min(self.debounce_ms, self.stable_ms) < 0
            or min(self.poll_interval_ms, self.main_loop_tick_ms) <= 0
        ):
            raise ValueError(
                "Watch intervals must be positive (debounce/stability may be zero)"
            )


def _should_ignore(path: Path, opts: WatchOptions) -> bool:
    name = path.name.lower()
    return (
        name == "filemindr.yaml"
        or name.startswith(INTERNAL_PREFIX)
        or matches_ignore_pattern(path, opts.ignore_patterns)
        or any(name.startswith(prefix.lower()) for prefix in opts.ignore_prefixes)
        or any(name.endswith(suffix.lower()) for suffix in opts.ignore_suffixes)
    )


def _signature(path: Path) -> tuple[int, ...] | None:
    try:
        return snapshot(path)
    except (OSError, ValueError):
        return None


def _is_stable(
    path: Path, previous: tuple[int, ...] | None, since: float, stable_ms: int
) -> bool:
    return (
        previous is not None
        and _signature(path) == previous
        and time.monotonic() - since >= stable_ms / 1000
    )


@dataclass
class _Pending:
    last_event: float
    signature: tuple[int, ...] | None = None
    since: float = 0


class PendingPaths:
    """A bounded, deduplicated handoff. Full buffers backpressure the producer."""

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.paths: dict[Path, _Pending] = {}
        self.condition = Condition()

    def put(self, path: Path, stop: Event) -> None:
        with self.condition:
            while path not in self.paths and len(self.paths) >= self.capacity:
                if stop.is_set():
                    return
                self.condition.wait(0.05)
            if not stop.is_set():
                state = self.paths.setdefault(path, _Pending(time.monotonic()))
                state.last_event = time.monotonic()


def watch_and_run(
    source_dir: Path,
    config_path: str,
    dry_run: bool,
    opts: WatchOptions | None = None,
    *,
    once: bool = False,
    profile: str | None = None,
) -> None:
    opts = opts or WatchOptions()
    source_dir = source_dir.expanduser().resolve()
    config_file = Path(config_path).expanduser().resolve()
    if not source_dir.is_dir():
        raise NotADirectoryError(source_dir)
    pending = PendingPaths(opts.max_pending_paths)
    stop = Event()
    done = Event()
    producer_errors: list[Exception] = []
    batch_failed = False
    attempted: OrderedDict[Path, tuple[int, ...]] = OrderedDict()
    previous_handlers = {}

    def shutdown(signum, frame) -> None:
        stop.set()

    if current_thread() is main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[sig] = signal.signal(sig, shutdown)

    def producer() -> None:
        try:
            for changes in watch(
                source_dir, recursive=False, stop_event=stop, rust_timeout=250
            ):
                if stop.is_set():
                    break
                for kind, value in changes:
                    path = Path(value).absolute()
                    if (
                        kind == Change.deleted
                        or path == config_file
                        or path.parent != source_dir
                    ):
                        continue
                    if _should_ignore(path, opts) or _signature(path) is None:
                        continue
                    pending.put(path, stop)
        except Exception as exc:
            producer_errors.append(exc)
            stop.set()
        finally:
            done.set()

    thread = Thread(target=producer, name="filemindr-watch", daemon=False)
    logger.info(f"Watching: {source_dir} | pending limit={opts.max_pending_paths}")
    tick = max(0.005, min(opts.main_loop_tick_ms, opts.poll_interval_ms) / 1000)
    try:
        thread.start()
        while not stop.is_set():
            ready: set[Path] = set()
            signatures: dict[Path, tuple[int, ...]] = {}
            now = time.monotonic()
            with pending.condition:
                for path, state in list(pending.paths.items()):
                    signature = _signature(path)
                    if signature is None or attempted.get(path) == signature:
                        del pending.paths[path]
                        continue
                    if signature != state.signature:
                        state.signature = signature
                        state.since = now
                    if now - state.last_event < opts.debounce_ms / 1000:
                        continue
                    if _is_stable(path, state.signature, state.since, opts.stable_ms):
                        ready.add(path)
                        signatures[path] = signature
                        del pending.paths[path]
                pending.condition.notify_all()
                empty = not pending.paths
            if ready:
                try:
                    kwargs = dict(dry_run=dry_run, only_paths=ready, stop_event=stop)
                    if profile:
                        kwargs.update(profile=profile, command="watch")
                    summary = run_pipeline(config_path, **kwargs)
                    if summary and summary["errors"]:
                        batch_failed = True
                        logger.error(
                            f"Watch batch had {summary['errors']} operation error(s)"
                        )
                except Exception:
                    batch_failed = True
                    logger.exception("Pipeline run failed.")
                # Recovery can recreate a source and emit events without changing its data.
                for path, signature in signatures.items():
                    attempted[path] = signature
                    attempted.move_to_end(path)
                    while len(attempted) > opts.max_pending_paths:
                        attempted.popitem(last=False)
                if once:
                    stop.set()
            elif done.is_set() and empty:
                break
            if opts.log_pending:
                logger.debug(f"Pending paths: {len(pending.paths)}")
            stop.wait(tick)
    except KeyboardInterrupt:
        stop.set()
    finally:
        stop.set()
        with pending.condition:
            pending.condition.notify_all()
        if thread.ident is not None:
            thread.join(timeout=2)
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        if thread.is_alive():
            logger.error("Watcher producer did not stop within two seconds")
        logger.info("Watcher shutdown complete.")
    if producer_errors:
        raise RuntimeError("Watcher producer failed") from producer_errors[0]
    if once and batch_failed:
        raise RuntimeError("Watch batch failed; inspect the pipeline errors")
