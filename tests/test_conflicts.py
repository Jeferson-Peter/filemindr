from pathlib import Path

from filemindr.core.runner import _resolve_conflict


def test_conflict_rename(tmp_path: Path):
    dest = tmp_path / "report.pdf"
    dest.write_text("old")

    resolved = _resolve_conflict(dest, "rename")
    assert resolved is not None
    assert resolved.name == "report (1).pdf"


def test_conflict_skip(tmp_path: Path):
    dest = tmp_path / "report.pdf"
    dest.write_text("old")

    resolved = _resolve_conflict(dest, "skip")
    assert resolved is None


def test_conflict_overwrite(tmp_path: Path):
    dest = tmp_path / "report.pdf"
    dest.write_text("old")

    resolved = _resolve_conflict(dest, "overwrite")
    assert resolved == dest