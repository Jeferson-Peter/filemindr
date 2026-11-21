from __future__ import annotations

import errno
import os
import stat
import time
from threading import Event

import pytest
import yaml

from filemindr.core import history, operations, watcher
from filemindr.core.runner import run_pipeline


@pytest.mark.parametrize("mode", ["same_device", "overwrite", "cross_device"])
def test_move_keeps_source_replaced_during_destination_verification(
    tmp_path, monkeypatch, mode
):
    source = tmp_path / "incoming.txt"
    source.write_bytes(b"incoming data")
    destination = tmp_path / "output.txt"
    if mode == "overwrite":
        destination.write_bytes(b"previous destination")
    plan = operations.OperationPlan(
        source,
        destination,
        "MOVE",
        "overwrite" if mode == "overwrite" else "rename",
        operations.destination_identity(destination),
    )
    identity = operations.file_identity
    initial = identity(source)
    replaced = False

    def change_source_while_verifying_destination(path):
        nonlocal replaced
        result = identity(path)
        if (
            path == destination
            and result["sha256"] == initial["sha256"]
            and not replaced
        ):
            replacement = tmp_path / "external.txt"
            replacement.write_bytes(b"unrelated replacement")
            os.replace(replacement, source)
            replaced = True
        return result

    monkeypatch.setattr(
        operations, "file_identity", change_source_while_verifying_destination
    )
    if mode == "cross_device":
        link = operations.os.link

        def cross_device(current, target, **kwargs):
            if current == source:
                raise OSError(errno.EXDEV, "different filesystem")
            return link(current, target, **kwargs)

        monkeypatch.setattr(operations.os, "link", cross_device)

    with pytest.raises(operations.PartialOperationError, match="Source changed"):
        operations.execute_operation(plan)
    assert replaced
    assert source.read_bytes() == b"unrelated replacement"
    assert destination.read_bytes() == b"incoming data"


@pytest.mark.parametrize("failure", ["directory", "unreadable"])
def test_failed_trash_keeps_backup_when_destination_cannot_be_verified(
    tmp_path, monkeypatch, failure
):
    source = tmp_path / "incoming.txt"
    source.write_bytes(b"incoming data")
    destination = tmp_path / "output.txt"
    destination.write_bytes(b"previous destination")
    plan = operations.OperationPlan(
        source, destination, "COPY", "trash", operations.file_identity(destination)
    )
    identity = operations.file_identity
    trash_started = False

    def trash(path):
        nonlocal trash_started
        path.unlink()
        if failure == "directory":
            path.mkdir()
        else:
            path.write_bytes(b"concurrent destination")
        trash_started = True
        raise OSError("trash failed after moving its input")

    def unreadable_after_trash(path):
        if path == destination and trash_started:
            raise PermissionError("concurrent destination cannot be read")
        return identity(path)

    if failure == "unreadable":
        monkeypatch.setattr(operations, "file_identity", unreadable_after_trash)
    with pytest.raises(operations.PartialOperationError) as error:
        operations.execute_operation(plan, trash=trash)
    assert error.value.recovery_path.suffix == ".bak"
    assert error.value.recovery_path.read_bytes() == b"previous destination"
    assert source.read_bytes() == b"incoming data"
    assert not list(tmp_path.glob(".filemindr-*.tmp"))


def test_undo_keeps_current_file_replaced_during_restoration_verification(
    tmp_path, fake_home, monkeypatch
):
    source_dir = tmp_path / "input"
    source_dir.mkdir()
    original = source_dir / "a.txt"
    original.write_bytes(b"original data")
    target = tmp_path / "output"
    config = tmp_path / "rules.yaml"
    config.write_text(
        yaml.safe_dump(dict(source=str(source_dir), default_target=str(target)))
    )
    assert run_pipeline(str(config), profile="review", command="run")["moved"] == 1
    run_id = history.iter_run_meta()[0]["run_id"]
    current = target / "a.txt"
    identity = operations.file_identity
    replaced = False

    def replace_current(path):
        nonlocal replaced
        result = identity(path)
        if path == original and not replaced:
            replacement = tmp_path / "external.txt"
            replacement.write_bytes(b"unrelated replacement")
            os.replace(replacement, current)
            replaced = True
        return result

    monkeypatch.setattr(operations, "file_identity", replace_current)
    result = history.undo_run(run_id)
    assert result.errors == 1 and result.reverted == 0
    assert current.read_bytes() == b"unrelated replacement"
    assert original.read_bytes() == b"original data"


