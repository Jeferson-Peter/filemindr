from __future__ import annotations

import re
import shutil
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from loguru import logger


def _p(value: str) -> Path:
    return Path(value).expanduser()


@dataclass(frozen=True)
class Rule:
    name: str
    priority: int
    extensions: set[str]
    regex: re.Pattern | None
    older_than_days: int | None
    move_to: Path | None
    copy_to: Path | None
    conflict_policy: str | None

def _trash(path: Path) -> None:
    """
    Send a file to the OS trash/recycle bin.
    Falls back to unlink if trash is not available.
    """
    try:
        from send2trash import send2trash
        send2trash(str(path))
    except Exception:
        path.unlink(missing_ok=True)


def _normalize_ext(ext: str) -> str:
    return ext.lower().lstrip(".")


def _load_rules(config: dict[str, Any]) -> list[Rule]:
    rules_cfg = config.get("rules", [])
    rules: list[Rule] = []

    for r in rules_cfg:
        name = r.get("name", "unnamed")
        priority = int(r.get("priority", 0))

        match = r.get("match", {}) or {}
        exts = {_normalize_ext(e) for e in match.get("extensions", [])}
        regex_raw = match.get("regex")
        regex = re.compile(regex_raw) if regex_raw else None
        older = match.get("older_than_days")
        older_than_days = int(older) if older is not None else None

        action = r.get("action", {}) or {}
        move_to_str = action.get("move_to")
        copy_to_str = action.get("copy_to")

        rule_policy = action.get("conflict_policy")

        if bool(move_to_str) == bool(copy_to_str):
            raise ValueError(
                f"Rule '{name}' must define exactly one of action.move_to or action.copy_to"
            )

        rules.append(
            Rule(
                name=name,
                priority=priority,
                extensions=exts,
                regex=regex,
                older_than_days=older_than_days,
                move_to=_p(move_to_str) if move_to_str else None,
                copy_to=_p(copy_to_str) if copy_to_str else None,
                conflict_policy=str(rule_policy) if rule_policy else None,
            )
        )

    rules.sort(key=lambda x: x.priority, reverse=True)
    return rules


def _is_older_than(file: Path, days: int) -> bool:
    cutoff_seconds = days * 24 * 60 * 60
    age_seconds = time.time() - file.stat().st_mtime
    return age_seconds >= cutoff_seconds


def _match_rule(file: Path, rules: list[Rule]) -> Rule | None:
    ext = _normalize_ext(file.suffix)
    filename = file.name

    for rule in rules:
        if rule.extensions and ext not in rule.extensions:
            continue

        if rule.regex and not rule.regex.search(filename):
            continue

        if rule.older_than_days is not None:
            if not _is_older_than(file, rule.older_than_days):
                continue

        return rule

    return None

def _resolve_conflict(dest: Path, policy: str) -> Path | None:
    """
    Returns:
      - Path to use (possibly renamed), or
      - None if policy == skip and file exists.
    """
    if not dest.exists():
        return dest

    policy = policy.lower()

    if policy in {"overwrite", "trash"}:
        return dest

    if policy == "skip":
        return None

    stem, suffix = dest.stem, dest.suffix
    parent = dest.parent
    i = 1
    while True:
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def run_pipeline(config_path: str, dry_run: bool = False, only_paths: set[Path] | None = None) -> None:
    cfg_path = Path(config_path)
    if not cfg_path.exists():
        raise FileNotFoundError(config_path)

    config = yaml.safe_load(cfg_path.read_text()) or {}
    cfg_abs = cfg_path.expanduser().resolve()

    source = _p(config["source"])
    default_target = _p(config.get("default_target", str(source / "others")))
    global_policy = str(config.get("conflict_policy", "rename"))

    rules = _load_rules(config)

    total_files = 0
    moved = 0
    skipped = 0
    overwritten = 0
    errors = 0

    by_rule = Counter()
    by_action = Counter()

    logger.info(f"Scanning: {source}")

    targets = {default_target} | {p for r in rules for p in (r.move_to, r.copy_to) if p}
    for t in targets:
        if dry_run:
            logger.debug(f"[DRY] ensure dir: {t}")
        else:
            t.mkdir(parents=True, exist_ok=True)

    for file in source.iterdir():
        if not file.is_file():
            continue
        if file.resolve() == cfg_abs:
            continue
        if only_paths is not None and file not in only_paths:
            continue

        total_files += 1

        rule = _match_rule(file, rules)
        rule_name = rule.name if rule else "default"
        dest_dir = (
            (rule.copy_to or rule.move_to) if rule else default_target
        )
        dest = dest_dir / file.name

        policy = (rule.conflict_policy if rule and rule.conflict_policy else global_policy)
        resolved = _resolve_conflict(dest, policy)
        if resolved is None:
            skipped += 1
            by_rule[rule_name] += 1
            by_action["skipped"] += 1
            logger.debug(f"SKIP (exists): {dest}")
            continue

        policy = policy.lower()
        will_replace = resolved.exists() and policy.lower() in {"overwrite", "trash"}

        if dry_run:
            by_rule[rule_name] += 1
            by_action["planned"] += 1
            chosen = f"rule={rule_name} prio={rule.priority}" if rule else "rule=default"
            action_name = "COPY" if (rule and rule.copy_to) else "MOVE"
            logger.debug(f"[DRY] {action_name} {chosen} | {file} -> {resolved}")
            continue

        try:
            if will_replace:
                if policy.lower() == "trash":
                    _trash(resolved)
                    by_action["trashed"] += 1
                else:
                    resolved.unlink()
                    overwritten += 1
                    by_action["overwritten"] += 1

            if rule and rule.copy_to:
                shutil.copy2(file, resolved)
            else:
                file.rename(resolved)

            moved += 1
            by_rule[rule_name] += 1
            by_action["moved"] += 1

            chosen = f"rule={rule_name} prio={rule.priority}" if rule else "rule=default"
            action_name = "COPY" if (rule and rule.copy_to) else "MOVE"
            logger.debug(f"{action_name} {chosen} | {file} -> {resolved}")
        except Exception as e:
            errors += 1
            by_action["errors"] += 1
            logger.exception(f"ERROR moving {file} -> {resolved}: {e}")


    logger.info("==== SUMMARY ====")
    logger.info(f"Dry-run: {dry_run}")
    logger.info(f"Files scanned: {total_files}")

    if dry_run:
        logger.info(f"Planned moves: {by_action.get('planned', 0)}")
    else:
        logger.info(f"Moved: {moved}")
        logger.info(f"Overwritten: {overwritten}")
        logger.info(f"Skipped: {skipped}")
        logger.info(f"Errors: {errors}")
        logger.info(f"Trashed: {by_action.get('trashed', 0)}")

    logger.info("By rule:")
    for rule_name, count in by_rule.most_common():
        logger.info(f"  - {rule_name}: {count}")