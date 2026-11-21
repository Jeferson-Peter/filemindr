from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from filemindr.core import operations as ops


@pytest.fixture
def files(tmp_path):
    source = tmp_path / "input.txt"
    destination = tmp_path / "out" / "output.txt"
    source.write_bytes(b"incoming content")
    destination.parent.mkdir()
    destination.write_bytes(b"original content")
    return source, destination


def plan(files, action="COPY", policy="overwrite"):
    source, destination = files
    return ops.OperationPlan(
        source, destination, action, policy, ops.destination_identity(destination)
    )


@pytest.mark.parametrize(
    "failure",
    [
        OSError(errno.ENOSPC, "disk full"),
        PermissionError("locked"),
        KeyboardInterrupt(),
    ],
)
@pytest.mark.parametrize("policy", ["overwrite", "trash"])
def test_failed_partial_copy_keeps_both_originals(files, monkeypatch, failure, policy):
    source, destination = files

    def partial(source, target):
        Path(target).write_bytes(b"partial")
        raise failure

    monkeypatch.setattr(ops.shutil, "copy2", partial)
    with pytest.raises(type(failure)):
        ops.execute_operation(
            plan(files, policy=policy),
            trash=lambda p: pytest.fail("trash called too early"),
        )
    assert source.read_bytes() == b"incoming content"
    assert destination.read_bytes() == b"original content"
    assert not list(destination.parent.glob(".filemindr-*"))


def test_failed_atomic_replace_preserves_existing_file(files, monkeypatch):
    def fail(*args):
        raise PermissionError("locked destination")

    monkeypatch.setattr(ops.os, "replace", fail)
    with pytest.raises(PermissionError):
        ops.execute_operation(plan(files, action="MOVE"))
    assert files[0].exists()
    assert files[1].read_bytes() == b"original content"
    assert not list(files[1].parent.glob(".filemindr-*"))


def test_cross_device_move_copies_before_removing_source(files, monkeypatch):
    source, destination = files
    destination.unlink()
    real_link = ops.os.link

    def cross_device(current, target, **kwargs):
        if current == source:
            raise OSError(errno.EXDEV, "different filesystem")
        assert source.exists()
        return real_link(current, target, **kwargs)

    monkeypatch.setattr(ops.os, "link", cross_device)
    result = ops.execute_operation(plan(files, "MOVE", "rename"))
    assert not source.exists()
    assert destination.read_bytes() == b"incoming content"
    assert result.identity == ops.file_identity(destination)


def test_failed_cross_device_copy_preserves_source(files, monkeypatch):
    source, destination = files
    destination.unlink()
    monkeypatch.setattr(
        ops.os,
        "link",
        lambda *a, **kw: (_ for _ in ()).throw(OSError(errno.EXDEV, "cross-device")),
    )

    def partial(current, target):
        Path(target).write_bytes(b"partial")
        raise OSError(errno.ENOSPC, "full")

    monkeypatch.setattr(ops.shutil, "copy2", partial)
    with pytest.raises(OSError):
        ops.execute_operation(plan(files, "MOVE", "rename"))
    assert source.read_bytes() == b"incoming content"
    assert not destination.exists()
    assert not list(destination.parent.iterdir())


def test_windows_cross_volume_error_is_handled(files, monkeypatch):
    source, destination = files
    destination.unlink()
    link = ops.os.link

    def different_volume(current, target, **kwargs):
        if current == source:
            error = OSError("different volume")
            error.winerror = 17
            raise error
        return link(current, target, **kwargs)

    monkeypatch.setattr(ops.os, "link", different_volume)
    ops.execute_operation(plan(files, "MOVE", "rename"))
    assert destination.read_bytes() == b"incoming content"
    assert not source.exists()


