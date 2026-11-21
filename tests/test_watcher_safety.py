from __future__ import annotations

import signal
import time
from pathlib import Path
from threading import Event, Thread

import pytest

from filemindr.core import watcher


def test_bounded_buffer_deduplicates_and_backpressures():
    buffer = watcher.PendingPaths(2)
    stop = Event()
    buffer.put(Path("a"), stop)
    for _ in range(1000):
        buffer.put(Path("a"), stop)
    buffer.put(Path("b"), stop)
    entered = Event()

    def producer():
        entered.set()
        buffer.put(Path("c"), stop)

    thread = Thread(target=producer)
    thread.start()
    assert entered.wait(1)
    try:
        assert len(buffer.paths) == 2
        assert thread.is_alive()
    finally:
        stop.set()
        with buffer.condition:
            buffer.condition.notify_all()
        thread.join(1)
    assert not thread.is_alive()
    assert len(buffer.paths) == 2


def test_duplicate_burst_runs_each_path_once_without_exceeding_capacity(
    tmp_path, monkeypatch
):
    items = []
    for i in range(30):
        path = tmp_path / f"{i}.txt"
        path.write_text(str(i))
        items.append(path)
    seen = []

    def fake_watch(*args, **kwargs):
        yield [(1, str(path)) for path in items for _ in range(4)]

    def pipeline(*args, only_paths, **kwargs):
        seen.extend(only_paths)
        time.sleep(0.002)  # Ingestion must remain bounded while work executes.

    monkeypatch.setattr(watcher, "watch", fake_watch)
    monkeypatch.setattr(watcher, "run_pipeline", pipeline)
    watcher.watch_and_run(
        tmp_path,
        str(tmp_path / "rules.yaml"),
        False,
        watcher.WatchOptions(
            max_pending_paths=3,
            debounce_ms=0,
            stable_ms=0,
            poll_interval_ms=1,
            main_loop_tick_ms=1,
        ),
    )
    assert set(seen) == set(items)
    assert len(seen) == len(items)


def test_stability_waits_for_last_change_without_needing_another_event(
    tmp_path, monkeypatch
):
    item = tmp_path / "writing.txt"
    item.write_text("start")
    finished = Event()

    def fake_watch(*args, **kwargs):
        yield [(1, str(item))]
        for n in range(5):
            item.write_text(str(n))
            time.sleep(0.005)
        finished.set()

    calls = []

    def pipeline(*args, only_paths, **kwargs):
        assert finished.is_set()
        calls.append(only_paths)

    monkeypatch.setattr(watcher, "watch", fake_watch)
    monkeypatch.setattr(watcher, "run_pipeline", pipeline)
    watcher.watch_and_run(
        tmp_path,
        str(tmp_path / "rules.yaml"),
        False,
        watcher.WatchOptions(
            debounce_ms=0, stable_ms=30, poll_interval_ms=1, main_loop_tick_ms=1
        ),
        once=True,
    )
    assert calls == [{item}]


def test_deleted_files_and_internal_files_never_reach_pipeline(tmp_path, monkeypatch):
    deleted = tmp_path / "deleted.txt"
    internal = tmp_path / ".filemindr-stage.txt"
    internal.write_text("private")

    def fake_watch(*args, **kwargs):
        yield [(3, str(deleted)), (1, str(internal))]

    monkeypatch.setattr(watcher, "watch", fake_watch)
    monkeypatch.setattr(
        watcher, "run_pipeline", lambda *a, **kw: pytest.fail("no eligible file")
    )
    watcher.watch_and_run(tmp_path, str(tmp_path / "rules.yaml"), False)


def test_shutdown_during_unstable_writes_restores_signal_handlers(
    tmp_path, monkeypatch
):
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    item = tmp_path / "busy.txt"
    item.write_text("busy")
    stopped = Event()

    def fake_watch(*args, stop_event, **kwargs):
        yield [(1, str(item))]
        time.sleep(0.02)
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        assert stop_event.wait(1)
        stopped.set()

    monkeypatch.setattr(watcher, "watch", fake_watch)
    monkeypatch.setattr(
        watcher, "run_pipeline", lambda *a, **kw: pytest.fail("unstable file")
    )
    start = time.monotonic()
    watcher.watch_and_run(
        tmp_path,
        str(tmp_path / "rules.yaml"),
        False,
        watcher.WatchOptions(stable_ms=60_000),
    )
    assert time.monotonic() - start < 1
    assert stopped.is_set()
    assert all(signal.getsignal(sig) == previous for sig, previous in before.items())


def test_producer_error_is_propagated(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise PermissionError("cannot watch")

    monkeypatch.setattr(watcher, "watch", fail)
    with pytest.raises(RuntimeError, match="producer failed"):
        watcher.watch_and_run(tmp_path, str(tmp_path / "rules.yaml"), False)


def test_once_batch_errors_fail_the_command(tmp_path, monkeypatch):
    item = tmp_path / "input.txt"
    item.write_text("data")

    def events(*args, **kwargs):
        yield [(1, str(item))]

    monkeypatch.setattr(watcher, "watch", events)
    monkeypatch.setattr(watcher, "run_pipeline", lambda *a, **kw: {"errors": 1})
    with pytest.raises(RuntimeError, match="Watch batch failed"):
        watcher.watch_and_run(
            tmp_path,
            str(tmp_path / "rules.yaml"),
            False,
            watcher.WatchOptions(stable_ms=0, debounce_ms=0),
            once=True,
        )


@pytest.mark.parametrize(
    "values", [{"max_pending_paths": 0}, {"stable_ms": -1}, {"poll_interval_ms": 0}]
)
def test_invalid_watch_options_fail_early(values):
    with pytest.raises(ValueError):
        watcher.WatchOptions(**values)
