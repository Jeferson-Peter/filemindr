from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from filemindr.cli import app

runner = CliRunner()


def _fake_home(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    fake_home.mkdir(parents=True, exist_ok=True)


    monkeypatch.setattr(Path, "home", lambda: fake_home)

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    monkeypatch.setenv("HOMEDRIVE", fake_home.drive or "C:")
    monkeypatch.setenv(
        "HOMEPATH",
        "\\" + "\\".join(fake_home.parts[1:]) if fake_home.drive else str(fake_home).replace("/", "\\"),
    )


def _write_profile(fake_home: Path, profile: str, rules_yaml_text: str) -> None:
    base = fake_home / ".filemindr"
    rules_dir = base / "rules" / profile
    rules_dir.mkdir(parents=True, exist_ok=True)

    (rules_dir / "rules.yaml").write_text(rules_yaml_text, encoding="utf-8")

    (base / "profiles.yaml").write_text(
        f"profiles:\n  {profile}: {rules_dir.as_posix()}\n",
        encoding="utf-8",
    )


def test_validate_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "home"
    _fake_home(monkeypatch, fake_home)

    src = tmp_path / "Downloads"
    src.mkdir()
    src_s = src.as_posix()

    rules = f"""
source: {src_s}
default_target: {src_s}/others
conflict_policy: rename

rules:
  - name: images
    priority: 10
    match:
      extensions: ["jpg"]
    action:
      move_to: {src_s}/images
""".strip()

    _write_profile(fake_home, "home", rules)

    result = runner.invoke(app, ["validate", "-p", "home"])
    assert result.exit_code == 0

    out = (result.stdout or "") + (result.stderr or "")
    assert "Config is valid" in out


def test_validate_invalid_regex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "home"
    _fake_home(monkeypatch, fake_home)

    src = tmp_path / "Downloads"
    src.mkdir()
    src_s = src.as_posix()

    rules = f"""
source: {src_s}
default_target: {src_s}/others

rules:
  - name: bad
    priority: 1
    match:
      regex: "("
      extensions: ["pdf"]
    action:
      move_to: {src_s}/documents
""".strip()

    _write_profile(fake_home, "home", rules)

    result = runner.invoke(app, ["validate", "-p", "home"])
    assert result.exit_code == 1

    out = ((result.stdout or "") + (result.stderr or "")).lower()
    assert "regex" in out


def test_validate_rejects_move_and_copy_together(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "home"
    _fake_home(monkeypatch, fake_home)

    src = tmp_path / "Downloads"
    src.mkdir()
    src_s = src.as_posix()

    rules = f"""
source: {src_s}

rules:
  - name: both
    priority: 1
    match:
      extensions: ["pdf"]
    action:
      move_to: {src_s}/documents
      copy_to: {src_s}/backup
""".strip()

    _write_profile(fake_home, "home", rules)

    result = runner.invoke(app, ["validate", "-p", "home"])
    assert result.exit_code == 1

    out = ((result.stdout or "") + (result.stderr or "")).lower()
    assert "move_to" in out and "copy_to" in out


def test_validate_rejects_invalid_rename_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "home"
    _fake_home(monkeypatch, fake_home)

    src = tmp_path / "Downloads"
    src.mkdir()
    src_s = src.as_posix()

    rules = f"""
source: {src_s}

rules:
  - name: bad-template
    priority: 1
    match:
      extensions: ["pdf"]
    action:
      move_to: {src_s}/documents/{{yyyy}}
      rename_template: nested/{{unknown}}.pdf
""".strip()

    _write_profile(fake_home, "home", rules)

    result = runner.invoke(app, ["validate", "-p", "home"])
    assert result.exit_code == 1

    out = ((result.stdout or "") + (result.stderr or "")).lower()
    assert "rename_template" in out
    assert "unsupported template fields" in out or "must only define a file name" in out
