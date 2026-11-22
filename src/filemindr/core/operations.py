"""Conservative regular-file operations; no cross-filesystem atomicity guarantee."""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

POLICIES = {"rename", "skip", "overwrite", "trash"}
INTERNAL_PREFIX = ".filemindr-"


def snapshot(path: Path) -> tuple[int, ...]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Not a regular file (symlinks are unsupported): {path}")
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
    )


def file_identity(path: Path) -> dict[str, int | str]:
    before = snapshot(path)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if snapshot(path) != before:
        raise OSError(f"File changed while reading: {path}")
    return dict(zip(("device", "inode", "size", "mtime_ns"), before[:4])) | {
        "sha256": digest
    }


def destination_identity(path: Path) -> dict[str, int | str] | None:
    try:
        return file_identity(path)
    except FileNotFoundError:
        return None


def same_file(source: Path, destination: Path) -> bool:
    return source == destination or (
        destination.exists() and source.samefile(destination)
    )


@dataclass(frozen=True)
class OperationPlan:
    source: Path
    destination: Path
    action: str
    policy: str
    expected_destination: dict[str, int | str] | None


@dataclass(frozen=True)
class OperationResult:
    identity: dict[str, int | str]
    replaced: bool


class PartialOperationError(OSError):
    """Publication succeeded, or recovery needs manual inspection."""

    def __init__(self, message: str, recovery_path: Path) -> None:
        super().__init__(f"{message}; recovery path: {recovery_path}")
        self.recovery_path = recovery_path


def _temporary(parent: Path, suffix: str = ".tmp") -> Path:
    fd, name = tempfile.mkstemp(prefix=INTERNAL_PREFIX, suffix=suffix, dir=parent)
    os.close(fd)
    return Path(name)


def _stage(
    source: Path, destination: Path, expected: dict[str, int | str]
) -> tuple[Path, dict[str, int | str]]:
    staged = _temporary(destination.parent)
    try:
        shutil.copy2(source, staged)
        copied_mode = staged.stat().st_mode
        staged.chmod(copied_mode | stat.S_IWRITE)
        with staged.open("r+b") as stream:
            os.fsync(stream.fileno())
        staged.chmod(copied_mode)
        copied = file_identity(staged)
        if (copied["size"], copied["sha256"]) != (expected["size"], expected["sha256"]):
            raise OSError(f"Staged copy verification failed: {source}")
        if file_identity(source) != expected:
            raise OSError(f"Source changed during copy: {source}")
        return staged, copied
    except BaseException:
        _cleanup(staged)
        raise


def _check_destination(plan: OperationPlan) -> None:
    if destination_identity(plan.destination) != plan.expected_destination:
        raise FileExistsError(f"Destination changed since planning: {plan.destination}")


def _check_destination_parents(destination: Path) -> None:
    for parent in reversed((*destination.parents, destination.parent)):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISDIR(info.st_mode)
            or parent.is_symlink()
            or parent.is_junction()
        ):
            raise OSError(f"Destination parent changed or is unsupported: {parent}")


def _publish_staged(source: Path, destination: Path) -> None:
    if os.name == "nt":
        # Windows rename refuses existing destinations and needs no hard links.
        source.rename(destination)
    else:
        os.link(source, destination)


def _remove_source(
    plan: OperationPlan,
    expected: dict[str, int | str],
    published_identity: dict[str, int | str],
) -> None:
    # Claim the pathname before deletion: a writer can recreate source at any time.
    retained = _temporary(plan.source.parent, suffix=".bak")
    placeholder = snapshot(retained)
    try:
        os.replace(plan.source, retained)
    except BaseException as exc:
        if snapshot(retained) == placeholder:
            _cleanup(retained)
        else:
            if isinstance(exc, Exception):
                raise PartialOperationError(
                    f"Source claim failed: {exc}", retained
                ) from exc
            exc.add_note(f"Source recovery file: {retained}")
        raise

    try:
        if file_identity(retained) != expected:
            raise OSError(f"Source changed before removal: {plan.source}")
        before = snapshot(retained)
        if file_identity(plan.destination) != published_identity:
            raise OSError(
                f"Published destination changed before source removal: {plan.destination}"
            )
        if snapshot(retained) != before:
            raise OSError(
                f"Source changed during destination verification: {plan.source}"
            )
        retained.unlink()
    except BaseException as exc:
        if retained.exists():
            # Restore without replacing any newly created source; retain the recovery link.
            try:
                os.link(retained, plan.source, follow_symlinks=False)
            except OSError:
                pass
            if isinstance(exc, Exception):
                raise PartialOperationError(
                    f"Source removal failed: {exc}", retained
                ) from exc
            exc.add_note(f"Source recovery file: {retained}")
        raise


