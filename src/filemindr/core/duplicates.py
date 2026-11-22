from __future__ import annotations

import hashlib
import os
import stat
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from filemindr.core.ignore import matches_ignore_pattern

CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class DuplicateGroup:
    size: int
    sha256: str
    paths: tuple[Path, ...]


@dataclass(frozen=True)
class UnverifiedFile:
    path: Path
    reason: str
    size: int | None = None


@dataclass(frozen=True)
class DuplicateScanResult:
    root: Path
    files_scanned: int
    files_hashed: int
    ignored: int
    symlinks_skipped: int
    groups: tuple[DuplicateGroup, ...]
    unverified: tuple[UnverifiedFile, ...]

    @property
    def complete(self) -> bool:
        return not self.unverified

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "complete": self.complete,
            "files_scanned": self.files_scanned,
            "files_hashed": self.files_hashed,
            "ignored": self.ignored,
            "symlinks_skipped": self.symlinks_skipped,
            "groups": [
                {
                    "size": group.size,
                    "sha256": group.sha256,
                    "paths": [str(path) for path in group.paths],
                }
                for group in self.groups
            ],
            "unverified": [
                {"path": str(item.path), "size": item.size, "reason": item.reason}
                for item in self.unverified
            ],
        }


def _signature(info: os.stat_result) -> tuple[int, ...]:
    values = (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_mode,
    )
    # On Windows st_ctime_ns is the creation timestamp and has shown small
    # filesystem rounding changes during immediate post-write scans.
    return values if os.name == "nt" else values + (info.st_ctime_ns,)


def _hash_stream(stream) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(CHUNK_SIZE):
        digest.update(chunk)
    return digest.hexdigest()


def _hash_file(path: Path, expected: tuple[int, ...]) -> str:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or _signature(opened) != expected:
            raise OSError("File changed or became a symbolic link before hashing")
        digest = _hash_stream(stream)
        after_open = os.fstat(stream.fileno())
    after_path = path.lstat()
    if (
        _signature(after_open) != expected
        or _signature(after_path) != expected
        or not stat.S_ISREG(after_path.st_mode)
    ):
        raise OSError("File changed while it was being hashed")
    return digest


def _path_key(path: Path) -> str:
    value = str(path)
    return os.path.normcase(value)


def scan_duplicates(
    directory: Path,
    *,
    ignore_patterns: list[str] | None = None,
) -> DuplicateScanResult:
    selected = directory.expanduser().absolute()
    if selected.is_symlink() or selected.is_junction():
        raise ValueError("Scanning a symbolic-link or junction root is unsupported")
    root_info = selected.lstat()
    if not stat.S_ISDIR(root_info.st_mode):
        raise NotADirectoryError(selected)
    root = selected.resolve()
    patterns = ignore_patterns or []
    size_buckets: dict[int, list[tuple[Path, tuple[int, ...]]]] = defaultdict(list)
    unverified: list[UnverifiedFile] = []
    ignored = 0
    symlinks_skipped = 0
    files_scanned = 0

    def on_walk_error(exc: OSError) -> None:
        unverified.append(
            UnverifiedFile(Path(exc.filename or root), f"{type(exc).__name__}: {exc}")
        )

    current, directories, filenames = str(root), [], []
    for current, directories, filenames in os.walk(
        root, topdown=True, followlinks=False, onerror=on_walk_error
    ):
        parent = Path(current)
        kept_directories = []
        for name in directories:
            path = parent / name
            if path.is_symlink() or path.is_junction():
                symlinks_skipped += 1
            elif matches_ignore_pattern(path, patterns):
                ignored += 1
            else:
                kept_directories.append(name)
        directories[:] = kept_directories
        for name in filenames:
            path = parent / name
            if matches_ignore_pattern(path, patterns):
                ignored += 1
                continue
            try:
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    symlinks_skipped += 1
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                files_scanned += 1
                size_buckets[info.st_size].append((path, _signature(info)))
            except OSError as exc:
                unverified.append(UnverifiedFile(path, f"{type(exc).__name__}: {exc}"))
    del current, directories, filenames

    hashes: dict[tuple[int, str], list[Path]] = defaultdict(list)
    files_hashed = 0
    while size_buckets:
        size, entries = size_buckets.popitem()
        if len(entries) < 2:
            continue
        while entries:
            path, signature = entries.pop()
            try:
                digest = _hash_file(path, signature)
            except OSError as exc:
                unverified.append(
                    UnverifiedFile(path, f"{type(exc).__name__}: {exc}", size)
                )
                continue
            files_hashed += 1
            hashes[(size, digest)].append(path)
    groups = tuple(
        DuplicateGroup(
            size,
            digest,
            tuple(sorted(paths, key=lambda path: (_path_key(path), str(path)))),
        )
        for (size, digest), paths in sorted(hashes.items())
        if len(paths) > 1
    )
    del hashes
    return DuplicateScanResult(
        root=root,
        files_scanned=files_scanned,
        files_hashed=files_hashed,
        ignored=ignored,
        symlinks_skipped=symlinks_skipped,
        groups=groups,
        unverified=tuple(
            sorted(unverified, key=lambda item: (_path_key(item.path), item.reason))
        ),
    )
