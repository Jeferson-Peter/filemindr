from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from filemindr.core.config import expand_path

ALLOWED_POLICIES = {"rename", "skip", "overwrite", "trash"}


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: list[str]


def validate_config_file(config_path: Path) -> ValidationResult:
    errors: list[str] = []

    if not config_path.exists():
        return ValidationResult(ok=False, errors=[f"Config not found: {config_path}"])

    try:
        raw = config_path.read_text(encoding="utf-8")
    except Exception as e:
        return ValidationResult(ok=False, errors=[f"Failed to read config as UTF-8: {e}"])

    try:
        config = yaml.safe_load(raw) or {}
    except Exception as e:
        return ValidationResult(ok=False, errors=[f"Invalid YAML: {e}"])

    base_dir = config_path.parent.resolve()

    source_raw = config.get("source")
    if not source_raw or not isinstance(source_raw, str):
        errors.append("Missing required field: source (string)")
        return ValidationResult(ok=False, errors=errors)

    source_dir = expand_path(source_raw, base_dir=base_dir)
    if not source_dir.exists():
        errors.append(f"source directory does not exist: {source_dir}")

    global_policy = str(config.get("conflict_policy", "rename")).lower()
    if global_policy not in ALLOWED_POLICIES:
        errors.append(
            f"Invalid global conflict_policy='{global_policy}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_POLICIES))}"
        )

    rules_cfg = config.get("rules", [])
    if rules_cfg is None:
        rules_cfg = []

    if not isinstance(rules_cfg, list):
        errors.append("Field 'rules' must be a list")
        return ValidationResult(ok=False, errors=errors)

    for i, r in enumerate(rules_cfg):
        if not isinstance(r, dict):
            errors.append(f"rules[{i}] must be an object")
            continue

        name = r.get("name") or f"rules[{i}]"
        if not isinstance(name, str):
            name = f"rules[{i}]"

        prio = r.get("priority", 0)
        try:
            int(prio)
        except Exception:
            errors.append(f"Rule '{name}': priority must be an integer")

        match = r.get("match", {}) or {}
        if not isinstance(match, dict):
            errors.append(f"Rule '{name}': match must be an object")
            match = {}

        regex_raw = match.get("regex")
        if regex_raw is not None:
            if not isinstance(regex_raw, str):
                errors.append(f"Rule '{name}': match.regex must be a string")
            else:
                try:
                    re.compile(regex_raw)
                except Exception as e:
                    errors.append(f"Rule '{name}': invalid regex '{regex_raw}': {e}")

        older = match.get("older_than_days")
        if older is not None:
            try:
                older_i = int(older)
                if older_i < 0:
                    errors.append(f"Rule '{name}': older_than_days must be >= 0")
            except Exception:
                errors.append(f"Rule '{name}': older_than_days must be an integer")

        exts = match.get("extensions", [])
        if exts is not None:
            if not isinstance(exts, list):
                errors.append(f"Rule '{name}': match.extensions must be a list")
            else:
                for e in exts:
                    if not isinstance(e, str):
                        errors.append(f"Rule '{name}': extension must be a string (got {type(e).__name__})")
                        break

        action = r.get("action", {}) or {}
        if not isinstance(action, dict):
            errors.append(f"Rule '{name}': action must be an object")
            action = {}

        move_to = action.get("move_to")
        copy_to = action.get("copy_to")

        if bool(move_to) == bool(copy_to):
            errors.append(
                f"Rule '{name}': must define exactly one of action.move_to or action.copy_to"
            )

        rule_policy = action.get("conflict_policy")
        if rule_policy is not None:
            rp = str(rule_policy).lower()
            if rp not in ALLOWED_POLICIES:
                errors.append(
                    f"Rule '{name}': invalid conflict_policy='{rp}'. "
                    f"Allowed: {', '.join(sorted(ALLOWED_POLICIES))}"
                )

    return ValidationResult(ok=(len(errors) == 0), errors=errors)