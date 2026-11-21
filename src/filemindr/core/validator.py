from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from filemindr.core.config import expand_path
from filemindr.core.operations import POLICIES
from filemindr.core.templating import (
    invalid_template_fields,
    template_syntax_error,
    validate_file_name,
)

ALLOWED_POLICIES = POLICIES


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: list[str]


def validate_config_file(config_path: Path) -> ValidationResult:
    if not config_path.exists():
        return ValidationResult(ok=False, errors=[f"Config not found: {config_path}"])

    try:
        raw = config_path.read_text(encoding="utf-8")
    except Exception as e:
        return ValidationResult(
            ok=False, errors=[f"Failed to read config as UTF-8: {e}"]
        )

    try:
        config = yaml.safe_load(raw)
    except Exception as e:
        return ValidationResult(ok=False, errors=[f"Invalid YAML: {e}"])

    return validate_config(config, base_dir=config_path.parent.resolve())


def load_validated_config(config_path: Path) -> dict[str, Any]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    result = validate_config(config, base_dir=config_path.parent.resolve())
    if not result.ok:
        raise ValueError("Invalid configuration: " + "; ".join(result.errors))
    return config


def validate_config(config: Any, *, base_dir: Path) -> ValidationResult:
    errors: list[str] = []
    if not isinstance(config, dict):
        return ValidationResult(ok=False, errors=["Config must be an object"])

    source_raw = config.get("source")
    if not source_raw or not isinstance(source_raw, str):
        errors.append("Missing required field: source (string)")
        return ValidationResult(ok=False, errors=errors)

    try:
        source_dir = expand_path(source_raw, base_dir=base_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        return ValidationResult(ok=False, errors=[f"Invalid source: {exc}"])
    if not source_dir.is_dir():
        errors.append(f"source directory does not exist: {source_dir}")

    default_target = config.get("default_target")
    if "default_target" in config:
        if (
            not isinstance(default_target, str)
            or not default_target.strip()
            or "\x00" in default_target
        ):
            errors.append("default_target must be a non-empty string")
        else:
            syntax_error = template_syntax_error(default_target)
            if syntax_error:
                errors.append(
                    f"default_target has invalid template syntax: {syntax_error}"
                )
            elif invalid_template_fields(default_target):
                errors.append("default_target uses unsupported template fields")

    global_policy = str(config.get("conflict_policy", "rename")).lower()
    if global_policy not in ALLOWED_POLICIES:
        errors.append(
            f"Invalid global conflict_policy='{global_policy}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_POLICIES))}"
        )

    ignore_cfg = config.get("ignore", [])
    if ignore_cfg is not None:
        if not isinstance(ignore_cfg, list):
            errors.append("Field 'ignore' must be a list")
        else:
            for item in ignore_cfg:
                if not isinstance(item, str):
                    errors.append(
                        f"Field 'ignore' must contain only strings (got {type(item).__name__})"
                    )
                    break

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
        if "name" in r and (not isinstance(r["name"], str) or not r["name"].strip()):
            errors.append(f"rules[{i}].name must be a non-empty string")
        if not isinstance(name, str):
            name = f"rules[{i}]"

        prio = r.get("priority", 0)
        try:
            if isinstance(prio, bool) or int(prio) != float(prio):
                raise ValueError("not an integer")
        except Exception:
            errors.append(f"Rule '{name}': priority must be an integer")

        match = r.get("match", {})
        if match is None:
            match = {}
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
                if isinstance(older, bool) or older_i != float(older):
                    raise ValueError("not an integer")
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
                        errors.append(
                            f"Rule '{name}': extension must be a string (got {type(e).__name__})"
                        )
                        break

        action = r.get("action", {})
        if not isinstance(action, dict):
            errors.append(f"Rule '{name}': action must be an object")
            action = {}

        move_to = action.get("move_to")
        copy_to = action.get("copy_to")
        rename_template = action.get("rename_template")

        if bool(move_to) == bool(copy_to):
            errors.append(
                f"Rule '{name}': must define exactly one of action.move_to or action.copy_to"
            )

        for field_name, value in (
            ("action.move_to", move_to),
            ("action.copy_to", copy_to),
        ):
            if value is not None and not isinstance(value, str):
                errors.append(f"Rule '{name}': {field_name} must be a string")
                continue

            if isinstance(value, str):
                if not value.strip() or "\x00" in value:
                    errors.append(
                        f"Rule '{name}': {field_name} must be a non-empty path"
                    )
                syntax_error = template_syntax_error(value)
                if syntax_error:
                    errors.append(
                        f"Rule '{name}': {field_name} has invalid template syntax: {syntax_error}"
                    )
                    continue

                invalid = sorted(invalid_template_fields(value))
                if invalid:
                    errors.append(
                        f"Rule '{name}': {field_name} uses unsupported template fields: {', '.join(invalid)}"
                    )

        if rename_template is not None:
            if not isinstance(rename_template, str):
                errors.append(f"Rule '{name}': action.rename_template must be a string")
            else:
                syntax_error = template_syntax_error(rename_template)
                if syntax_error:
                    errors.append(
                        f"Rule '{name}': action.rename_template has invalid template syntax: {syntax_error}"
                    )
                    continue

                invalid = sorted(invalid_template_fields(rename_template))
                if invalid:
                    errors.append(
                        f"Rule '{name}': action.rename_template uses unsupported template fields: {', '.join(invalid)}"
                    )

                if "/" in rename_template or "\\" in rename_template:
                    errors.append(
                        f"Rule '{name}': action.rename_template must only define a file name, not a path"
                    )
                if not invalid and not template_syntax_error(rename_template):
                    try:
                        validate_file_name(
                            rename_template.format_map(
                                {
                                    "name": "example.txt",
                                    "stem": "example",
                                    "stem_safe": "example",
                                    "suffix": ".txt",
                                    "ext": "txt",
                                    "parent": "input",
                                    "yyyy": "2026",
                                    "mm": "11",
                                    "dd": "21",
                                }
                            )
                        )
                    except ValueError as exc:
                        errors.append(f"Rule '{name}': action.rename_template: {exc}")

        rule_policy = action.get("conflict_policy")
        if rule_policy is not None:
            rp = str(rule_policy).lower()
            if rp not in ALLOWED_POLICIES:
                errors.append(
                    f"Rule '{name}': invalid conflict_policy='{rp}'. "
                    f"Allowed: {', '.join(sorted(ALLOWED_POLICIES))}"
                )

    return ValidationResult(ok=(len(errors) == 0), errors=errors)
