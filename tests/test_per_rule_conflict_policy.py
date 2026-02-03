from __future__ import annotations

from pathlib import Path
import yaml

from filemindr.core.runner import run_pipeline


def write_cfg(path: Path, cfg: dict) -> None:
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def test_rule_conflict_policy_overrides_global_rename(tmp_path: Path):
    source = tmp_path / "Downloads"
    images = tmp_path / "images"
    source.mkdir()
    images.mkdir()

    (source / "pic.jpg").write_text("NEW", encoding="utf-8")

    (images / "pic.jpg").write_text("OLD", encoding="utf-8")

    cfg = {
        "source": str(source),
        "default_target": str(source / "others"),
        "conflict_policy": "rename",
        "rules": [
            {
                "name": "images",
                "priority": 10,
                "match": {"extensions": ["jpg"]},
                "action": {
                    "move_to": str(images),
                    "conflict_policy": "overwrite",
                },
            }
        ],
    }

    cfg_path = tmp_path / "filemindr.yaml"
    write_cfg(cfg_path, cfg)

    run_pipeline(str(cfg_path), dry_run=False)

    assert (images / "pic.jpg").read_text(encoding="utf-8") == "NEW"
    assert not (source / "pic.jpg").exists()


def test_rule_without_policy_uses_global(tmp_path: Path):
    source = tmp_path / "Downloads"
    docs = tmp_path / "docs"
    source.mkdir()
    docs.mkdir()

    (source / "a.txt").write_text("NEW", encoding="utf-8")
    (docs / "a.txt").write_text("OLD", encoding="utf-8")

    cfg = {
        "source": str(source),
        "default_target": str(source / "others"),
        "conflict_policy": "rename",
        "rules": [
            {
                "name": "docs",
                "priority": 10,
                "match": {"extensions": ["txt"]},
                "action": {
                    "move_to": str(docs),
                },
            }
        ],
    }

    cfg_path = tmp_path / "filemindr.yaml"
    write_cfg(cfg_path, cfg)

    # Act
    run_pipeline(str(cfg_path), dry_run=False)

    assert (docs / "a.txt").read_text(encoding="utf-8") == "OLD"
    assert (docs / "a (1).txt").read_text(encoding="utf-8") == "NEW"
    assert not (source / "a.txt").exists()