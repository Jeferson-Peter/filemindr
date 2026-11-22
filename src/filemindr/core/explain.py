from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from filemindr.core.planning import ExecutionPlanner, PlannedOperation


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


def _explain_result(
    item: PlannedOperation, *, verbose: bool, source: Path
) -> ExplainResult:
    dest = item.requested_destination or item.source
    if item.status == "error":
        action = "ERROR"
        resolved = None
        reason = item.error
    elif item.status == "skipped" and item.action == "SKIP":
        action = "SKIP"
        resolved = None
        reason = item.reason if verbose else None
    else:
        action = item.action
        resolved = item.destination
        reason = None

    if verbose and action not in {"SKIP", "ERROR"}:
        parts = [f"source={source}", f"ext={item.source.suffix.lower() or '-'}"]
        if item.matched_rules:
            parts.extend(
                (
                    f"matched_rule={item.rule_name}",
                    "matched_candidates="
                    + ", ".join(
                        f"{name}(prio={priority})"
                        for name, priority in item.matched_rules
                    ),
                )
            )
        else:
            parts.extend(("matched_rule=default", "matched_candidates=default"))
        parts.append(f"target_template={item.target_template}")
        if item.rename_template:
            parts.extend(
                (
                    f"rename_template={item.rename_template}",
                    f"rendered_name={item.rendered_name}",
                )
            )
        parts.append(f"dest_exists={'YES' if dest.exists() else 'NO'}")
        if item.reason == "conflict_policy_skip":
            parts.append("conflict=skip")
        elif resolved is not None and resolved != dest:
            parts.append(f"conflict=rename -> {resolved.name}")
        elif item.policy in {"overwrite", "trash"} and dest.exists():
            parts.append(f"conflict={item.policy}")
        else:
            parts.append("conflict=none")
        reason = " ".join(parts)

    return ExplainResult(
        file=item.source,
        action=action,
        rule_name=item.rule_name,
        rule_priority=item.rule_priority,
        policy=item.policy,
        dest=dest,
        resolved=resolved,
        matched_rules=[
            f"{name}(prio={priority})" for name, priority in item.matched_rules
        ],
        target_template=item.target_template,
        rename_template=item.rename_template,
        rendered_name=item.rendered_name,
        reason=reason,
    )


def explain_files(
    config_path: str,
    files: list[Path],
    *,
    verbose: bool = False,
) -> list[ExplainResult]:
    """Explain planned operations without changing the filesystem."""
    planner = ExecutionPlanner(Path(config_path))
    return [
        _explain_result(item, verbose=verbose, source=planner.source)
        for item in planner.plan_files(files)
    ]


def format_explain(
    result: ExplainResult, *, fmt: str = "one", verbose: bool = False
) -> str:
    file_name = result.file.name
    if result.action == "SKIP":
        base = f"{file_name} [SKIP]"
        if verbose and result.reason:
            base += f" ({result.reason})"
        return base
    if result.action == "ERROR":
        return (
            f"{file_name} -> {result.dest} [ERROR] {result.reason or 'planning failed'}"
        )
    if result.resolved is None:
        base = (
            f"{file_name} -> {result.dest} [SKIP] rule={result.rule_name} "
            f"prio={result.rule_priority or '-'} policy={result.policy}"
        )
        if verbose and result.reason:
            base += f" ({result.reason})"
        return base
    if fmt == "short":
        base = f"{result.action} {file_name} -> {result.resolved} (rule={result.rule_name})"
        if verbose and result.reason:
            base += f" ({result.reason})"
        return base
    base = (
        f"{file_name} -> {result.resolved} [{result.action}] "
        f"rule={result.rule_name} prio={result.rule_priority or '-'} policy={result.policy}"
    )
    if verbose and result.reason:
        base += f" ({result.reason})"
    return base
