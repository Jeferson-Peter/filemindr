from __future__ import annotations

import os
from pathlib import Path

import yaml


def expand_path(value: str | Path, *, base_dir: Path | None = None) -> Path:
    raw_text = os.path.expandvars(str(value))

    if raw_text == "~":
        raw = Path.home()
    elif raw_text.startswith("~/") or raw_text.startswith("~\\"):
        raw = Path.home() / raw_text[2:]
    else:
        raw = Path(raw_text).expanduser()

    if not raw.is_absolute() and base_dir is not None:
        raw = base_dir / raw
    return raw.resolve()


def _expand(value: str | Path, *, base_dir: Path | None = None) -> Path:
    return expand_path(value, base_dir=base_dir)


def resolve_profile_config(profile: str) -> Path:
    base = expand_path("~/.filemindr")
    profiles_file = base / "profiles.yaml"

    if not profiles_file.exists():
        raise FileNotFoundError("profiles.yaml not found in ~/.filemindr | Run: filemindr profile init <name>")

    data = yaml.safe_load(profiles_file.read_text()) or {}
    profiles = data.get("profiles", {})

    if profile not in profiles:
        raise ValueError(f"Profile '{profile}' not defined")

    rules_dir = expand_path(profiles[profile])
    if not rules_dir.exists():
        raise FileNotFoundError(rules_dir)

    rules_yaml = rules_dir / "rules.yaml"

    if not rules_yaml.exists():
        raise FileNotFoundError(rules_yaml)

    return rules_yaml