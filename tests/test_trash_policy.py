from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import filemindr.core.runner as runner


def test_trash_calls_send2trash(monkeypatch, tmp_path: Path):
    target = tmp_path / "existing.txt"
    target.write_text("old", encoding="utf-8")

    calls = {"n": 0, "arg": None}

    def fake_send2trash(arg: str):
        calls["n"] += 1
        calls["arg"] = arg

    monkeypatch.setitem(
        runner.__dict__,
        "send2trash",
        SimpleNamespace(send2trash=fake_send2trash),
    )

    import sys
    sys.modules["send2trash"] = SimpleNamespace(send2trash=fake_send2trash)

    runner._trash(target)

    assert calls["n"] == 1
    assert calls["arg"] == str(target)


def test_trash_falls_back_to_unlink_when_send2trash_fails(monkeypatch, tmp_path: Path):
    target = tmp_path / "existing.txt"
    target.write_text("old", encoding="utf-8")

    def failing_send2trash(_: str):
        raise RuntimeError("boom")

    import sys
    sys.modules["send2trash"] = SimpleNamespace(send2trash=failing_send2trash)

    unlinked = {"n": 0}

    real_unlink = Path.unlink

    def fake_unlink(self, missing_ok: bool = False):
        unlinked["n"] += 1
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fake_unlink, raising=True)

    runner._trash(target)

    assert unlinked["n"] == 1
    assert not target.exists()

def test_policy_trash_replaces_existing_destination(monkeypatch, tmp_path: Path):
    """
    Integração leve: simula conflito e garante que:
    - o destino existente é "trashed"
    - o novo arquivo é copiado/movido para o destino final
    """
    source_dir = tmp_path / "Downloads"
    source_dir.mkdir()

    dest_dir = tmp_path / "images"
    dest_dir.mkdir()

    src = source_dir / "pic.jpg"
    src.write_text("new", encoding="utf-8")

    dest = dest_dir / "pic.jpg"
    dest.write_text("old", encoding="utf-8")

    cfg = tmp_path / "filemindr.yaml"
    cfg.write_text(
        f"""
source: {source_dir}
default_target: {tmp_path / "others"}
conflict_policy: trash
rules:
  - name: images
    priority: 10
    match:
      extensions: ["jpg"]
    action:
      copy_to: {dest_dir}
""".strip(),
        encoding="utf-8",
    )

    trashed = {"n": 0, "arg": None}

    def fake_trash(p: Path):
        trashed["n"] += 1
        trashed["arg"] = p
        if p.exists():
            p.unlink()

    monkeypatch.setattr(runner, "_trash", fake_trash)

    runner.run_pipeline(str(cfg), dry_run=False)

    assert trashed["n"] == 1
    assert trashed["arg"] == dest
    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == "new"