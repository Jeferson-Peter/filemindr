"""Run using the installed wheel/sdist interpreter with `python -I`."""

from __future__ import annotations

import importlib.resources
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import filemindr
import yaml


def main() -> None:
    installed = Path(filemindr.__file__).resolve()
    assert "site-packages" in str(installed), installed
    assert importlib.resources.files("filemindr").joinpath("py.typed").is_file()
    from filemindr.core.templates.yaml_tmpl import DEFAULT_CONFIG

    assert "source:" in DEFAULT_CONFIG
    executable = Path(sys.executable).parent / (
        "filemindr.exe" if os.name == "nt" else "filemindr"
    )
    subprocess.run([str(executable), "--help"], check=True, capture_output=True)
    duplicate_help = subprocess.run(
        [str(executable), "duplicates", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "directory" in duplicate_help.stdout
    subprocess.run(
        [sys.executable, "-I", "-m", "filemindr", "--help"],
        check=True,
        capture_output=True,
    )
    with tempfile.TemporaryDirectory(prefix="filemindr-package-") as temporary:
        root = Path(temporary)
        source = root / "input"
        source.mkdir()
        original = source / "sample.txt"
        original.write_text("package smoke test", encoding="utf-8")
        rules = root / ".filemindr" / "rules" / "sample"
        rules.mkdir(parents=True)
        (rules / "rules.yaml").write_text(
            yaml.safe_dump(dict(source=str(source), default_target="output"))
        )
        (rules.parent.parent / "profiles.yaml").write_text(
            yaml.safe_dump(dict(profiles=dict(sample=str(rules))))
        )
        environment = os.environ | {"HOME": str(root), "USERPROFILE": str(root)}

        def cli(*args):
            return subprocess.run(
                [str(executable), *args],
                env=environment,
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )

        cli("run", "-p", "sample", "--dry-run", "--report", str(root / "dry.json"))
        assert original.exists()
        cli("run", "-p", "sample")
        assert not original.exists()
        run_id = cli("history", "list").stdout.split("|", 1)[0].strip()
        cli("undo", run_id)
        assert original.read_text() == "package smoke test"
    print(f"Installed CLI, templates, dry-run, move and undo passed: {installed}")


if __name__ == "__main__":
    main()
