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

    # arquivo continua na origem
    assert (src / "a.pdf").exists()
    # destino não deve ter o arquivo (dry-run)
    assert not (tmp_path / "docs" / "a.pdf").exists()