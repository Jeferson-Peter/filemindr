from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from filemindr.cli import app
from filemindr.core.history import history_root

runner = CliRunner()


def _write_profile(fake_home: Path, profile: str, rules_yaml_text: str) -> None:
    base = fake_home / ".filemindr"
    rules_dir = base / "rules" / profile
    rules_dir.mkdir(parents=True, exist_ok=True)

    (rules_dir / "rules.yaml").write_text(rules_yaml_text, encoding="utf-8")

    (base / "profiles.yaml").write_text(
        f"profiles:\n  {profile}: {rules_dir.as_posix()}\n",
        encoding="utf-8",
    )


def test_history_records_runs_and_can_show_them(tmp_path: Path, fake_home: Path):
    src = tmp_path / "Downloads"
    src.mkdir()
    (src / "a.pdf").write_text("x", encoding="utf-8")

    rules = f"""
source: {src.as_posix()}
default_target: {src.as_posix()}/others
conflict_policy: rename

rules:
  - name: documents
    priority: 10
    match:
      extensions: ["pdf"]
    action:
      move_to: {src.as_posix()}/documents
""".strip()
    _write_profile(fake_home, "home", rules)

    run_result = runner.invoke(app, ["run", "-p", "home", "--dry-run"])
    assert run_result.exit_code == 0, (run_result.stdout or "") + (run_result.stderr or "")

    list_result = runner.invoke(app, ["history", "list"])
    assert list_result.exit_code == 0
    out = (list_result.stdout or "") + (list_result.stderr or "")
    assert "command=run" in out
    assert "profile=home" in out

    run_id = out.split("|", 1)[0].strip()
    show_result = runner.invoke(app, ["history", "show", run_id])
    assert show_result.exit_code == 0
    show_out = (show_result.stdout or "") + (show_result.stderr or "")
    assert "planned_move" in show_out
    assert "documents" in show_out


def test_history_prune_removes_old_runs(tmp_path: Path, fake_home: Path):
    old_run = history_root() / "runs" / "2000-01-01" / "oldrun"
    old_run.mkdir(parents=True, exist_ok=True)
    (old_run / "meta.json").write_text(
        json.dumps(
            {
                "run_id": "oldrun",
                "profile": "home",
                "status": "completed",
                "dry_run": True,
                "started_at": "2000-01-01T00:00:00+00:00",
                "finished_at": "2000-01-01T00:00:01+00:00",
                "counts": {},
            }
        ),
        encoding="utf-8",
    )
    (old_run / "events.jsonl").write_text("", encoding="utf-8")

    result = runner.invoke(app, ["history", "prune", "--days", "7"])
    assert result.exit_code == 0

    out = (result.stdout or "") + (result.stderr or "")
    assert "Removed 1 run(s)." in out
    assert not old_run.exists()


def test_history_clear_removes_all_runs(tmp_path: Path, fake_home: Path):
    run_dir = history_root() / "runs" / "2000-01-01" / "oldrun"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "meta.json").write_text("{}", encoding="utf-8")
    (run_dir / "events.jsonl").write_text("", encoding="utf-8")

    result = runner.invoke(app, ["history", "clear", "--yes"])
    assert result.exit_code == 0

    out = (result.stdout or "") + (result.stderr or "")
    assert "Removed 1 run(s)." in out
    assert not run_dir.exists()
