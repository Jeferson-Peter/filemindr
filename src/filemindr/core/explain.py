from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from filemindr.core.runner import (
    _p,
    _load_rules,
    _match_rule,
    _resolve_conflict,
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
    reason: str | None


def _load_config(config_path: str) -> dict[str, Any]:
    cfg_path = Path(config_path)
    if not cfg_path.exists():
        raise FileNotFoundError(config_path)
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}


def explain_files(
    config_path: str,
    files: list[Path],
    *,
    verbose: bool = False,
) -> list[ExplainResult]:
    """
    Explain what would happen to each file. Does NOT move/copy anything.
    """
    config = _load_config(config_path)

    source = _p(config["source"]).resolve()
    default_target = _p(config.get("default_target", str(source / "others"))).resolve()
    global_policy = str(config.get("conflict_policy", "rename")).lower()

    rules = _load_rules(config)

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
                    reason="not_found_or_not_file" if verbose else None,
                )
            )
            continue

        rule = _match_rule(f, rules)

        rule_name = rule.name if rule else "default"
        rule_priority = rule.priority if rule else None

        dest_dir = (rule.copy_to or rule.move_to) if rule else default_target
        dest_dir = dest_dir.resolve()

        action = "COPY" if (rule and rule.copy_to) else "MOVE"

        policy = (rule.conflict_policy if rule and rule.conflict_policy else global_policy).lower()

        dest = dest_dir / f.name
        resolved = _resolve_conflict(dest, policy)

        reason = None
        if verbose:
            parts: list[str] = []
            parts.append(f"source={source}")
            parts.append(f"ext={f.suffix.lower() or '-'}")

            if rule:
                parts.append(f"matched_rule={rule.name}")
                if rule.regex:
                    parts.append("regex=YES")
                if rule.older_than_days is not None:
                    parts.append(f"older_than_days={rule.older_than_days}")
            else:
                parts.append("matched_rule=default")

            if dest.exists():
                parts.append("dest_exists=YES")
            else:
                parts.append("dest_exists=NO")

            if resolved is None:
                parts.append("conflict=skip")
            elif resolved != dest:
                parts.append(f"conflict=rename -> {resolved.name}")
            else:
                # overwrite/trash keep same dest
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
