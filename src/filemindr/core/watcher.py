from __future__ import annotations

import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue, Empty
from threading import Event, Thread

from loguru import logger
from watchfiles import watch

from filemindr.core.runner import run_pipeline


DEFAULT_IGNORED_SUFFIXES = {
    ".crdownload",
    ".part",
    ".aria2",
    ".tmp",
    ".temp",
}

DEFAULT_IGNORED_PREFIXES = {"~$"}


@dataclass(frozen=True)
class WatchOptions:
    debounce_ms: int = 800
    stable_ms: int = 1500
    poll_interval_ms: int = 250

    ignore_suffixes: set[str] = field(default_factory=lambda: set(DEFAULT_IGNORED_SUFFIXES))
    ignore_prefixes: set[str] = field(default_factory=lambda: set(DEFAULT_IGNORED_PREFIXES))

    max_pending_paths: int = 10_000
    log_pending: bool = False

    main_loop_tick_ms: int = 250


def _should_ignore(path: Path, opts: WatchOptions) -> bool:
    name = path.name.lower()
    if path.name.lower() == "filemindr.yaml":
        return True
    for p in opts.ignore_prefixes:
        if name.startswith(p.lower()):
            return True
    for s in opts.ignore_suffixes:
        if name.endswith(s.lower()):
            return True
    return False


def _is_stable(path: Path, stable_ms: int, poll_interval_ms: int) -> bool:
    if not path.exists() or not path.is_file():
        return False

    stable_s = stable_ms / 1000
    poll_s = max(0.05, poll_interval_ms / 1000)

    start = time.monotonic()
    try:
        st0 = path.stat()
        prev_size = st0.st_size
        prev_mtime = st0.st_mtime
    except OSError:
        return False

    while True:
        time.sleep(poll_s)

        if not path.exists():
            return False

        try:
            st = path.stat()
            size = st.st_size
            mtime = st.st_mtime
        except OSError:
            return False

        if size != prev_size or mtime != prev_mtime:
            prev_size, prev_mtime = size, mtime
            start = time.monotonic()

        if (time.monotonic() - start) >= stable_s:
            return True


def watch_and_run(
    source_dir: Path,
    config_path: str,
    dry_run: bool,
    opts: WatchOptions | None = None,
    *,
    once: bool = False,
) -> None:
    opts = opts or WatchOptions()
    source_dir = source_dir.expanduser().resolve()

    logger.info(f"Watching: {source_dir}")
    logger.info(f"Config: {config_path} | dry_run={dry_run}")
    logger.info(f"debounce_ms={opts.debounce_ms} stable_ms={opts.stable_ms} once={once}")

    q: Queue[Path] = Queue()
    stop_event = Event()

    prev_handler = signal.getsignal(signal.SIGINT)

    def _sigint_handler(signum, frame):
        if not stop_event.is_set():
            logger.info("Ctrl+C received. Stopping watcher...")
            stop_event.set()

    signal.signal(signal.SIGINT, _sigint_handler)

    def producer() -> None:
        try:
            for changes in watch(source_dir, recursive=False, stop_event=stop_event):
                if stop_event.is_set():
                    break
                for _, changed_path in changes:
                    p = Path(changed_path)
                    if p.is_dir():
                        continue
                    if _should_ignore(p, opts):
                        continue
                    q.put(p)
        except Exception:
            logger.exception("Watcher producer crashed.")
            stop_event.set()

    producer_thread = Thread(target=producer, daemon=False)
    producer_thread.start()

    pending: set[Path] = set()

    def flush_pending() -> None:
        nonlocal pending
        if not pending:
            return

        if opts.log_pending:
            logger.debug(
                f"Pending paths ({len(pending)}): " + ", ".join(str(p) for p in list(pending)[:50])
            )

        ready = {
            p for p in pending
            if not _should_ignore(p, opts)
            and _is_stable(p, opts.stable_ms, opts.poll_interval_ms)
        }

        if ready:
            logger.info(f"Detected stable changes ({len(ready)} paths). Running pipeline...")
            try:
                run_pipeline(config_path, dry_run=dry_run, only_paths=ready)
            except KeyboardInterrupt:
                stop_event.set()
                logger.info("Pipeline interrupted by user.")
            except Exception:
                logger.exception("Pipeline run failed.")
            finally:
                if once and not stop_event.is_set():
                    logger.info("watch --once completed. Exiting watcher.")
                    stop_event.set()

        pending.clear()

    quiet_s = opts.debounce_ms / 1000
    tick_s = max(0.05, opts.main_loop_tick_ms / 1000)

    try:
        while not stop_event.is_set():
            try:
                p = q.get(timeout=tick_s)
            except Empty:
                continue

            pending.add(p)

            if len(pending) > opts.max_pending_paths:
                logger.warning(f"Too many pending paths ({len(pending)}). Flushing now...")
                flush_pending()
                continue

            deadline = time.monotonic() + quiet_s
            while not stop_event.is_set():
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    break
                try:
                    p2 = q.get(timeout=min(timeout, tick_s))
                except Empty:
                    continue
                else:
                    pending.add(p2)
                    deadline = time.monotonic() + quiet_s

            if stop_event.is_set():
                break

            flush_pending()

    except KeyboardInterrupt:
        stop_event.set()
        logger.info("Watcher stopped by user (KeyboardInterrupt).")
    finally:
        if pending and not stop_event.is_set():
            flush_pending()

        stop_event.set()
        producer_thread.join(timeout=2.0)

        signal.signal(signal.SIGINT, prev_handler)

        logger.info("Watcher shutdown complete.")
