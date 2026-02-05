from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from filemindr.cli import app

runner = CliRunner()


def test_validate_ok(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    src = tmp_path / "Downloads"
    src.mkdir()

    cfg = tmp_path / "filemindr.yaml"
    cfg.write_text(
        """
source: ./Downloads
default_target: ./others
conflict_policy: rename

rules:
  - name: images
    priority: 10
    match:
      extensions: ["jpg"]
    action:
      move_to: ./images
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate"])
    assert result.exit_code == 0
    assert "Config is valid" in result.stdout


def test_validate_invalid_regex(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    src = tmp_path / "Downloads"
    src.mkdir()

    cfg = tmp_path / "filemindr.yaml"
    cfg.write_text(
        """
source: ./Downloads
rules:
  - name: bad
    priority: 1
    match:
      regex: "("
      extensions: ["pdf"]
    action:
      move_to: ./documents
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate"])
    assert result.exit_code == 1
    assert "invalid regex" in result.stdout.lower()


def test_validate_rejects_move_and_copy_together(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    src = tmp_path / "Downloads"
    src.mkdir()

    cfg = tmp_path / "filemindr.yaml"
    cfg.write_text(
        """
source: ./Downloads
rules:
  - name: both
    priority: 1
    match:
      extensions: ["pdf"]
    action:
      move_to: ./documents
      copy_to: ./backup
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate"])
    assert result.exit_code == 1
    assert "exactly one of action.move_to or action.copy_to" in result.stdout
