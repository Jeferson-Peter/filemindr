import sys
import typer
from loguru import logger

from filemindr.core.config import resolve_config
from filemindr.core.runner import run_pipeline

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


if __name__ == "__main__":
    app()