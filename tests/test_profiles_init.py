from __future__ import annotations


def test_profile_init_creates_rules_and_registers(runner, fake_home, cli_app):
    result = runner.invoke(cli_app, ["profile", "init", "home"])
    assert result.exit_code == 0

    base = fake_home / ".filemindr"
    rules_file = base / "rules" / "home" / "rules.yaml"
    profiles_file = base / "profiles.yaml"

    assert rules_file.exists()
    assert profiles_file.exists()

    text = profiles_file.read_text(encoding="utf-8")
    assert "home" in text
