"""Inspect built package contents before installation/publication."""

from pathlib import Path
import tarfile
import zipfile


def main() -> None:
    expected = {
        "filemindr/cli.py",
        "filemindr/__main__.py",
        "filemindr/py.typed",
        "filemindr/core/operations.py",
        "filemindr/core/planning.py",
        "filemindr/core/duplicates.py",
        "filemindr/core/templates/yaml_tmpl.py",
    }
    wheels = list(Path("dist").glob("*.whl"))
    archives = list(Path("dist").glob("*.tar.gz"))
    assert len(wheels) == len(archives) == 1, (
        "Expected exactly one wheel and source archive"
    )
    with zipfile.ZipFile(wheels[0]) as archive:
        assert expected <= set(archive.namelist())
    with tarfile.open(archives[0]) as archive:
        names = {name.split("/", 1)[1] for name in archive.getnames() if "/" in name}
        assert {"src/" + path for path in expected} <= names
        assert "pyproject.toml" in names
        assert "report.json" not in names
        assert "AGENTS.md" not in names
        assert not any(
            ".package-check" in name or ".benchmark-work" in name for name in names
        )
    print("Distribution contents passed (all required modules; local reports excluded)")


if __name__ == "__main__":
    main()
