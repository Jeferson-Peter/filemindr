from __future__ import annotations


def test_profile_list_shows_profiles(runner, fake_home, cli_app):
    runner.invoke(cli_app, ["profile", "init", "home"])

    result = runner.invoke(cli_app, ["profile", "list"])
    assert result.exit_code == 0
    assert "home" in result.stdout
