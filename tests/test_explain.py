from __future__ import annotations

import os
from pathlib import Path

from typer.testing import CliRunner

from filemindr.cli import app

runner = CliRunner()


def _write_profile(fake_home: Path, profile: str, rules_yaml: str) -> None:
    """
    Create:
      ~/.filemindr/profiles.yaml
      ~/.filemindr/rules/<profile>/rules.yaml
    under fake_home (which is Path.home()).
    """
    base = fake_home / ".filemindr"
    rules_dir = base / "rules" / profile
    rules_dir.mkdir(parents=True, exist_ok=True)

    (rules_dir / "rules.yaml").write_text(rules_yaml, encoding="utf-8")

    profiles_yaml = base / "profiles.yaml"
    profiles_yaml.write_text(
        f"profiles:\n  {profile}: {rules_dir.as_posix()}\n",
        encoding="utf-8",
    )


def _rules_for(source_dir: Path) -> str:
    src = source_dir.as_posix()
    return f"""
source: {src}
default_target: {src}/others
conflict_policy: rename

rules:
  - name: images
    priority: 50
    match:
      extensions: ["jpg", "png"]
    action:
      move_to: {src}/images
""".lstrip()


def test_explain_marks_directories_as_skip(tmp_path: Path, fake_home: Path, runner: CliRunner):
    _write_profile(fake_home, "home", _rules_for(tmp_path))

    (tmp_path / "documents").mkdir()
    (tmp_path / "pic.jpg").write_text("x", encoding="utf-8")

    result = runner.invoke(app, ["explain", str(tmp_path), "-p", "home"])
    assert result.exit_code == 0, (result.stdout or "") + (result.stderr or "")

def test_explain_respects_default_limit(tmp_path: Path, fake_home: Path):
    _write_profile(fake_home, "home", _rules_for(tmp_path))

    for i in range(0, 80):
        (tmp_path / f"f{i:03d}.txt").write_text("x", encoding="utf-8")

    result = runner.invoke(app, ["explain", str(tmp_path), "-p", "home"])
    assert result.exit_code == 0

    out = (result.stdout or "") + (result.stderr or "")

    assert "Showing first" in out

    assert "f000.txt" in out
    assert "f049.txt" in out
    assert "f079.txt" not in out


def test_explain_limit_option_overrides(tmp_path: Path, fake_home: Path, runner: CliRunner):
    _write_profile(fake_home, "home", _rules_for(tmp_path))

    for i in range(0, 30):
        (tmp_path / f"f{i:03d}.txt").write_text("x", encoding="utf-8")

    result = runner.invoke(app, ["explain", str(tmp_path), "-p", "home", "--limit", "10"])
    assert result.exit_code == 0, (result.stdout or "") + (result.stderr or "")

    out = (result.stdout or "") + (result.stderr or "")
    assert "f000.txt" in out
    assert "f009.txt" in out
    assert "f010.txt" not in out


def test_explain_all_disables_limit(tmp_path: Path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    _write_profile(fake_home, "home", _rules_for(tmp_path))

    for i in range(0, 60):
        (tmp_path / f"f{i:03d}.txt").write_text("x", encoding="utf-8")

    result = runner.invoke(app, ["explain", str(tmp_path), "-p", "home", "--all"])
    assert result.exit_code == 0

    out = (result.stdout or "") + (result.stderr or "")

    assert "f000.txt" in out
    assert "f059.txt" in out
    assert "Showing first" not in out
