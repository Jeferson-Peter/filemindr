from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from filemindr.core.config import expand_path
from filemindr.core.ignore import load_ignore_patterns, matches_ignore_pattern
from filemindr.core.runner import (
    Rule,
    _load_rules,
    _match_rule,
    _resolve_destination,
    _resolve_conflict,
    _normalize_ext,
    _is_older_than,
)


@dataclass(frozen=True)
class ExplainResult:
    file: Path
    action: str
    rule_name: str
    rule_priority: int | None
    policy: str
    dest: Path
    resolved: Path | None
    matched_rules: list[str]
    target_template: str | None
    rename_template: str | None
    rendered_name: str
    reason: str | None


def _load_config(config_path: str) -> tuple[Path, dict[str, Any]]:
    cfg_path = Path(config_path).expanduser().resolve()
    if not cfg_path.exists():
        raise FileNotFoundError(config_path)
    return cfg_path, yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}


def _rule_matches(file: Path, rule: Rule) -> bool:
    ext = _normalize_ext(file.suffix)
    filename = file.name

    if rule.extensions and ext not in rule.extensions:
        return False
    if rule.regex and not rule.regex.search(filename):
        return False
    if rule.older_than_days is not None and not _is_older_than(file, rule.older_than_days):
        return False
    return True


def _matching_rules(file: Path, rules: list[Rule]) -> list[Rule]:
    return [rule for rule in rules if _rule_matches(file, rule)]


def explain_files(
    config_path: str,
    files: list[Path],
    *,
    verbose: bool = False,
) -> list[ExplainResult]:
    """
    Explain what would happen to each file. Does NOT move/copy anything.
    """
    cfg_path, config = _load_config(config_path)
    base_dir = cfg_path.parent

    source = expand_path(config["source"], base_dir=base_dir)
    default_target = str(config.get("default_target", str(source / "others")))
    global_policy = str(config.get("conflict_policy", "rename")).lower()
    ignore_patterns = load_ignore_patterns(config)

    rules = _load_rules(config, base_dir=base_dir)

    results: list[ExplainResult] = []

    for f in files:
        f = f.expanduser().resolve()

        if not f.exists() or not f.is_file():
            results.append(
                ExplainResult(
                    file=f,
                    action="SKIP",
                    rule_name="n/a",
                    rule_priority=None,
                    policy=global_policy,
                    dest=f,
                    resolved=None,
                    matched_rules=[],
                    target_template=None,
                    rename_template=None,
                    rendered_name=f.name,
                    reason="not_found_or_not_file" if verbose else None,
                )
            )
            continue

        if matches_ignore_pattern(f, ignore_patterns):
            results.append(
                ExplainResult(
                    file=f,
                    action="SKIP",
                    rule_name="ignored",
                    rule_priority=None,
                    policy=global_policy,
                    dest=f,
                    resolved=None,
                    matched_rules=[],
                    target_template=None,
                    rename_template=None,
                    rendered_name=f.name,
                    reason="ignored_by_pattern" if verbose else None,
                )
            )
            continue

        matched_rules = _matching_rules(f, rules)
        rule = _match_rule(f, rules)

        rule_name = rule.name if rule else "default"
        rule_priority = rule.priority if rule else None

        action = "COPY" if (rule and rule.copy_to) else "MOVE"

        policy = (rule.conflict_policy if rule and rule.conflict_policy else global_policy).lower()

        dest = _resolve_destination(f, rule, default_target=default_target, base_dir=base_dir)
        resolved = _resolve_conflict(dest, policy)
        target_template = (rule.copy_to or rule.move_to) if rule else default_target
        rename_template = rule.rename_template if rule else None
        rendered_name = dest.name

        reason = None
        if verbose:
            parts: list[str] = []
            parts.append(f"source={source}")
            parts.append(f"ext={f.suffix.lower() or '-'}")

            if rule:
                parts.append(f"matched_rule={rule.name}")
                parts.append(
                    "matched_candidates="
                    + ", ".join(f"{item.name}(prio={item.priority})" for item in matched_rules)
                )
                if rule.regex:
                    parts.append("regex=YES")
                if rule.older_than_days is not None:
                    parts.append(f"older_than_days={rule.older_than_days}")
                parts.append(f"target_template={target_template}")
                if rename_template:
                    parts.append(f"rename_template={rename_template}")
                    parts.append(f"rendered_name={rendered_name}")
            else:
                parts.append("matched_rule=default")
                parts.append("matched_candidates=default")
                parts.append(f"target_template={target_template}")
                parts.append(f"rendered_name={rendered_name}")

            if dest.exists():
                parts.append("dest_exists=YES")
            else:
                parts.append("dest_exists=NO")

            if resolved is None:
                parts.append("conflict=skip")
            elif resolved != dest:
                parts.append(f"conflict=rename -> {resolved.name}")
            else:
                if policy in {"overwrite", "trash"} and dest.exists():
                    parts.append(f"conflict={policy}")
                else:
                    parts.append("conflict=none")

            reason = " ".join(parts)

        results.append(
            ExplainResult(
                file=f,
                action=action,
                rule_name=rule_name,
                rule_priority=rule_priority,
                policy=policy,
                dest=dest,
                resolved=resolved,
                matched_rules=[f"{item.name}(prio={item.priority})" for item in matched_rules],
                target_template=target_template,
                rename_template=rename_template,
                rendered_name=rendered_name,
                reason=reason,
            )
        )

    return results


def format_explain(result: ExplainResult, *, fmt: str = "one", verbose: bool = False) -> str:
    """
    fmt:
      - one:  <FILE> -> <DEST> [ACTION] rule=... prio=... policy=... (optional reason)
      - short: ACTION <FILE> -> <DEST> (rule=...)
    """
    file_name = result.file.name

    if result.action == "SKIP":
        base = f"{file_name} [SKIP]"
        if verbose and result.reason:
            base += f" ({result.reason})"
        return base

    if result.resolved is None:
        dest_str = str(result.dest)
        base = f"{file_name} -> {dest_str} [SKIP] rule={result.rule_name} prio={result.rule_priority or '-'} policy={result.policy}"
        if verbose and result.reason:
            base += f" ({result.reason})"
        return base

    dest_str = str(result.resolved)

    if fmt == "short":
        base = f"{result.action} {file_name} -> {dest_str} (rule={result.rule_name})"
        if verbose and result.reason:
            base += f" ({result.reason})"
        return base

    base = (
        f"{file_name} -> {dest_str} "
        f"[{result.action}] rule={result.rule_name} prio={result.rule_priority or '-'} policy={result.policy}"
    )
    if verbose and result.reason:
        base += f" ({result.reason})"
    return base
