import os
import time
from pathlib import Path

from filemindr.core.runner import _load_rules, _match_rule


def test_older_than_days(tmp_path: Path):
    config = {
        "rules": [
            {
                "name": "old_installers",
                "priority": 50,
                "match": {"extensions": ["exe"], "older_than_days": 1},
                "action": {"move_to": str(tmp_path / "old")},
            }
        ]
    }
    rules = _load_rules(config)

    f = tmp_path / "setup.exe"
    f.write_text("x")

    # deixa o arquivo com mtime de 2 dias atrás
    two_days_ago = time.time() - (2 * 24 * 60 * 60)
    os.utime(f, (two_days_ago, two_days_ago))

    rule = _match_rule(f, rules)
    assert rule is not None
    assert rule.name == "old_installers"