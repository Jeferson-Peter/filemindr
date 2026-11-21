"""Bounded handoff microbenchmark; excludes native event delivery and file work."""

from __future__ import annotations

import json
import time
import tracemalloc
from pathlib import Path
from threading import Event, Thread

from filemindr.core.watcher import PendingPaths


def benchmark(count: int, capacity: int = 1000) -> dict:
    handoff = PendingPaths(capacity)
    stop = Event()
    done = Event()
    peak_pending = 0
    consumed = 0

    def produce():
        nonlocal peak_pending
        try:
            for i in range(count):
                handoff.put(Path(f"file-{i}.txt"), stop)
                with handoff.condition:
                    peak_pending = max(peak_pending, len(handoff.paths))
        finally:
            done.set()

    tracemalloc.start()
    start = time.perf_counter()
    thread = Thread(target=produce)
    thread.start()
    while not done.is_set() or handoff.paths:
        with handoff.condition:
            consumed += len(handoff.paths)
            handoff.paths.clear()
            handoff.condition.notify_all()
        stop.wait(0.001)
    thread.join()
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert consumed == count
    assert peak_pending <= capacity
    return dict(
        events=count,
        seconds=round(elapsed, 6),
        peak_pending=peak_pending,
        capacity=capacity,
        peak_python_mib=round(peak / 2**20, 3),
    )


if __name__ == "__main__":
    results = [benchmark(count) for count in (100, 10_000, 100_000)]
    output = Path("docs/watch-buffer-results.json")
    output.write_text(json.dumps(results, indent=2))
    print(output.read_text())
