import os
from pathlib import Path
import yaml

from filemindr.core.runner import run_pipeline


def test_dry_run_does_not_move_files(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.pdf").write_text("x")

    cfg = {
        "source": str(src),
        "default_target": str(tmp_path / "others"),
        "conflict_policy": "rename",
        "rules": [
            {
                "name": "documents",
                "priority": 10,
                "match": {"extensions": ["pdf"]},
                "action": {"move_to": str(tmp_path / "docs")},
            }
        ],
    }

    cfg_path = tmp_path / "filemindr.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))

    run_pipeline(str(cfg_path), dry_run=True)

    assert (src / "a.pdf").exists()
    assert not (tmp_path / "docs" / "a.pdf").exists()

def test_summary_distinguishes_moves_and_copies(tmp_path: Path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.pdf").write_text("pdf", encoding="utf-8")
    (src / "b.png").write_text("png", encoding="utf-8")

    cfg = {
        "source": str(src),
        "default_target": str(tmp_path / "others"),
        "conflict_policy": "rename",
        "rules": [
            {
                "name": "documents",
                "priority": 20,
                "match": {"extensions": ["pdf"]},
                "action": {"move_to": str(tmp_path / "docs")},
            },
            {
                "name": "images",
                "priority": 10,
                "match": {"extensions": ["png"]},
                "action": {"copy_to": str(tmp_path / "images")},
            },
        ],
    }

    cfg_path = tmp_path / "filemindr.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    messages: list[str] = []

    monkeypatch.setattr("filemindr.core.runner.logger.info", lambda message: messages.append(str(message)))
    monkeypatch.setattr("filemindr.core.runner.logger.debug", lambda message: None)

    run_pipeline(str(cfg_path), dry_run=False)

    assert "Moved: 1" in messages
    assert "Copied: 1" in messages


def test_dry_run_summary_distinguishes_planned_moves_and_copies(tmp_path: Path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.pdf").write_text("pdf", encoding="utf-8")
    (src / "b.png").write_text("png", encoding="utf-8")

    cfg = {
        "source": str(src),
        "default_target": str(tmp_path / "others"),
        "conflict_policy": "rename",
        "rules": [
            {
                "name": "documents",
                "priority": 20,
                "match": {"extensions": ["pdf"]},
                "action": {"move_to": str(tmp_path / "docs")},
            },
            {
                "name": "images",
                "priority": 10,
                "match": {"extensions": ["png"]},
                "action": {"copy_to": str(tmp_path / "images")},
            },
        ],
    }

    cfg_path = tmp_path / "filemindr.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    messages: list[str] = []

    monkeypatch.setattr("filemindr.core.runner.logger.info", lambda message: messages.append(str(message)))
    monkeypatch.setattr("filemindr.core.runner.logger.debug", lambda message: None)

    run_pipeline(str(cfg_path), dry_run=True)

    assert "Planned moves: 1" in messages
    assert "Planned copies: 1" in messages


def test_pipeline_renders_target_and_rename_templates(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()

    file = src / "report.PDF"
    file.write_text("pdf", encoding="utf-8")
    os.utime(file, (1715904000, 1715904000))

    cfg = {
        "source": str(src),
        "default_target": str(tmp_path / "others"),
        "rules": [
            {
                "name": "documents",
                "priority": 20,
                "match": {"extensions": ["pdf"]},
                "action": {
                    "move_to": str(tmp_path / "organized" / "{ext}" / "{yyyy}" / "{mm}"),
                    "rename_template": "{stem}_{yyyy}-{mm}{suffix}",
                },
            },
        ],
    }

    cfg_path = tmp_path / "filemindr.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    run_pipeline(str(cfg_path), dry_run=False)

    expected = tmp_path / "organized" / "pdf" / "2024" / "05" / "report_2024-05.PDF"
    assert expected.exists()
    assert not file.exists()


def test_pipeline_renders_stem_safe_template(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()

    file = src / "Day Trade 2025 (Final).pdf"
    file.write_text("pdf", encoding="utf-8")
    os.utime(file, (1715904000, 1715904000))

    cfg = {
        "source": str(src),
        "default_target": str(tmp_path / "others"),
        "rules": [
            {
                "name": "documents",
                "priority": 20,
                "match": {"extensions": ["pdf"]},
                "action": {
                    "move_to": str(tmp_path / "organized"),
                    "rename_template": "{stem_safe}_{yyyy}-{mm}{suffix}",
                },
            },
        ],
    }

    cfg_path = tmp_path / "filemindr.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    run_pipeline(str(cfg_path), dry_run=False)

    expected = tmp_path / "organized" / "day-trade-2025-final_2024-05.pdf"
    assert expected.exists()
    assert not file.exists()
