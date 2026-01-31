from pathlib import Path


def resolve_config(cli_path: str | None = None) -> Path:
    if cli_path:
        return Path(cli_path).expanduser()

    local = Path.cwd() / "filemindr.yaml"
    if local.exists():
        return local

    home = Path.home() / ".filemindr" / "config.yaml"
    if home.exists():
        return home

    raise FileNotFoundError("No filemindr config found.")
