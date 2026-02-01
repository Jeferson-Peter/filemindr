import sys
from pathlib import Path

import typer
from loguru import logger

from filemindr.core.config import resolve_config
from filemindr.core.runner import run_pipeline
from filemindr.core.watcher import WatchOptions, watch_and_run

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
    # Configura logging
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
):
    """
    Watch source dir and run the pipeline when files are ready.
    """

    logger.remove()
    logger.add(sys.stdout, level=log_level.upper())
    config_path = resolve_config(config)

    # opcional: log
    logger.info(f"Starting watch | config={config_path} dry_run={dry_run}")

    # Como teu runner já lê source do YAML, a gente também pode ler de lá,
    # mas pra manter simples: usa o mesmo source do YAML dentro do runner
    # e aqui você informa explicitamente o dir a observar.
    # Sugestão: parseia source do YAML também, mas dá pra começar assim:
    import yaml
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    source_dir = Path(cfg["source"]).expanduser()

    opts = WatchOptions(debounce_ms=debounce_ms, stable_ms=stable_ms)
    watch_and_run(source_dir=source_dir, config_path=str(config_path), dry_run=dry_run, opts=opts)


if __name__ == "__main__":
    app()