def execute_operation(
    plan: OperationPlan,
    *,
    trash: Callable[[Path], None] | None = None,
    expected_source: dict[str, int | str] | None = None,
    expected_source_snapshot: tuple[int, ...] | None = None,
) -> OperationResult:
    if plan.policy not in POLICIES or plan.action not in {"MOVE", "COPY"}:
        raise ValueError("Invalid operation action or conflict policy")
    if (
        expected_source_snapshot is not None
        and snapshot(plan.source) != expected_source_snapshot
    ):
        raise OSError(f"Source changed since planning: {plan.source}")
    initial = file_identity(plan.source)
    if (
        expected_source_snapshot is not None
        and snapshot(plan.source) != expected_source_snapshot
    ):
        raise OSError(f"Source changed since planning: {plan.source}")
    if expected_source is not None and initial != expected_source:
        raise ValueError(f"File identity no longer matches history: {plan.source}")
    if same_file(plan.source, plan.destination):
        raise ValueError("Source and destination refer to the same file")
    _check_destination_parents(plan.destination)
    plan.destination.parent.mkdir(parents=True, exist_ok=True)
    _check_destination_parents(plan.destination)
    _check_destination(plan)
    staged: Path | None = None
    backup: Path | None = None
    published = False
    published_identity = initial
    try:
        # Hard-link publication cannot clobber a path created by another process.
        if plan.action == "MOVE" and plan.expected_destination is None:
            try:
                _check_destination_parents(plan.destination)
                os.link(plan.source, plan.destination, follow_symlinks=False)
                published = True
            except OSError as exc:
                if exc.errno != errno.EXDEV and getattr(exc, "winerror", None) != 17:
                    raise
        if not published:
            staged, published_identity = _stage(plan.source, plan.destination, initial)
            _check_destination_parents(plan.destination)
            _check_destination(plan)
            if plan.expected_destination is not None:
                if plan.policy == "trash":
                    if trash is None:
                        raise ValueError("Trash callback is required")
                    backup, _ = _stage(
                        plan.destination, plan.destination, plan.expected_destination
                    )
                    # Keep a discoverable copy until trash and publication both finish.
                    recovery = backup.with_suffix(".bak")
                    backup.rename(recovery)
                    backup = recovery
                    _check_destination(plan)
                    try:
                        trash(plan.destination)
                        _publish_staged(staged, plan.destination)
                    except BaseException:
                        retained = backup
                        backup = None
                        try:
                            current = destination_identity(plan.destination)
                            if current is None:
                                _publish_staged(retained, plan.destination)
                            elif current != plan.expected_destination:
                                raise OSError("Destination changed during trash")
                        except Exception as exc:
                            raise PartialOperationError(
                                "Trash rollback failed or could not be verified",
                                retained,
                            ) from exc
                        backup = retained
                        raise
                elif plan.policy == "overwrite":
                    os.replace(staged, plan.destination)
                else:
                    raise FileExistsError(plan.destination)
            else:
                _publish_staged(staged, plan.destination)
            published = True
        if plan.action == "MOVE":
            if file_identity(plan.destination) != published_identity:
                raise OSError(
                    f"Published destination changed before source removal: {plan.destination}"
                )
            if file_identity(plan.source) != initial:
                raise OSError(f"Source changed before removal: {plan.source}")
            _remove_source(plan, initial, published_identity)
        # Hash after publication; undo needs the final destination identity.
        final = file_identity(plan.destination)
        if final != published_identity:
            raise OSError(f"Published destination identity changed: {plan.destination}")
        return OperationResult(final, plan.expected_destination is not None)
    except Exception as exc:
        if published and not isinstance(exc, PartialOperationError):
            raise PartialOperationError(str(exc), plan.destination) from exc
        raise
    finally:
        if staged is not None:
            _cleanup(staged)
        if backup is not None:
            _cleanup(backup)


def _cleanup(path: Path) -> None:
    try:
        try:
            path.unlink(missing_ok=True)
        except PermissionError:
            # Windows copy2 propagates the read-only bit to our private staging file.
            path.chmod(stat.S_IWRITE | stat.S_IREAD)
            path.unlink()
    except OSError as exc:
        raise PartialOperationError("Temporary file cleanup failed", path) from exc
