from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

from filemindr.core.config import expand_path
from filemindr.core.ignore import load_ignore_patterns, matches_ignore_pattern
from filemindr.core.operations import (
    POLICIES,
    destination_identity,
    same_file,
    snapshot,
)
from filemindr.core.templating import (
    render_name_template,
    render_target_template,
    template_fields,
)
from filemindr.core.validator import load_validated_config


@dataclass(frozen=True)
class Rule:
    name: str
    priority: int
    extensions: set[str]
    regex: re.Pattern | None
    older_than_days: int | None
    move_to: str | None
    copy_to: str | None
    rename_template: str | None
    conflict_policy: str | None


@dataclass(frozen=True)
class PlannedOperation:
    source: Path
    action: Literal["MOVE", "COPY", "SKIP"]
    status: Literal["planned", "skipped", "error"]
    rule_name: str
    rule_priority: int | None
    policy: str
    requested_destination: Path | None
    destination: Path | None
    reason: str | None
    error: str | None
    matched_rules: tuple[tuple[str, int], ...]
    target_template: str | None
    rename_template: str | None
    rendered_name: str
    source_snapshot: tuple[int, ...] | None
    expected_destination: dict[str, int | str] | None


def normalize_ext(ext: str) -> str:
    return ext.lower().lstrip(".")


def load_rules(config: dict[str, Any]) -> list[Rule]:
    rules: list[Rule] = []
    for item in config.get("rules") or []:
        match = item.get("match") or {}
        action = item.get("action") or {}
        if bool(action.get("move_to")) == bool(action.get("copy_to")):
            raise ValueError(
                f"Rule '{item.get('name', 'unnamed')}' must define exactly one of action.move_to or action.copy_to"
            )
        rules.append(
            Rule(
                name=item.get("name", "unnamed"),
                priority=int(item.get("priority", 0)),
                extensions={
                    normalize_ext(ext) for ext in match.get("extensions") or []
                },
                regex=re.compile(match["regex"]) if match.get("regex") else None,
                older_than_days=int(match["older_than_days"])
                if match.get("older_than_days") is not None
                else None,
                move_to=action.get("move_to"),
                copy_to=action.get("copy_to"),
                rename_template=action.get("rename_template"),
                conflict_policy=action.get("conflict_policy"),
            )
        )
    return sorted(rules, key=lambda rule: rule.priority, reverse=True)


def is_older_than(file: Path, days: int) -> bool:
    import time

    return time.time() - file.stat().st_mtime >= days * 86400


def matching_rules(file: Path, rules: list[Rule]) -> list[Rule]:
    ext = normalize_ext(file.suffix)
    matched = []
    for rule in rules:
        if rule.extensions and ext not in rule.extensions:
            continue
        if rule.regex and not rule.regex.search(file.name):
            continue
        if rule.older_than_days is not None and not is_older_than(
            file, rule.older_than_days
        ):
            continue
        matched.append(rule)
    return matched


class DestinationReservations:
    """Track planned file leaves and implicit parent directories in linear space."""

    def __init__(self) -> None:
        self.files: set[str] = set()
        self.directories: set[str] = set()

    @staticmethod
    def key(path: Path) -> str:
        return os.path.normcase(str(path))

    def has_file(self, path: Path) -> bool:
        return self.key(path) in self.files

    def has_directory(self, path: Path) -> bool:
        return self.key(path) in self.directories

    def reserve(self, path: Path) -> None:
        key = self.key(path)
        self.files.add(key)
        parent = path.parent
        while True:
            parent_key = self.key(parent)
            if parent_key in self.directories:
                break
            self.directories.add(parent_key)
            if parent.parent == parent:
                break
            parent = parent.parent


def _validate_destination_shape(
    destination: Path, reservations: DestinationReservations
) -> None:
    try:
        destination_info = destination.lstat()
    except FileNotFoundError:
        destination_info = None
    if destination_info is not None and (
        not stat.S_ISREG(destination_info.st_mode)
        or destination.is_symlink()
        or destination.is_junction()
    ):
        raise ValueError(f"Destination is not a regular file: {destination}")
    if reservations.has_directory(destination):
        raise ValueError(
            f"Destination conflicts with a planned directory: {destination}"
        )
    for parent in destination.parents:
        if reservations.has_file(parent):
            raise ValueError(
                f"Destination parent conflicts with a planned file: {parent}"
            )
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISDIR(info.st_mode)
            or parent.is_symlink()
            or parent.is_junction()
        ):
            raise ValueError(f"Destination parent is not a directory: {parent}")
        break


