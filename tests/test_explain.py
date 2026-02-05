from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from filemindr.cli import app


runner = CliRunner()


def _write_config(tmp_path: Path) -> None:
    (tmp_path / "filemindr.yaml").write_text(
        """
source: .
default_target: ./others
conflict_policy: rename

rules:
  - name: images
    priority: 50
    match:
      extensions: ["jpg", "png"]
    action:
      move_to: ./images
""".lstrip(),
        encoding="utf-8",
    )


def test_explain_marks_directories_as_skip(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path)

    (tmp_path / "documents").mkdir()
    (tmp_path / "pic.jpg").write_text("x", encoding="utf-8")

    result = runner.invoke(app, ["explain", str(tmp_path)])

    assert result.exit_code == 0
    out = result.stdout + result.stderr

    assert "documents" in out
    assert "[SKIP]" in out

    assert "pic.jpg" in out


def test_explain_respects_default_limit(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path)

    for i in range(0, 80):
        (tmp_path / f"f{i:03d}.txt").write_text("x", encoding="utf-8")

    result = runner.invoke(app, ["explain", str(tmp_path)])

    assert result.exit_code == 0
    out = result.stdout + result.stderr

    assert "Showing first" in out

    assert "f000.txt" in out
    assert "f049.txt" in out
    assert "f079.txt" not in out


def test_explain_limit_option_overrides(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path)

    for i in range(0, 30):
        (tmp_path / f"f{i:03d}.txt").write_text("x", encoding="utf-8")

    result = runner.invoke(app, ["explain", str(tmp_path), "--limit", "10"])

    assert result.exit_code == 0
    out = result.stdout + result.stderr

    assert "f000.txt" in out
    assert "f009.txt" in out
    assert "f010.txt" not in out


def test_explain_all_disables_limit(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path)

    for i in range(0, 60):
        (tmp_path / f"f{i:03d}.txt").write_text("x", encoding="utf-8")

    result = runner.invoke(app, ["explain", str(tmp_path), "--all"])

    assert result.exit_code == 0
    out = result.stdout + result.stderr

    assert "f000.txt" in out
    assert "f059.txt" in out
    assert "Showing first" not in out
