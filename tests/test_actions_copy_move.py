from __future__ import annotations

from pathlib import Path

import pytest


# Ajusta o import se teu módulo estiver em outro lugar
from filemindr.core.runner import run_pipeline


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_copy_to_keeps_source_and_copies_to_target(tmp_path: Path) -> None:
    source = tmp_path / "Downloads"
    target = tmp_path / "images"
    source.mkdir(parents=True)
    target.mkdir(parents=True)

    # arquivo de exemplo
    src_file = source / "photo.png"
    src_file.write_bytes(b"fake-png")

    config_path = tmp_path / "filemindr.yaml"
    _write_yaml(
        config_path,
        f"""
source: "{source.as_posix()}"
default_target: "{(tmp_path / "others").as_posix()}"
conflict_policy: rename

rules:
  - name: images_copy
    priority: 10
    match:
      extensions: ["png"]
    action:
      copy_to: "{target.as_posix()}"
""".lstrip(),
    )

    run_pipeline(str(config_path), dry_run=False)

    # source continua
    assert src_file.exists()

    # target recebeu cópia
    copied = target / "photo.png"
    assert copied.exists()
    assert copied.read_bytes() == b"fake-png"


def test_move_to_removes_source_and_moves_to_target(tmp_path: Path) -> None:
    source = tmp_path / "Downloads"
    target = tmp_path / "documents"
    source.mkdir(parents=True)
    target.mkdir(parents=True)

    src_file = source / "doc.txt"
    src_file.write_text("hello", encoding="utf-8")

    config_path = tmp_path / "filemindr.yaml"
    _write_yaml(
        config_path,
        f"""
source: "{source.as_posix()}"
default_target: "{(tmp_path / "others").as_posix()}"
conflict_policy: rename

rules:
  - name: docs_move
    priority: 10
    match:
      extensions: ["txt"]
    action:
      move_to: "{target.as_posix()}"
""".lstrip(),
    )

    run_pipeline(str(config_path), dry_run=False)

    # saiu do source
    assert not src_file.exists()

    # foi pro target
    moved = target / "doc.txt"
    assert moved.exists()
    assert moved.read_text(encoding="utf-8") == "hello"


def test_rule_cannot_have_move_to_and_copy_to(tmp_path: Path) -> None:
    source = tmp_path / "Downloads"
    source.mkdir(parents=True)

    config_path = tmp_path / "filemindr.yaml"
    _write_yaml(
        config_path,
        f"""
source: "{source.as_posix()}"
default_target: "{(tmp_path / "others").as_posix()}"
conflict_policy: rename

rules:
  - name: invalid_both
    priority: 10
    match:
      extensions: ["txt"]
    action:
      move_to: "{(tmp_path / "docs").as_posix()}"
      copy_to: "{(tmp_path / "docs_copy").as_posix()}"
""".lstrip(),
    )

    with pytest.raises(ValueError, match="must define exactly one"):
        run_pipeline(str(config_path), dry_run=False)


def test_rule_must_have_move_to_or_copy_to(tmp_path: Path) -> None:
    source = tmp_path / "Downloads"
    source.mkdir(parents=True)

    config_path = tmp_path / "filemindr.yaml"
    _write_yaml(
        config_path,
        f"""
source: "{source.as_posix()}"
default_target: "{(tmp_path / "others").as_posix()}"
conflict_policy: rename

rules:
  - name: invalid_none
    priority: 10
    match:
      extensions: ["txt"]
    action:
      # nenhum move_to/copy_to
      something_else: "x"
""".lstrip(),
    )

    with pytest.raises(ValueError, match="must define exactly one"):
        run_pipeline(str(config_path), dry_run=False)