def resolve_conflict(
    destination: Path,
    policy: str,
    reservations: DestinationReservations | None = None,
) -> Path | None:
    policy = policy.lower()
    if policy not in POLICIES:
        raise ValueError(f"Invalid conflict policy: {policy}")
    reservations = reservations or DestinationReservations()
    _validate_destination_shape(destination, reservations)
    exists = destination.exists() or reservations.has_file(destination)
    if not exists:
        return destination
    if policy in {"overwrite", "trash"}:
        return destination
    if policy == "skip":
        return None
    index = 1
    while True:
        candidate = destination.with_name(
            f"{destination.stem} ({index}){destination.suffix}"
        )
        _validate_destination_shape(candidate, reservations)
        if not candidate.exists() and not reservations.has_file(candidate):
            return candidate
        index += 1


def resolve_destination(
    file: Path,
    rule: Rule | None,
    *,
    default_target: str,
    base_dir: Path | None = None,
    target_directory: Path | None = None,
    source_parent: Path | None = None,
) -> Path:
    template = (rule.copy_to or rule.move_to) if rule else default_target
    directory = target_directory or render_target_template(
        template, file=file, base_dir=base_dir
    )
    if directory == (source_parent or file.parent.resolve()):
        raise ValueError(
            "Destination directory must differ from source (prevents repeated watch processing)"
        )
    name = (
        render_name_template(rule.rename_template, file=file)
        if rule and rule.rename_template
        else file.name
    )
    destination = directory / name
    if destination.is_symlink():
        raise ValueError(f"Destination symlinks are unsupported: {destination}")
    if same_file(file, destination):
        raise ValueError("Source and destination refer to the same file")
    return destination


def _path_sort_key(path: Path) -> tuple[str, str]:
    value = str(path)
    return os.path.normcase(value), value


