import sys
from pathlib import Path

import typer
from loguru import logger

from filemindr.core.config import resolve_config
from filemindr.core.explain import explain_files, format_explain
from filemindr.core.runner import run_pipeline
from filemindr.core.templates.yaml_tmpl import DEFAULT_CONFIG
from filemindr.core.watcher import WatchOptions, watch_and_run
from filemindr.core.validator import validate_config_file


app = typer.Typer(help="Declarative local file pipelines")


@app.command()
def run(
    config: str = "filemindr.yaml",
    dry_run: bool = False,
    log_level: str = typer.Option("INFO", help="Log level: INFO or DEBUG"),
):
    """
    Run filemindr pipeline.
    """
    logger.remove()
    logger.add(sys.stdout, level=log_level.upper())

    config_path = resolve_config(config)

    logger.info(f"Running pipeline with config={config_path} dry_run={dry_run}")

    run_pipeline(str(config_path), dry_run)

@app.command()
def watch(
    config: str = "filemindr.yaml",
    dry_run: bool = False,
    debounce_ms: int = 500,
    stable_ms: int = 1500,
    log_level: str = typer.Option("INFO", help="Log level: INFO or DEBUG"),
    once: bool = typer.Option(False, "--once", help="Run once on first stable batch and exit")

):
    """
    Watch source dir and run the pipeline when files are ready.
    """

    logger.remove()
    logger.add(sys.stdout, level=log_level.upper())
    config_path = resolve_config(config)

    logger.info(f"Starting watch | config={config_path} dry_run={dry_run}")

    import yaml
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    source_dir = Path(cfg["source"]).expanduser()

    opts = WatchOptions(debounce_ms=debounce_ms, stable_ms=stable_ms)
    watch_and_run(source_dir=source_dir, config_path=str(config_path), dry_run=dry_run, opts=opts, once=once)

@app.command("init")
def init_config(
    path: Path = typer.Argument(Path(".")),
    global_: bool = typer.Option(False, "--global", help="Create global config at ~/.filemindr/config.yaml"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing config"),
    log_level: str = typer.Option("INFO", help="Log level: INFO or DEBUG"),
):
    logger.remove()
    logger.add(sys.stdout, level=log_level.upper())

    if global_:
        target = Path.home() / ".filemindr" / "config.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        path = path.expanduser().resolve()
        target = path / "filemindr.yaml"

    if target.exists() and not force:
        logger.error(f"{target.name} already exists. Use --force to overwrite.")
        raise typer.Exit(code=1)

    target.write_text(DEFAULT_CONFIG, encoding="utf-8")
    logger.info(f"Created {target}")

@app.command()
def explain(
    paths: list[Path] = typer.Argument(..., help="File paths to explain (one or more)"),
    config: str = "filemindr.yaml",
    fmt: str = typer.Option("one", "--format", "-f", help="one | short"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show match details and conflict info"),
    log_level: str = typer.Option("INFO", help="Log level: INFO or DEBUG"),
    limit: int = typer.Option(50, help="Max files to explain"),
    all: bool = typer.Option(False, "--all", help="Disable limit"),
):
    """
    Explain what filemindr would do with the given files (no changes are made).
    """
    logger.remove()
    logger.add(sys.stdout, level=log_level.upper())

    config_path = resolve_config(config)

    expanded: list[Path] = []

    for p in paths:
        if p.is_dir():
            expanded.extend(sorted(p.iterdir()))
        else:
            expanded.append(p)

    if not all and len(expanded) > limit:
        typer.echo(f"Showing first {limit} files (use --all or --limit to override)")
        expanded = expanded[:limit]


    results = explain_files(str(config_path), expanded, verbose=verbose)

    for r in results:
        logger.info(format_explain(r, fmt=fmt, verbose=verbose))

@app.command()
def validate(
    config: str = "filemindr.yaml",
    log_level: str = typer.Option("INFO", help="Log level: INFO or DEBUG"),
):
    """
    Validate filemindr YAML config without running the pipeline.
    """
    logger.remove()
    logger.add(sys.stdout, level=log_level.upper())

    config_path = resolve_config(config)

    result = validate_config_file(Path(config_path))

    if result.ok:
        logger.info("Config is valid ✅ ")
        raise typer.Exit(code=0)

    logger.error("Config is invalid ❌ ")
    for err in result.errors:
        logger.error(f"- {err}")

    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()