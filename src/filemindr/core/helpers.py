import os
import sys
import shlex
import subprocess
from pathlib import Path


def _editor_cmd() -> list[str]:
    ed = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if ed:
        return shlex.split(ed)

    if sys.platform.startswith("win"):
        return ["notepad.exe"]
    if sys.platform == "darwin":
        return ["open", "-e"]
    return ["nano"]


def open_in_editor(path: Path) -> None:
    cmd = _editor_cmd() + [str(path)]
    subprocess.run(cmd, check=False)


def open_with_default_app(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
        return
    subprocess.run(["xdg-open", str(path)], check=False)
