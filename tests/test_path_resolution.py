from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import filemindr.cli as cli
from filemindr.cli import app

runner = CliRunner()


def _write_profile(fake_home: Path, profile: str, rules_yaml: str) -> Path:
    base = fake_home / ".filemindr"
    rules_dir = base / "rules" / profile
    rules_dir.mkdir(parents=True, exist_ok=True)

    (rules_dir / "rules.yaml").write_text(rules_yaml, encoding="utf-8")
    (base / "profiles.yaml").write_text(
        f"profiles:\n  {profile}: {rules_dir.as_posix()}\n",
        encoding="utf-8",
    )
    return rules_dir


def test_validate_resolves_relative_source_from_config_dir(fake_home: Path):
    rules_dir = fake_home / ".filemindr" / "rules" / "home"
    source_dir = rules_dir / "inbox"
    source_dir.mkdir(parents=True, exist_ok=True)

    _write_profile(
        fake_home,
        "home",
        """
source: inbox
default_target: others
conflict_policy: rename

rules:
  - name: images
    priority: 10
    match:
      extensions: [\"jpg\"]
    action:
      move_to: images
""".strip(),
    )

    result = runner.invoke(app, ["validate", "-p", "home"])

    assert result.exit_code == 0, (result.stdout or "") + (result.stderr or "")


def test_doctor_resolves_relative_paths_from_config_dir(fake_home: Path, cli_app):
    rules_dir = fake_home / ".filemindr" / "rules" / "home"
    (rules_dir / "inbox").mkdir(parents=True, exist_ok=True)
    (rules_dir / "others").mkdir(parents=True, exist_ok=True)
    (rules_dir / "images").mkdir(parents=True, exist_ok=True)

    _write_profile(
        fake_home,
        "home",
        """
source: inbox
default_target: others
conflict_policy: rename

rules:
  - name: images
    priority: 10
    match:
      extensions: [\"jpg\"]
    action:
      move_to: images
""".strip(),
    )

    result = runner.invoke(cli_app, ["doctor"])

    assert result.exit_code == 0, (result.stdout or "") + (result.stderr or "")
    assert "OK" in ((result.stdout or "") + (result.stderr or ""))


def test_explain_resolves_relative_targets_from_config_dir(fake_home: Path):
    rules_dir = fake_home / ".filemindr" / "rules" / "home"
    source_dir = rules_dir / "inbox"
    source_dir.mkdir(parents=True, exist_ok=True)

    file_path = source_dir / "pic.jpg"
    file_path.write_text("x", encoding="utf-8")

    _write_profile(
        fake_home,
        "home",
        """
source: inbox
default_target: others
conflict_policy: rename

rules:
  - name: images
    priority: 10
    match:
      extensions: [\"jpg\"]
    action:
      move_to: images
""".strip(),
    )

    result = runner.invoke(app, ["explain", str(file_path), "-p", "home"])

    assert result.exit_code == 0, (result.stdout or "") + (result.stderr or "")
    expected_dest = str((rules_dir / "images" / "pic.jpg").resolve())
    out = (result.stdout or "") + (result.stderr or "")
    assert expected_dest in out


def test_watch_resolves_relative_source_from_config_dir(fake_home: Path, monkeypatch):
    rules_dir = fake_home / ".filemindr" / "rules" / "home"
    source_dir = rules_dir / "inbox"
    source_dir.mkdir(parents=True, exist_ok=True)

    _write_profile(
        fake_home,
        "home",
        """
source: inbox
default_target: others
conflict_policy: rename

rules:
  - name: images
    priority: 10
    match:
      extensions: [\"jpg\"]
    action:
      move_to: images
""".strip(),
    )

    captured: dict[str, object] = {}

    def fake_watch_and_run(*, source_dir: Path, config_path: str, dry_run: bool, opts, once: bool):
        captured["source_dir"] = source_dir
        captured["config_path"] = config_path
        captured["dry_run"] = dry_run
        captured["once"] = once

    monkeypatch.setattr(cli, "watch_and_run", fake_watch_and_run)

    result = runner.invoke(app, ["watch", "-p", "home", "--once"])

    assert result.exit_code == 0, (result.stdout or "") + (result.stderr or "")
    assert captured["source_dir"] == source_dir.resolve()
    assert captured["once"] is True