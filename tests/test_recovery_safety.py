from __future__ import annotations

import json
import os
import errno
import subprocess
from pathlib import Path

import pytest
import yaml

from filemindr.core import history
from filemindr.core import operations
from filemindr.core.runner import run_pipeline


@pytest.fixture
def moved(tmp_path, fake_home):
    source = tmp_path / "source"
    source.mkdir()
    original = source / "a.txt"
    original.write_text("original data")
    target = tmp_path / "target"
    cfg = tmp_path / "rules.yaml"
    cfg.write_text(
        yaml.safe_dump({"source": str(source), "default_target": str(target)})
    )
    summary = run_pipeline(str(cfg), profile="test", command="run")
    assert summary["moved"] == 1
    run = history.iter_run_meta()[0]["run_id"]
    return original, target / "a.txt", run


def test_undo_detects_same_size_edit_even_if_mtime_restored(moved):
    original, destination, run = moved
    info = destination.stat()
    destination.write_text("modified data")
    os.utime(destination, ns=(info.st_atime_ns, info.st_mtime_ns))
    result = history.undo_run(run)
    assert (result.reverted, result.skipped, result.errors) == (0, 1, 0)
    assert not original.exists()
    assert destination.read_text() == "modified data"
    assert (
        history.load_run_events(result.undo_run_id)[0]["reason"]
        == "file_identity_changed"
    )


def test_undo_detects_replacement_with_same_content(moved):
    original, destination, run = moved
    info = destination.stat()
    replacement = destination.with_suffix(".replacement")
    replacement.write_bytes(destination.read_bytes())
    os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
    os.replace(replacement, destination)
    result = history.undo_run(run)
    assert result.skipped == 1
    assert not original.exists() and destination.exists()


def test_undo_preserves_recreated_original(moved):
    original, destination, run = moved
    original.write_text("new unrelated file")
    result = history.undo_run(run)
    assert result.skipped == 1
    assert original.read_text() == "new unrelated file"
    assert destination.exists()


def test_repeated_undo_never_replays_even_if_file_is_moved_back(moved):
    original, destination, run = moved
    first = history.undo_run(run)
    assert first.reverted == 1
    original.rename(destination)
    second = history.undo_run(run)
    assert second.reverted == 0 and second.skipped == 1
    assert destination.exists() and not original.exists()
    assert (
        history.load_run_events(second.undo_run_id)[0]["reason"] == "already_reverted"
    )


def test_missing_undo_file_is_reported(moved):
    original, destination, run = moved
    destination.unlink()
    result = history.undo_run(run)
    assert result.skipped == 1
    assert not original.exists()


def test_legacy_unverified_move_is_not_undone(tmp_path, fake_home):
    original = tmp_path / "original.txt"
    destination = tmp_path / "destination.txt"
    destination.write_text("unverified")
    writer = history.HistoryWriter(
        profile="test",
        command="run",
        config_path=tmp_path / "rules.yaml",
        source=tmp_path,
        dry_run=False,
    )
    writer.record(
        dict(
            event="moved",
            source=str(original),
            destination=str(destination),
            undo_supported=True,
        )
    )
    writer.complete()
    assert history.undo_run(writer.run.run_id).skipped == 1
    assert destination.exists() and not original.exists()


def test_overwrite_move_does_not_advertise_full_undo(tmp_path, fake_home):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "a.txt").write_text("new")
    (target / "a.txt").write_text("old")
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            dict(
                source=str(source),
                default_target=str(target),
                conflict_policy="overwrite",
            )
        )
    )
    run_pipeline(str(config), profile="test", command="run")
    run = history.iter_run_meta()[0]["run_id"]
    event = next(e for e in history.load_run_events(run) if e["event"] == "moved")
    assert event["undo_supported"] is False
    assert history.undo_run(run).skipped == 1
    assert (target / "a.txt").read_text() == "new"


def test_truncated_final_history_append_is_ignored(moved):
    _, _, run = moved
    item = history.find_run(run)
    path = Path(item["_events_path"])
    with path.open("a") as stream:
        stream.write('{"event":')
    assert history.undo_run(run).reverted == 1


def test_corrupt_complete_history_record_fails_closed(moved):
    _, destination, run = moved
    path = Path(history.find_run(run)["_events_path"])
    with path.open("a") as stream:
        stream.write('{"event":\n')
    with pytest.raises(json.JSONDecodeError):
        history.undo_run(run)
    assert destination.exists()


def test_undo_uses_verified_cross_device_fallback(moved, monkeypatch):
    original, destination, run = moved
    real_link = operations.os.link

    def cross_device(source, target, **kwargs):
        if source == destination:
            raise OSError(errno.EXDEV, "cross-device")
        return real_link(source, target, **kwargs)

    monkeypatch.setattr(operations.os, "link", cross_device)
    assert history.undo_run(run).reverted == 1
    assert original.read_text() == "original data"
    assert not destination.exists()


def test_history_clear_does_not_follow_directory_links(tmp_path, fake_home):
    outside = tmp_path / "outside"
    run = outside / "some-run"
    run.mkdir(parents=True)
    valuable = run / "important.txt"
    valuable.write_text("keep")
    root = history.runs_root()
    root.mkdir(parents=True)
    link = root / "2000-01-01"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    assert history.clear_history() == 0
    assert history.prune_history(days=7) == 0
    assert valuable.read_text() == "keep"
