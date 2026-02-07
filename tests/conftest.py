from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from filemindr.cli import app


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    monkeypatch.setenv("HOMEDRIVE", fake_home.drive or "C:")

    homedir = str(fake_home)
    if ":" in homedir:
        homedir = homedir.split(":", 1)[1]
    homedir = homedir.replace("/", "\\")
    if not homedir.startswith("\\"):
        homedir = "\\" + homedir
    monkeypatch.setenv("HOMEPATH", homedir)

    return fake_home


@pytest.fixture()
def cli_app() -> typer.Typer:
    return app