def test_source_replaced_after_final_path_check_is_not_deleted(tmp_path, monkeypatch):
    source = tmp_path / "incoming.txt"
    source.write_bytes(b"incoming data")
    destination = tmp_path / "output.txt"
    identity = operations.file_identity
    replaced = False

    def replace_after_source_check(path):
        nonlocal replaced
        result = identity(path)
        if path == source and destination.exists() and not replaced:
            replacement = tmp_path / "external.txt"
            replacement.write_bytes(b"unrelated replacement")
            os.replace(replacement, source)
            replaced = True
        return result

    monkeypatch.setattr(operations, "file_identity", replace_after_source_check)
    with pytest.raises(
        operations.PartialOperationError, match="Source changed"
    ) as error:
        operations.execute_operation(
            operations.OperationPlan(source, destination, "MOVE", "rename", None)
        )
    assert replaced
    assert source.read_bytes() == b"unrelated replacement"
    assert destination.read_bytes() == b"incoming data"
    assert error.value.recovery_path.read_bytes() == b"unrelated replacement"


def test_source_recreated_after_private_claim_survives_success(tmp_path, monkeypatch):
    source = tmp_path / "incoming.txt"
    source.write_bytes(b"incoming data")
    destination = tmp_path / "output.txt"
    identity = operations.file_identity
    recreated = False

    def recreate_after_claim(path):
        nonlocal recreated
        result = identity(path)
        if (
            path.parent == source.parent
            and path.name.startswith(operations.INTERNAL_PREFIX)
            and path.suffix == ".bak"
            and not recreated
        ):
            source.write_bytes(b"new incoming file")
            recreated = True
        return result

    monkeypatch.setattr(operations, "file_identity", recreate_after_claim)
    operations.execute_operation(
        operations.OperationPlan(source, destination, "MOVE", "rename", None)
    )
    assert recreated
    assert source.read_bytes() == b"new incoming file"
    assert destination.read_bytes() == b"incoming data"
    assert not list(tmp_path.glob(".filemindr-*.bak"))


def test_failure_report_identifies_source_claim_that_completed_before_error(
    tmp_path, monkeypatch
):
    source = tmp_path / "incoming.txt"
    source.write_bytes(b"incoming data")
    destination = tmp_path / "output.txt"
    replace = operations.os.replace

    def claim_then_fail(current, target):
        replace(current, target)
        if current == source:
            raise InterruptedError("interrupted after claiming source")

    monkeypatch.setattr(operations.os, "replace", claim_then_fail)
    with pytest.raises(operations.PartialOperationError) as error:
        operations.execute_operation(
            operations.OperationPlan(source, destination, "MOVE", "rename", None)
        )
    assert not source.exists()
    assert error.value.recovery_path.suffix == ".bak"
    assert error.value.recovery_path.read_bytes() == b"incoming data"
    assert destination.read_bytes() == b"incoming data"


def test_watch_suppresses_failed_self_events_but_retries_permission_change(
    tmp_path, monkeypatch
):
    source = tmp_path / "incoming.txt"
    source.write_bytes(b"incoming data")
    source.chmod(0o444)
    first = Event()
    writable_attempts = []

    def events(*args, **kwargs):
        yield [(1, str(source))]
        assert first.wait(1)
        yield [(1, str(source))]
        time.sleep(0.1)
        source.chmod(0o666)
        yield [(2, str(source))]

    def run(*args, **kwargs):
        writable = bool(source.stat().st_mode & stat.S_IWRITE)
        writable_attempts.append(writable)
        first.set()
        return {"errors": 0 if writable else 1}

    monkeypatch.setattr(watcher, "watch", events)
    monkeypatch.setattr(watcher, "run_pipeline", run)
    try:
        watcher.watch_and_run(
            tmp_path,
            str(tmp_path / "rules.yaml"),
            False,
            watcher.WatchOptions(
                stable_ms=0, debounce_ms=0, poll_interval_ms=1, main_loop_tick_ms=1
            ),
        )
    finally:
        source.chmod(0o666)
    assert writable_attempts == [False, True]


def test_source_recovery_does_not_overwrite_a_new_input(tmp_path, monkeypatch):
    source = tmp_path / "incoming.txt"
    source.write_bytes(b"incoming data")
    destination = tmp_path / "output.txt"
    identity = operations.file_identity
    raced = False

    def replace_after_claim(path):
        nonlocal raced
        result = identity(path)
        if (
            path.name.startswith(operations.INTERNAL_PREFIX)
            and path.suffix == ".bak"
            and not raced
        ):
            source.write_bytes(b"new input")
            external = tmp_path / "external.txt"
            external.write_bytes(b"external output")
            os.replace(external, destination)
            raced = True
        return result

    monkeypatch.setattr(operations, "file_identity", replace_after_claim)
    with pytest.raises(operations.PartialOperationError) as error:
        operations.execute_operation(
            operations.OperationPlan(source, destination, "MOVE", "rename", None)
        )
    assert raced
    assert source.read_bytes() == b"new input"
    assert destination.read_bytes() == b"external output"
    assert error.value.recovery_path.read_bytes() == b"incoming data"
