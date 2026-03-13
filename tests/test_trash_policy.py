from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import filemindr.core.runner as runner


def test_trash_calls_send2trash(monkeypatch, tmp_path: Path):
    target = tmp_path / "existing.txt"
    target.write_text("old", encoding="utf-8")

    calls = {"n": 0, "arg": None}

    def fake_send2trash(arg: str):
        calls["n"] += 1
        calls["arg"] = arg

    monkeypatch.setitem(
        runner.__dict__,
        "send2trash",
        SimpleNamespace(send2trash=fake_send2trash),
    )

    import sys
    sys.modules["send2trash"] = SimpleNamespace(send2trash=fake_send2trash)

    runner._trash(target)

    assert calls["n"] == 1
    assert calls["arg"] == str(target)


def test_trash_raises_and_preserves_file_when_send2trash_fails(monkeypatch, tmp_path: Path):
    target = tmp_path / "existing.txt"
    target.write_text("old", encoding="utf-8")

    def failing_send2trash(_: str):
        raise RuntimeError("boom")

    import sys
    sys.modules["send2trash"] = SimpleNamespace(send2trash=failing_send2trash)

    with pytest.raises(RuntimeError, match="Failed to send"):
        runner._trash(target)

    assert target.exists()
    assert target.read_text(encoding="utf-8") == "old"


def test_policy_trash_replaces_existing_destination(monkeypatch, tmp_path: Path):
    """
    Lightweight integration: if trash succeeds, the existing destination is
    removed and the new file is copied into place.
    """
    source_dir = tmp_path / "Downloads"
    source_dir.mkdir()

    dest_dir = tmp_path / "images"
    dest_dir.mkdir()

    src = source_dir / "pic.jpg"
    src.write_text("new", encoding="utf-8")

    dest = dest_dir / "pic.jpg"
    dest.write_text("old", encoding="utf-8")

    cfg = tmp_path / "filemindr.yaml"
    cfg.write_text(
        f"""
source: {source_dir}
default_target: {tmp_path / "others"}
conflict_policy: trash
rules:
  - name: images
    priority: 10
    match:
      extensions: ["jpg"]
    action:
      copy_to: {dest_dir}
""".strip(),
        encoding="utf-8",
    )

    trashed = {"n": 0, "arg": None}

    def fake_trash(p: Path):
        trashed["n"] += 1
        trashed["arg"] = p
        if p.exists():
            p.unlink()

    monkeypatch.setattr(runner, "_trash", fake_trash)

    runner.run_pipeline(str(cfg), dry_run=False)

    assert trashed["n"] == 1
    assert trashed["arg"] == dest
    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == "new"


def test_policy_trash_does_not_replace_destination_when_trash_fails(monkeypatch, tmp_path: Path):
    source_dir = tmp_path / "Downloads"
    source_dir.mkdir()

    dest_dir = tmp_path / "images"
    dest_dir.mkdir()

    src = source_dir / "pic.jpg"
    src.write_text("new", encoding="utf-8")

    dest = dest_dir / "pic.jpg"
    dest.write_text("old", encoding="utf-8")

    cfg = tmp_path / "filemindr.yaml"
    cfg.write_text(
        f"""
source: {source_dir}
default_target: {tmp_path / "others"}
conflict_policy: trash
rules:
  - name: images
    priority: 10
    match:
      extensions: ["jpg"]
    action:
      copy_to: {dest_dir}
""".strip(),
        encoding="utf-8",
    )

    def failing_trash(_: Path):
        raise RuntimeError("trash unavailable")

    monkeypatch.setattr(runner, "_trash", failing_trash)

    runner.run_pipeline(str(cfg), dry_run=False)

    assert src.exists()
    assert src.read_text(encoding="utf-8") == "new"
    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == "old"