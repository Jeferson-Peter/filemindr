from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from filemindr.core.runner import run_pipeline
from filemindr.core.templating import render_target_template
from filemindr.core.validator import validate_config_file


@pytest.mark.parametrize(
    "override",
    [
        {"conflict_policy": "typo"},
        {"rules": {}},
        {"rules": [1]},
        {"ignore": "*.txt"},
        {"default_target": "out/{unknown}"},
        {"default_target": "out/{stem!r}"},
        {"default_target": None},
        {"rules": [{"name": [], "action": {"move_to": "out"}}]},
        {"rules": [{"match": [], "action": {"move_to": "out"}}]},
        {"rules": [{"priority": 1.5, "action": {"move_to": "out"}}]},
        {"rules": [{"match": {"regex": "("}, "action": {"move_to": "out"}}]},
        {"rules": [{"action": {"move_to": "out", "rename_template": "../escape.txt"}}]},
        {"rules": [{"action": {"move_to": "out", "rename_template": ".."}}]},
        {
            "rules": [
                {"action": {"move_to": "out", "rename_template": "{stem:>{name}}"}}
            ]
        },
        {"rules": [{"action": {"move_to": "out", "rename_template": "{}"}}]},
    ],
)
def test_invalid_config_is_rejected_before_any_mutation(tmp_path, override):
    source = tmp_path / "input"
    source.mkdir()
    item = source / "a.txt"
    item.write_text("keep")
    config = dict(source=str(source), default_target="out") | override
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(config))
    assert not validate_config_file(path).ok
    with pytest.raises(ValueError, match="Invalid configuration"):
        run_pipeline(str(path))
    assert item.read_text() == "keep"
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("content", ["- a\n- b", "false", "1", "null", "a string"])
def test_non_mapping_yaml_is_rejected_cleanly(tmp_path, content):
    path = tmp_path / "rules.yaml"
    path.write_text(content)
    assert not validate_config_file(path).ok
    with pytest.raises(ValueError):
        run_pipeline(str(path))


def test_template_filename_cannot_expand_environment_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("FILEMINDR_ESCAPE", "outside")
    item = tmp_path / "$FILEMINDR_ESCAPE.txt"
    item.write_text("data")
    result = render_target_template("archive/{stem}", file=item, base_dir=tmp_path)
    assert result == tmp_path / "archive" / "$FILEMINDR_ESCAPE"


def test_extensionless_template_components_remain_supported(tmp_path):
    item = tmp_path / "README"
    item.write_text("data")
    result = render_target_template(
        "archive/{ext}/{stem}{suffix}", file=item, base_dir=tmp_path
    )
    assert result == tmp_path / "archive" / "README"


def test_implicit_default_target_preserves_literal_braces_in_source(tmp_path):
    source = tmp_path / "input-{literal}"
    source.mkdir()
    (source / "a.txt").write_text("data")
    config = tmp_path / "rules.yaml"
    config.write_text(yaml.safe_dump(dict(source=str(source))))
    assert run_pipeline(str(config))["moved"] == 1
    assert (source / "others" / "a.txt").read_text() == "data"


def test_dry_run_reserves_duplicate_destinations_without_creating_directories(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "a.txt").write_text("a")
    (source / "b.txt").write_text("b")
    config = dict(
        source=str(source),
        rules=[dict(action=dict(move_to="out", rename_template="same.txt"))],
    )
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(config))
    report = tmp_path / "report.json"
    run_pipeline(str(path), dry_run=True, report_path=report)
    planned = {
        Path(e["destination"]).name for e in json.loads(report.read_text())["events"]
    }
    assert planned == {"same.txt", "same (1).txt"}
    assert not (tmp_path / "out").exists()
    result = run_pipeline(str(path))
    assert result["moved"] == 2
    assert {p.name for p in (tmp_path / "out").iterdir()} == planned


def test_source_target_overlap_is_refused(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "a.txt").write_text("data")
    config = tmp_path / "rules.yaml"
    config.write_text(
        yaml.safe_dump(
            dict(
                source=str(source),
                default_target=str(source),
                conflict_policy="overwrite",
            )
        )
    )
    result = run_pipeline(str(config))
    assert result["errors"] == 1
    assert (source / "a.txt").read_text() == "data"


def test_existing_report_path_fails_before_processing(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    item = source / "a.txt"
    item.write_text("keep")
    config = tmp_path / "rules.yaml"
    config.write_text(yaml.safe_dump(dict(source=str(source), default_target="out")))
    with pytest.raises(FileExistsError):
        run_pipeline(str(config), report_path=item)
    assert item.read_text() == "keep"


def test_empty_and_inaccessible_scan(tmp_path, monkeypatch):
    source = tmp_path / "input"
    source.mkdir()
    config = tmp_path / "rules.yaml"
    config.write_text(yaml.safe_dump(dict(source=str(source), default_target="out")))
    assert run_pipeline(str(config))["files_scanned"] == 0
    real_iterdir = Path.iterdir

    def inaccessible(path):
        if path == source:
            raise PermissionError("directory is inaccessible")
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", inaccessible)
    with pytest.raises(PermissionError):
        run_pipeline(str(config))


def test_profile_remove_refuses_external_registry_path(
    tmp_path, fake_home, runner, cli_app
):
    outside = tmp_path / "valuable"
    outside.mkdir()
    (outside / "data.txt").write_text("keep")
    registry = fake_home / ".filemindr" / "profiles.yaml"
    registry.parent.mkdir()
    registry.write_text(yaml.safe_dump(dict(profiles=dict(home=str(outside)))))
    result = runner.invoke(cli_app, ["profile", "remove", "home", "--yes"])
    assert result.exit_code != 0
    assert (outside / "data.txt").read_text() == "keep"
    assert "home" in yaml.safe_load(registry.read_text())["profiles"]


def test_cli_operation_failure_marks_history_failed_and_returns_nonzero(
    tmp_path, fake_home, runner, cli_app, monkeypatch
):
    from filemindr.core import history

    source = tmp_path / "input"
    source.mkdir()
    item = source / "a.txt"
    item.write_text("keep")
    rules = fake_home / ".filemindr" / "rules" / "test"
    rules.mkdir(parents=True)
    (rules / "rules.yaml").write_text(
        yaml.safe_dump(dict(source=str(source), default_target="out"))
    )
    (rules.parent.parent / "profiles.yaml").write_text(
        yaml.safe_dump(dict(profiles=dict(test=str(rules))))
    )

    def fail(*args, **kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr("filemindr.core.runner.execute_operation", fail)
    report = tmp_path / "report.json"
    result = runner.invoke(cli_app, ["run", "-p", "test", "--report", str(report)])
    assert result.exit_code == 1
    assert history.iter_run_meta()[0]["status"] == "failed"
    assert json.loads(report.read_text())["summary"]["errors"] == 1
    assert item.read_text() == "keep"
