from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path


def load_ignore_patterns(config: dict) -> list[str]:
    raw = config.get("ignore", [])
    if raw is None:
        return []
    return [str(item) for item in raw]


def matches_ignore_pattern(path: Path, patterns: list[str]) -> bool:
    if not patterns:
        return False

    name = path.name
    path_posix = path.as_posix()
    for pattern in patterns:
        if fnmatch(name, pattern) or fnmatch(path_posix, pattern):
            return True
    return False
