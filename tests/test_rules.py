from pathlib import Path
import yaml

from filemindr.core.runner import _load_rules, _match_rule


def test_regex_wins_with_higher_priority(tmp_path: Path):
    config = {
        "rules": [
            {
                "name": "documents",
                "priority": 10,
                "match": {"extensions": ["pdf"]},
                "action": {"move_to": str(tmp_path / "docs")},
            },
            {
                "name": "invoices",
                "priority": 100,
                "match": {"extensions": ["pdf"], "regex": "(?i)invoice|nota"},
                "action": {"move_to": str(tmp_path / "invoices")},
            },
        ]
    }

    rules = _load_rules(config)

    f = tmp_path / "invoice_123.pdf"
    f.write_text("x")

    rule = _match_rule(f, rules)
    assert rule is not None
    assert rule.name == "invoices"