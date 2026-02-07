from pathlib import Path
import os
import yaml


def _expand(p):
    return Path(os.path.expandvars(p)).expanduser().resolve()


def resolve_profile_config(profile: str) -> Path:
    base = _expand("~/.filemindr")
    profiles_file = base / "profiles.yaml"

    if not profiles_file.exists():
        raise FileNotFoundError("profiles.yaml not found in ~/.filemindr | Run: filemindr profile init <name>")

    data = yaml.safe_load(profiles_file.read_text()) or {}
    profiles = data.get("profiles", {})

    if profile not in profiles:
        raise ValueError(f"Profile '{profile}' not defined")

    rules_dir = _expand(profiles[profile])
    rules_dir.mkdir(parents=True, exist_ok=True)

    rules_yaml = rules_dir / "rules.yaml"

    if not rules_yaml.exists():
        raise FileNotFoundError(rules_yaml)

    return rules_yaml
