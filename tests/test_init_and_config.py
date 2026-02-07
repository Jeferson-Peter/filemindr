from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from filemindr.cli import app
from filemindr.core.config import resolve_profile_config

runner = CliRunner()


def _fake_home(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: fake_home)


def _profile_paths(fake_home: Path, profile: str) -> tuple[Path, Path, Path]:
    """
    Returns:
      base_dir, rules_yaml_path, profiles_yaml_path
    """
    base = fake_home / ".filemindr"
    rules_yaml = base / "rules" / profile / "rules.yaml"
    profiles_yaml = base / "profiles.yaml"
    return base, rules_yaml, profiles_yaml


def test_profile_init_creates_rules_and_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "home"
    _fake_home(monkeypatch, fake_home)

    result = runner.invoke(app, ["profile", "init", "home"])
    assert result.exit_code == 0

    _, rules_yaml, profiles_yaml = _profile_paths(fake_home, "home")

    assert rules_yaml.exists()
    assert profiles_yaml.exists()

    text = rules_yaml.read_text(encoding="utf-8")
    assert "filemindr configuration" in text
    assert "home" in profiles_yaml.read_text(encoding="utf-8")


def test_profile_init_refuses_overwrite_without_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "home"
    _fake_home(monkeypatch, fake_home)

    r1 = runner.invoke(app, ["profile", "init", "home"])
    assert r1.exit_code == 0

    r2 = runner.invoke(app, ["profile", "init", "home"])
    assert r2.exit_code != 0


def test_profile_init_force_overwrites_rules_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "home"
    _fake_home(monkeypatch, fake_home)

    r1 = runner.invoke(app, ["profile", "init", "home"])
    assert r1.exit_code == 0

    _, rules_yaml, _ = _profile_paths(fake_home, "home")

    rules_yaml.write_text("old", encoding="utf-8")

    r2 = runner.invoke(app, ["profile", "init", "home", "--force"])
    assert r2.exit_code == 0

    text = rules_yaml.read_text(encoding="utf-8")
    assert text != "old"
    assert "filemindr configuration" in text


def test_resolve_profile_config_returns_rules_yaml(fake_home: Path, runner: CliRunner):
    runner.invoke(app, ["profile", "init", "home"])
    resolved = Path(resolve_profile_config("home"))
    assert resolved.exists()
    assert resolved.name == "rules.yaml"

def test_resolve_profile_config_raises_when_profile_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "home"
    _fake_home(monkeypatch, fake_home)

    with pytest.raises(Exception):
        resolve_profile_config("missing-profile")
