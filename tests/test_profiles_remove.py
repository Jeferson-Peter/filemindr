from __future__ import annotations


def test_profile_remove_deletes_registry_and_folder(runner, fake_home, cli_app):
    runner.invoke(cli_app, ["profile", "init", "home"])

    base = fake_home / ".filemindr"
    profile_dir = base / "rules" / "home"
    assert profile_dir.exists()

    result = runner.invoke(cli_app, ["profile", "remove", "home", "--yes"])
    assert result.exit_code == 0

    assert not profile_dir.exists()

    profiles_file = base / "profiles.yaml"
    if profiles_file.exists():
        text = profiles_file.read_text(encoding="utf-8")
        assert "home" not in text
