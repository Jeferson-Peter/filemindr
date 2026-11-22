from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from filemindr.core.explain import explain_files
from filemindr.core.history import iter_run_meta
from filemindr.core.operations import OperationPlan, execute_operation
from filemindr.core.planning import ExecutionPlanner
from filemindr.core.runner import run_pipeline


def write_config(path: Path, source: Path, target: Path, rules: list[dict]) -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "source": str(source),
                "default_target": str(target),
                "conflict_policy": "rename",
                "rules": rules,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_explain_dry_run_and_execution_share_numbered_destinations(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    first = source / "a.txt"
    second = source / "b.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    target = tmp_path / "output"
    config = write_config(
        tmp_path / "rules.yaml",
        source,
        target,
        [
            {
                "name": "all-text",
                "priority": 10,
                "match": {"extensions": ["txt"]},
                "action": {"move_to": str(target), "rename_template": "same.txt"},
            }
        ],
    )

    explained = explain_files(str(config), [second, first])
    destinations = [str(item.resolved) for item in explained]
    assert destinations == [str(target / "same.txt"), str(target / "same (1).txt")]

    report = tmp_path / "dry-run.json"
    dry = run_pipeline(str(config), dry_run=True, report_path=report)
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert [event["destination"] for event in payload["events"]] == destinations
    assert dry["planned_moves"] == 2
    assert first.exists() and second.exists()
    assert not target.exists()

    result = run_pipeline(str(config))
    assert result["moved"] == 2
    assert (target / "same.txt").read_text(encoding="utf-8") == "first"
    assert (target / "same (1).txt").read_text(encoding="utf-8") == "second"


def test_file_directory_conflict_is_reported_by_explain_dry_run_and_execution(
    tmp_path: Path,
):
    source = tmp_path / "source"
    source.mkdir()
    first = source / "a.txt"
    second = source / "b.txt"
    first.write_text("blocker", encoding="utf-8")
    second.write_text("child", encoding="utf-8")
    output = tmp_path / "output"
    config = write_config(
        tmp_path / "rules.yaml",
        source,
        tmp_path / "fallback",
        [
            {
                "name": "blocker",
                "priority": 20,
                "match": {"regex": "^a\\.txt$"},
                "action": {"move_to": str(output), "rename_template": "blocked"},
            },
            {
                "name": "child",
                "priority": 10,
                "match": {"regex": "^b\\.txt$"},
                "action": {"move_to": str(output / "blocked")},
            },
        ],
    )

    explained = explain_files(str(config), [second, first])
    assert explained[0].resolved == output / "blocked"
    assert explained[1].action == "ERROR"
    assert "planned file" in (explained[1].reason or "")
    assert explained[1].dest == output / "blocked" / "b.txt"

    dry = run_pipeline(str(config), dry_run=True)
    assert dry["planned_moves"] == 1
    assert dry["errors"] == 1
    actual = run_pipeline(str(config))
    assert actual["moved"] == 1
    assert actual["errors"] == 1
    assert (output / "blocked").read_text(encoding="utf-8") == "blocker"
    assert second.exists()


def test_execution_rejects_source_changed_after_planning(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    path = source / "item.txt"
    path.write_text("before", encoding="utf-8")
    target = tmp_path / "output"
    config = write_config(tmp_path / "rules.yaml", source, target, [])
    planner = ExecutionPlanner(config)
    item = next(planner.plan_files([path], capture_destination_identity=True))
    path.write_text("after", encoding="utf-8")

    with pytest.raises(OSError, match="Source changed since planning"):
        execute_operation(
            OperationPlan(
                item.source,
                item.destination,
                item.action,
                item.policy,
                item.expected_destination,
            ),
            expected_source_snapshot=item.source_snapshot,
        )
    assert path.read_text(encoding="utf-8") == "after"
    assert not item.destination.exists()


def test_execution_rejects_destination_parent_replaced_after_planning(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    path = source / "item.txt"
    path.write_text("before", encoding="utf-8")
    target = tmp_path / "output"
    target.mkdir()
    config = write_config(tmp_path / "rules.yaml", source, target, [])
    planner = ExecutionPlanner(config)
    item = next(planner.plan_files([path], capture_destination_identity=True))
    target.rename(tmp_path / "output-before-race")
    target.write_text("a file now blocks the target directory", encoding="utf-8")

    with pytest.raises(OSError, match="Destination parent changed"):
        execute_operation(
            OperationPlan(
                item.source,
                item.destination,
                item.action,
                item.policy,
                item.expected_destination,
            ),
            expected_source_snapshot=item.source_snapshot,
        )
    assert path.exists()
    assert target.is_file()


def test_execution_rejects_destination_created_after_planning(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    path = source / "item.txt"
    path.write_text("source", encoding="utf-8")
    target = tmp_path / "output"
    config = write_config(tmp_path / "rules.yaml", source, target, [])
    item = next(
        ExecutionPlanner(config).plan_files([path], capture_destination_identity=True)
    )
    item.destination.parent.mkdir()
    item.destination.write_text("concurrent file", encoding="utf-8")

    with pytest.raises(FileExistsError, match="Destination changed since planning"):
        execute_operation(
            OperationPlan(
                item.source,
                item.destination,
                item.action,
                item.policy,
                item.expected_destination,
            ),
            expected_source_snapshot=item.source_snapshot,
        )
    assert item.destination.read_text(encoding="utf-8") == "concurrent file"
    assert path.read_text(encoding="utf-8") == "source"


def test_dynamic_target_templates_are_resolved_per_source(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    first = source / "a.dat"
    second = source / "b.dat"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    for path, year in ((first, 2024), (second, 2025)):
        stamp = datetime(year, 1, 1).timestamp()
        os.utime(path, (stamp, stamp))
    target = tmp_path / "archive" / "{yyyy}"
    config = write_config(
        tmp_path / "rules.yaml",
        source,
        target,
        [
            {
                "name": "dated",
                "priority": 10,
                "match": {"extensions": ["dat"]},
                "action": {"move_to": str(target)},
            }
        ],
    )

    items = list(ExecutionPlanner(config).plan_files([second, first]))

    assert [item.destination.parent.name for item in items] == ["2024", "2025"]


def test_metrics_and_history_match_partial_execution(
    tmp_path: Path, fake_home, monkeypatch
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("first", encoding="utf-8")
    (source / "b.txt").write_text("secret-content", encoding="utf-8")
    target = tmp_path / "output"
    config = write_config(
        tmp_path / "rules.yaml",
        source,
        target,
        [
            {
                "name": "text",
                "priority": 10,
                "match": {"extensions": ["txt"]},
                "action": {"move_to": str(target)},
            }
        ],
    )
    from filemindr.core import runner

    execute = runner.execute_operation

    def fail_second(plan, **kwargs):
        if plan.source.name == "b.txt":
            raise RuntimeError("simulated operation failure")
        return execute(plan, **kwargs)

    monkeypatch.setattr(runner, "execute_operation", fail_second)
    report = tmp_path / "report.json"
    result = run_pipeline(
        str(config), profile="home", command="run", report_path=report
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    metrics = payload["metrics"]

    assert result["moved"] == 1 and result["errors"] == 1
    assert metrics["planned"] == {"moves": 2}
    assert metrics["completed"] == {"moves": 1}
    assert metrics["operations_by_type"] == {"move": 1}
    assert metrics["errors_by_category"] == {"RuntimeError": 1}
    assert metrics["rule_matches"] == {"text": 2}
    assert metrics["per_rule"]["text"]["planned_moves"] == 2
    assert metrics["per_rule"]["text"]["completed_moves"] == 1
    assert "secret-content" not in report.read_text(encoding="utf-8")

    history = iter_run_meta()
    assert history[-1]["status"] == "failed"
    assert history[-1]["metrics"] == metrics
