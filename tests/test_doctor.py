from __future__ import annotations


def test_doctor_ok(runner, fake_home, cli_app, tmp_path):
    r1 = runner.invoke(cli_app, ["profile", "init", "home"])
    assert r1.exit_code == 0

    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)

    rules_file = fake_home / ".filemindr" / "rules" / "home" / "rules.yaml"
    rules_file.write_text(
        f"""
source: {src.as_posix()}
default_target: {src.as_posix()}/others
conflict_policy: rename

rules:
  - name: images
    priority: 10
    match:
      extensions: ["jpg"]
    action:
      move_to: {src.as_posix()}/images
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(cli_app, ["doctor"])
    assert result.exit_code == 0, (result.stdout or "") + (result.stderr or "")

def test_doctor_fails_when_rules_missing(runner, fake_home, cli_app):
    runner.invoke(cli_app, ["profile", "init", "home"])

    rules_file = fake_home / ".filemindr" / "rules" / "home" / "rules.yaml"
    rules_file.unlink()

    result = runner.invoke(cli_app, ["doctor"])
    assert result.exit_code != 0