def test_source_unlink_failure_reports_published_duplicate(files, monkeypatch):
    source, destination = files
    destination.unlink()
    real_unlink = Path.unlink

    def unlink(path, **kwargs):
        if (
            path.parent == source.parent
            and path.name.startswith(ops.INTERNAL_PREFIX)
            and path.suffix == ".bak"
        ):
            raise PermissionError("source locked")
        return real_unlink(path, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(ops.PartialOperationError) as error:
        ops.execute_operation(plan(files, "MOVE", "rename"))
    assert error.value.recovery_path.suffix == ".bak"
    assert error.value.recovery_path.read_bytes() == b"incoming content"
    assert source.read_bytes() == destination.read_bytes() == b"incoming content"


def test_destination_replaced_after_publication_does_not_delete_source(
    files, monkeypatch
):
    source, destination = files
    destination.unlink()
    link = ops.os.link

    def replace_after_link(current, target, **kwargs):
        link(current, target, **kwargs)
        unrelated = destination.with_suffix(".external")
        unrelated.write_bytes(b"unrelated file")
        os.replace(unrelated, destination)

    monkeypatch.setattr(ops.os, "link", replace_after_link)
    with pytest.raises(ops.PartialOperationError, match="destination changed"):
        ops.execute_operation(plan(files, "MOVE", "rename"))
    assert source.read_bytes() == b"incoming content"
    assert destination.read_bytes() == b"unrelated file"


@pytest.mark.parametrize("action", ["COPY", "MOVE"])
def test_no_clobber_when_destination_appears_during_publication(
    files, monkeypatch, action
):
    source, destination = files
    destination.unlink()
    publish = ops._publish_staged
    real_link = ops.os.link

    def race(current, target, **kwargs):
        destination.write_bytes(b"unrelated file")
        return real_link(current, target, **kwargs)

    monkeypatch.setattr(ops.os, "link", race)
    if action == "COPY":

        def staged_race(current, target):
            destination.write_bytes(b"unrelated file")
            return publish(current, target)

        monkeypatch.setattr(ops, "_publish_staged", staged_race)
    with pytest.raises(FileExistsError):
        ops.execute_operation(plan(files, action, "rename"))
    assert source.exists()
    assert destination.read_bytes() == b"unrelated file"


def test_modified_destination_between_plan_and_execute_is_preserved(files):
    operation = plan(files)
    files[1].write_bytes(b"external edit")
    with pytest.raises(FileExistsError, match="changed"):
        ops.execute_operation(operation)
    assert files[1].read_bytes() == b"external edit"
    assert files[0].exists()


def test_source_changed_during_copy_is_not_removed(files, monkeypatch):
    source, destination = files
    copy = ops.shutil.copy2

    def race(current, target):
        copy(current, target)
        source.write_bytes(b"new source edit")

    monkeypatch.setattr(ops.shutil, "copy2", race)
    with pytest.raises(OSError, match="Source changed"):
        ops.execute_operation(plan(files, "MOVE"))
    assert source.read_bytes() == b"new source edit"
    assert destination.read_bytes() == b"original content"


def test_corrupt_copy_is_never_published(files, monkeypatch):
    monkeypatch.setattr(
        ops.shutil, "copy2", lambda source, target: Path(target).write_bytes(b"corrupt")
    )
    with pytest.raises(OSError, match="verification"):
        ops.execute_operation(plan(files))
    assert files[1].read_bytes() == b"original content"


def test_same_file_and_hardlink_alias_are_refused(files):
    source, destination = files
    destination.unlink()
    os.link(source, destination)
    with pytest.raises(ValueError, match="same file"):
        ops.execute_operation(plan(files))
    assert source.read_bytes() == destination.read_bytes() == b"incoming content"


@pytest.mark.parametrize("link_source", [True, False])
def test_symlink_operations_are_refused(files, link_source):
    source, destination = files
    link = source.with_name("link.txt")
    try:
        link.symlink_to(source if link_source else destination)
    except OSError:
        pytest.skip("Creating symlinks requires OS support/privileges")
    with pytest.raises(ValueError, match="regular file"):
        ops.execute_operation(
            plan((link, destination) if link_source else (source, link))
        )
    assert source.exists() and destination.exists()


def test_trash_publication_failure_restores_old_destination(files, monkeypatch):
    def fail_new_file(current, target, **kwargs):
        if Path(current).suffix == ".tmp":
            raise PermissionError("publication failed")
        return publish(current, target)

    publish = ops._publish_staged
    monkeypatch.setattr(ops, "_publish_staged", fail_new_file)
    with pytest.raises(PermissionError):
        ops.execute_operation(plan(files, policy="trash"), trash=lambda p: p.unlink())
    assert files[0].exists()
    assert files[1].read_bytes() == b"original content"
    assert not list(files[1].parent.glob(".filemindr-*"))


def test_trash_rollback_failure_retains_discoverable_backup(files, monkeypatch):
    def fail(*args, **kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(ops, "_publish_staged", fail)
    with pytest.raises(ops.PartialOperationError) as error:
        ops.execute_operation(plan(files, policy="trash"), trash=lambda p: p.unlink())
    assert error.value.recovery_path.suffix == ".bak"
    assert error.value.recovery_path.read_bytes() == b"original content"
    assert files[0].exists()


def test_readonly_source_copy_cleans_staging_file(files):
    source, destination = files
    source.chmod(0o444)
    try:
        ops.execute_operation(plan(files))
        assert destination.read_bytes() == source.read_bytes()
        assert destination.stat().st_mode & 0o222 == 0
        assert not list(destination.parent.glob(".filemindr-*"))
    finally:
        source.chmod(0o666)
        destination.chmod(0o666)


def test_locked_staging_cleanup_reports_its_recovery_path(tmp_path, monkeypatch):
    staged = tmp_path / ".filemindr-locked.tmp"
    staged.write_text("retained")
    with monkeypatch.context() as patch:
        patch.setattr(
            Path,
            "unlink",
            lambda *a, **kw: (_ for _ in ()).throw(PermissionError("locked")),
        )
        with pytest.raises(ops.PartialOperationError, match="cleanup failed") as error:
            ops._cleanup(staged)
        assert error.value.recovery_path == staged
    assert staged.read_text() == "retained"
