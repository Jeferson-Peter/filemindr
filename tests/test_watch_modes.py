from __future__ import annotations

import time
from pathlib import Path


def test_watch_normal_runs_twice_then_stops(monkeypatch, tmp_path: Path):
    """
    Normal watch (once=False): runs pipeline for each burst (debounce window).
    We simulate 2 bursts separated by > debounce, then stop.
    """
    import filemindr.core.watcher as watcher

    source_dir = tmp_path / "Downloads"
    source_dir.mkdir()

    cfg_path = tmp_path / "filemindr.yaml"
    cfg_path.write_text("source: .\n", encoding="utf-8")

    f1 = source_dir / "a.jpg"
    f2 = source_dir / "b.jpg"
    f1.write_text("x", encoding="utf-8")
    f2.write_text("y", encoding="utf-8")

    monkeypatch.setattr(watcher, "_is_stable", lambda *args, **kwargs: True)
    monkeypatch.setattr(watcher, "_should_ignore", lambda *args, **kwargs: False)

    calls: list[set[Path] | None] = []

    def fake_run_pipeline(config_path: str, dry_run: bool = False, only_paths=None):
        calls.append(only_paths)

    monkeypatch.setattr(watcher, "run_pipeline", fake_run_pipeline)

    opts = watcher.WatchOptions(debounce_ms=10, stable_ms=10, poll_interval_ms=10)

    def fake_watch(_dir, recursive=False, stop_event=None):
        yield [(1, str(f1))]
        time.sleep(0.05)

        yield [(1, str(f2))]
        time.sleep(0.05)

        if stop_event is not None:
            stop_event.set()

    monkeypatch.setattr(watcher, "watch", fake_watch)

    watcher.watch_and_run(
        source_dir=source_dir,
        config_path=str(cfg_path),
        dry_run=False,
        opts=opts,
        once=False,
    )

    assert len(calls) == 2
    assert calls[0] is not None and f1 in calls[0]
    assert calls[1] is not None and f2 in calls[1]


def test_watch_once_runs_once_and_exits(monkeypatch, tmp_path: Path):
    """
    Watch once (once=True): should run pipeline only once and exit
    after the first stable burst.
    """
    import filemindr.core.watcher as watcher

    source_dir = tmp_path / "Downloads"
    source_dir.mkdir()

    cfg_path = tmp_path / "filemindr.yaml"
    cfg_path.write_text("source: .\n", encoding="utf-8")

    f1 = source_dir / "pic.jpg"
    f1.write_text("x", encoding="utf-8")

    monkeypatch.setattr(watcher, "_is_stable", lambda *args, **kwargs: True)
    monkeypatch.setattr(watcher, "_should_ignore", lambda *args, **kwargs: False)

    calls: list[set[Path] | None] = []

    def fake_run_pipeline(config_path: str, dry_run: bool = False, only_paths=None):
        calls.append(only_paths)

    monkeypatch.setattr(watcher, "run_pipeline", fake_run_pipeline)

    opts = watcher.WatchOptions(debounce_ms=10, stable_ms=10, poll_interval_ms=10)

    def fake_watch(_dir, recursive=False, stop_event=None):
        yield [(1, str(f1))]
        yield [(1, str(f1))]

    monkeypatch.setattr(watcher, "watch", fake_watch)

    watcher.watch_and_run(
        source_dir=source_dir,
        config_path=str(cfg_path),
        dry_run=False,
        opts=opts,
        once=True,
    )

    assert len(calls) == 1
    assert calls[0] is not None and f1 in calls[0]


def test_watch_keeps_unstable_files_pending_until_they_stabilize(monkeypatch, tmp_path: Path):
    """
    If a file is not yet stable during one flush, it should remain pending
    and be processed in a later burst once stability checks pass.
    """
    import filemindr.core.watcher as watcher

    source_dir = tmp_path / "Downloads"
    source_dir.mkdir()

    cfg_path = tmp_path / "filemindr.yaml"
    cfg_path.write_text("source: .\n", encoding="utf-8")

    f1 = source_dir / "large.zip"
    f1.write_text("x", encoding="utf-8")

    stability_checks = iter([False, True])

    def fake_is_stable(*args, **kwargs):
        return next(stability_checks)

    monkeypatch.setattr(watcher, "_is_stable", fake_is_stable)
    monkeypatch.setattr(watcher, "_should_ignore", lambda *args, **kwargs: False)

    calls: list[set[Path] | None] = []

    def fake_run_pipeline(config_path: str, dry_run: bool = False, only_paths=None):
        calls.append(only_paths)

    monkeypatch.setattr(watcher, "run_pipeline", fake_run_pipeline)

    opts = watcher.WatchOptions(debounce_ms=10, stable_ms=10, poll_interval_ms=10)

    def fake_watch(_dir, recursive=False, stop_event=None):
        yield [(1, str(f1))]
        time.sleep(0.05)
        yield [(1, str(f1))]
        time.sleep(0.05)

        if stop_event is not None:
            stop_event.set()

    monkeypatch.setattr(watcher, "watch", fake_watch)

    watcher.watch_and_run(
        source_dir=source_dir,
        config_path=str(cfg_path),
        dry_run=False,
        opts=opts,
        once=False,
    )

    assert len(calls) == 1
    assert calls[0] is not None and f1 in calls[0]
