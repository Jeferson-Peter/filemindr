from pathlib import Path


def resolve_config(cli_path: str | None = None) -> Path:
    # 1. CLI explícito
    if cli_path:
        return Path(cli_path).expanduser().resolve()

    # 2. Local (cwd)
    local = Path.cwd() / "filemindr.yaml"
    if local.exists():
        return local.resolve()

    # 3. Global (~/.filemindr/config.yaml)
    global_cfg = Path.home() / ".filemindr" / "config.yaml"
    if global_cfg.exists():
        return global_cfg.resolve()

    raise FileNotFoundError(
        "No filemindr config found. "
        "Run `filemindr init` or provide a config path."
    )
