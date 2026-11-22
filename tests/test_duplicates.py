from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from filemindr.cli import app
from filemindr.core import duplicates
from filemindr.core.duplicates import scan_duplicates
from typer.testing import CliRunner


def test_duplicate_groups_require_matching_size_and_sha256(tmp_path: Path):
    root = tmp_path / "files"
    root.mkdir()
    (root / "résumé A.txt").write_bytes(b"same")
    (root / "resume B.txt").write_bytes(b"same")
    (root / "different.txt").write_bytes(b"else")
    (root / "empty one").write_bytes(b"")
    (root / "empty two").write_bytes(b"")

    result = scan_duplicates(root)

    assert result.complete
    assert result.files_scanned == 5
    assert {
        frozenset(path.name for path in group.paths) for group in result.groups
    } == {
        frozenset(("résumé A.txt", "resume B.txt")),
        frozenset(("empty one", "empty two")),
    }
    assert all(len(group.sha256) == 64 for group in result.groups)


def test_large_files_are_hashed_in_bounded_chunks(tmp_path: Path, monkeypatch):
    root = tmp_path / "large"
    root.mkdir()
    data = b"0123456789abcdef" * 131_072
    (root / "one.bin").write_bytes(data)
    (root / "two.bin").write_bytes(data)
    monkeypatch.setattr(duplicates, "CHUNK_SIZE", 4096)
    sizes: list[int] = []
    original = duplicates._hash_stream

    class TrackingStream:
        def __init__(self, stream):
            self.stream = stream

        def read(self, size=-1):
            sizes.append(size)
            return self.stream.read(size)

    monkeypatch.setattr(
        duplicates,
        "_hash_stream",
        lambda stream: original(TrackingStream(stream)),
    )
    result = scan_duplicates(root)
    assert result.groups[0].size == len(data)
    assert result.files_hashed == 2
    assert sizes and set(sizes) == {duplicates.CHUNK_SIZE}


def test_unreadable_same_size_file_is_reported_as_unverified(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "permission"
    root.mkdir()
    blocked = root / "blocked.dat"
    blocked.write_bytes(b"same")
    (root / "copy one.dat").write_bytes(b"same")
    (root / "copy two.dat").write_bytes(b"same")
    original = duplicates._hash_file

    def unreadable(path, expected):
        if path == blocked:
            raise PermissionError("simulated read denial")
        return original(path, expected)

    monkeypatch.setattr(duplicates, "_hash_file", unreadable)
    result = scan_duplicates(root)

    assert not result.complete
    assert len(result.unverified) == 1
    assert result.unverified[0].path == blocked
    assert "PermissionError" in result.unverified[0].reason
    assert len(result.groups) == 1
    assert {path.name for path in result.groups[0].paths} == {
        "copy one.dat",
        "copy two.dat",
    }


def test_file_changed_during_hash_is_not_reported_as_verified(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "changing"
    root.mkdir()
    changing = root / "a.txt"
    changing.write_bytes(b"same")
    (root / "b.txt").write_bytes(b"same")
    original = duplicates._hash_stream

    def mutate_after_read(stream):
        digest = original(stream)
        changing.write_bytes(b"new!")
        return digest

    monkeypatch.setattr(duplicates, "_hash_stream", mutate_after_read)
    result = scan_duplicates(root)

    assert not result.complete
    assert result.unverified[0].path == changing
    assert "changed" in result.unverified[0].reason.lower()
    assert not result.groups


def test_scanner_skips_symlinks_and_honors_ignore_patterns(tmp_path: Path):
    root = tmp_path / "links"
    root.mkdir()
    original = root / "source.txt"
    original.write_text("same", encoding="utf-8")
    ignored = root / "ignored.skip"
    ignored.write_text("same", encoding="utf-8")
    alias = root / "alias.txt"
    nested = root / "nested"
    nested.mkdir()
    (nested / "inside.txt").write_text("same", encoding="utf-8")
    try:
        alias.symlink_to(original)
        (root / "nested-alias").symlink_to(nested, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlink creation unavailable: {exc}")

    result = scan_duplicates(root, ignore_patterns=["*.skip"])

    assert result.files_scanned == 2
    assert result.ignored == 1
    assert result.symlinks_skipped == 2
    assert len(result.groups) == 1
    assert set(result.groups[0].paths) == {original, nested / "inside.txt"}
    assert original.exists() and alias.is_symlink()


def test_cli_outputs_json_without_modifying_files(tmp_path: Path):
    root = tmp_path / "cli"
    root.mkdir()
    first = root / "one.txt"
    second = root / "二.txt"
    first.write_text("duplicate", encoding="utf-8")
    second.write_text("duplicate", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, ["duplicates", str(root), "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["complete"] is True
    assert len(payload["groups"]) == 1
    assert {Path(path).name for path in payload["groups"][0]["paths"]} == {
        "one.txt",
        "二.txt",
    }
    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")


def test_cli_reuses_profile_ignore_patterns(tmp_path: Path, fake_home: Path):
    root = tmp_path / "profile-scan"
    root.mkdir()
    (root / "included.txt").write_text("same", encoding="utf-8")
    (root / "ignored.tmp").write_text("same", encoding="utf-8")
    rules_dir = fake_home / ".filemindr" / "rules" / "home"
    rules_dir.mkdir(parents=True)
    rules = rules_dir / "rules.yaml"
    rules.write_text(
        yaml.safe_dump(
            {
                "source": str(root),
                "default_target": str(root / "out"),
                "ignore": ["*.tmp"],
            }
        ),
        encoding="utf-8",
    )
    (fake_home / ".filemindr" / "profiles.yaml").write_text(
        yaml.safe_dump({"profiles": {"home": str(rules_dir)}}), encoding="utf-8"
    )
    result = CliRunner().invoke(
        app, ["duplicates", str(root), "--profile", "home", "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["files_scanned"] == 1
    assert payload["ignored"] == 1
