from __future__ import annotations


def test_profile_init_existing_without_force_fails(runner, fake_home, cli_app):
    runner.invoke(cli_app, ["profile", "init", "home"])

    result = runner.invoke(cli_app, ["profile", "init", "home"])
    assert result.exit_code != 0
    assert "already exists" in result.stdout.lower() or result.stderr.lower()