class ExecutionPlanner:
    """Shared, side-effect-free planner for execution, dry-run, and explain."""

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path.expanduser().resolve()
        self.config = load_validated_config(self.config_path)
        self.source = expand_path(
            self.config["source"], base_dir=self.config_path.parent
        )
        self.default_target = self.config.get(
            "default_target",
            str(self.source / "others").replace("{", "{{").replace("}", "}}"),
        )
        self.global_policy = str(self.config.get("conflict_policy", "rename")).lower()
        self.ignore_patterns = load_ignore_patterns(self.config)
        self.rules = load_rules(self.config)
        self.reservations = DestinationReservations()
        self._static_targets: dict[str, Path] = {}
        self._source_parents: dict[Path, Path] = {}

    def _target_directory(self, template: str, file: Path) -> Path:
        try:
            fields = template_fields(os.path.expandvars(template))
        except ValueError:
            return render_target_template(
                template, file=file, base_dir=self.config_path.parent
            )
        if fields:
            return render_target_template(
                template, file=file, base_dir=self.config_path.parent
            )
        if template not in self._static_targets:
            self._static_targets[template] = render_target_template(
                template, file=file, base_dir=self.config_path.parent
            )
        return self._static_targets[template]

    def _source_parent(self, file: Path) -> Path:
        if file.parent not in self._source_parents:
            self._source_parents[file.parent] = file.parent.resolve()
        return self._source_parents[file.parent]

    def plan_files(
        self,
        files: Iterable[Path],
        *,
        capture_destination_identity: bool = False,
    ) -> Iterable[PlannedOperation]:
        # Stable ordering makes collisions independent of filesystem enumeration order.
        ordered = list(files)
        parent = ordered[0].parent if ordered else None
        if parent is not None and all(file.parent == parent for file in ordered):
            ordered.sort(key=lambda file: file.name)
        else:
            ordered.sort(key=_path_sort_key)
        for file in ordered:
            yield self.plan_file(
                file, capture_destination_identity=capture_destination_identity
            )

    def plan_file(
        self, file: Path, *, capture_destination_identity: bool = False
    ) -> PlannedOperation:
        file = file.expanduser()
        if not file.is_absolute():
            file = file.absolute()
        source_snapshot: tuple[int, ...] | None = None
        matched: list[Rule] = []
        rule: Rule | None = None
        rule_name = "n/a"
        policy = self.global_policy
        action: Literal["MOVE", "COPY", "SKIP"] = "SKIP"
        target_template: str | None = None
        requested: Path | None = None
        rendered_name = file.name
        common = dict(
            source=file,
            rule_priority=None,
            requested_destination=None,
            destination=None,
            matched_rules=(),
            target_template=None,
            rename_template=None,
            rendered_name=rendered_name,
            source_snapshot=source_snapshot,
            expected_destination=None,
        )
        if file.is_symlink() or not file.exists() or not file.is_file():
            return PlannedOperation(
                **common,
                action="SKIP",
                status="skipped",
                rule_name="n/a",
                policy=self.global_policy,
                reason="not_found_or_not_file",
                error=None,
            )
        if matches_ignore_pattern(file, self.ignore_patterns):
            return PlannedOperation(
                **common,
                action="SKIP",
                status="skipped",
                rule_name="ignored",
                policy=self.global_policy,
                reason="ignored_by_pattern",
                error=None,
            )

        try:
            source_snapshot = snapshot(file)
            matched = matching_rules(file, self.rules)
            rule = matched[0] if matched else None
            rule_name = rule.name if rule else "default"
            policy = str(
                rule.conflict_policy
                if rule and rule.conflict_policy
                else self.global_policy
            ).lower()
            action = "COPY" if rule and rule.copy_to else "MOVE"
            target_template = (
                (rule.copy_to or rule.move_to) if rule else self.default_target
            )
            requested = resolve_destination(
                file,
                rule,
                default_target=self.default_target,
                base_dir=self.config_path.parent,
                target_directory=self._target_directory(target_template, file),
                source_parent=self._source_parent(file),
            )
            rendered_name = requested.name
            destination = resolve_conflict(requested, policy, self.reservations)
            if destination is None:
                return PlannedOperation(
                    **(
                        common
                        | {
                            "rule_priority": rule.priority if rule else None,
                            "requested_destination": requested,
                            "matched_rules": tuple(
                                (item.name, item.priority) for item in matched
                            ),
                            "target_template": target_template,
                            "rename_template": rule.rename_template if rule else None,
                            "source_snapshot": source_snapshot,
                        }
                    ),
                    action=action,
                    status="skipped",
                    rule_name=rule_name,
                    policy=policy,
                    reason="conflict_policy_skip",
                    error=None,
                )
            expected = (
                destination_identity(destination)
                if capture_destination_identity
                else None
            )
            plan = PlannedOperation(
                **(
                    common
                    | {
                        "rule_priority": rule.priority if rule else None,
                        "requested_destination": requested,
                        "destination": destination,
                        "matched_rules": tuple(
                            (item.name, item.priority) for item in matched
                        ),
                        "target_template": target_template,
                        "rename_template": rule.rename_template if rule else None,
                        "rendered_name": requested.name,
                        "source_snapshot": source_snapshot,
                        "expected_destination": expected,
                    }
                ),
                action=action,
                status="planned",
                rule_name=rule_name,
                policy=policy,
                reason=None,
                error=None,
            )
            self.reservations.reserve(destination)
            return plan
        except Exception as exc:
            context = common | {
                "rule_priority": rule.priority if rule else None,
                "requested_destination": requested,
                "matched_rules": tuple((item.name, item.priority) for item in matched),
                "target_template": target_template,
                "rename_template": rule.rename_template if rule else None,
                "rendered_name": rendered_name,
                "source_snapshot": source_snapshot,
            }
            return PlannedOperation(
                **context,
                action=action,
                status="error",
                rule_name=rule_name,
                policy=policy,
                reason="planning_error",
                error=str(exc),
            )
