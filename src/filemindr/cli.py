import sys
from pathlib import Path

import typer
import yaml
from loguru import logger

from filemindr.core.config import resolve_profile_config, expand_path
from filemindr.core.explain import explain_files, format_explain
from filemindr.core.history import clear_history, find_run, load_run_events, iter_run_meta, prune_history, retention_days_default, undo_run
from filemindr.core.helpers import open_in_editor, open_with_default_app
from filemindr.core.ignore import load_ignore_patterns
from filemindr.core.runner import run_pipeline
from filemindr.core.templates.yaml_tmpl import DEFAULT_CONFIG
from filemindr.core.validator import validate_config_file
from filemindr.core.watcher import WatchOptions, watch_and_run


app = typer.Typer(help="Declarative local file pipelines")
profile_app = typer.Typer(help="Manage profiles")
history_app = typer.Typer(help="Inspect pipeline history")
app.add_typer(profile_app, name="profile")
app.add_typer(history_app, name="history")


def _setup_logger(level: str) -> None:
    logger.remove()
    logger.add(
        sys.stdout,
        level=level.upper(),
        colorize=True,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
               "<level>{level}</level> | "
               "{message}",
    )


def _home() -> Path:
    return Path.home() / ".filemindr"


def _profiles_file() -> Path:
    return _home() / "profiles.yaml"


@app.command()
def run(
    profile: str = typer.Option(..., "--profile", "-p"),
    dry_run: bool = False,
    report: Path | None = typer.Option(None, "--report", help="Write a JSON execution report to this path"),
    log_level: str = typer.Option("INFO"),
):
    _setup_logger(log_level)

    config_path = resolve_profile_config(profile)
    logger.info(f"Running pipeline | profile={profile} config={config_path} dry_run={dry_run}")

    run_pipeline(str(config_path), dry_run, profile=profile, command="run", report_path=report)


@app.command()
def watch(
    profile: str = typer.Option(..., "--profile", "-p"),
    dry_run: bool = False,
    debounce_ms: int = 500,
    stable_ms: int = 1500,
    once: bool = typer.Option(False, "--once"),
    log_level: str = typer.Option("INFO"),
):
    _setup_logger(log_level)

    config_path = resolve_profile_config(profile)
    logger.info(f"Starting watch | profile={profile} config={config_path} dry_run={dry_run}")

    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    source_dir = expand_path(cfg["source"], base_dir=Path(config_path).parent)
    ignore_patterns = load_ignore_patterns(cfg)

    opts = WatchOptions(debounce_ms=debounce_ms, stable_ms=stable_ms, ignore_patterns=ignore_patterns)

    watch_and_run(
        source_dir=source_dir,
        config_path=str(config_path),
        dry_run=dry_run,
        opts=opts,
        once=once,
        profile=profile,
    )


@app.command()
def explain(
    paths: list[Path] = typer.Argument(...),
    profile: str = typer.Option(..., "--profile", "-p"),
    fmt: str = typer.Option("one", "--format", "-f"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    limit: int = typer.Option(50),
    all: bool = typer.Option(False, "--all"),
    log_level: str = typer.Option("INFO"),
):
    _setup_logger(log_level)

    config_path = resolve_profile_config(profile)

    expanded: list[Path] = []
    for p in paths:
        if p.is_dir():
            expanded.extend(sorted(p.iterdir()))
        else:
            expanded.append(p)

    if not all and len(expanded) > limit:
        typer.echo(f"Showing first {limit} files")
        expanded = expanded[:limit]

    results = explain_files(str(config_path), expanded, verbose=verbose)

    for r in results:
        logger.info(format_explain(r, fmt=fmt, verbose=verbose))


@app.command()
def validate(
    profile: str = typer.Option(..., "--profile", "-p"),
    log_level: str = typer.Option("INFO"),
):
    _setup_logger(log_level)

    config_path = resolve_profile_config(profile)
    result = validate_config_file(Path(config_path))

    if result.ok:
        logger.info("Config is valid [OK]")
        raise typer.Exit(code=0)

    logger.error("Config is invalid [ERROR]")
    for err in result.errors:
        logger.error(f"- {err}")

    raise typer.Exit(code=1)


@app.command()
def undo(
    run_id: str = typer.Argument(...),
    log_level: str = typer.Option("INFO"),
):
    _setup_logger(log_level)

    result = undo_run(run_id)
    logger.info(f"Undo completed | original_run={result.run_id} undo_run={result.undo_run_id}")
    logger.info(f"Reverted: {result.reverted}")
    logger.info(f"Skipped: {result.skipped}")
    logger.info(f"Errors: {result.errors}")


@profile_app.command("init")
def profile_init(
    name: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force"),
):
    """
    Create profile folder + rules.yaml and register in profiles.yaml
    """

    base = _home()
    rules_dir = base / "rules" / name
    rules_dir.mkdir(parents=True, exist_ok=True)

    rules_yaml = rules_dir / "rules.yaml"

    if rules_yaml.exists() and not force:
        typer.echo("rules.yaml already exists. Use --force.")
        raise typer.Exit(1)

    rules_yaml.write_text(DEFAULT_CONFIG, encoding="utf-8")

    profiles_file = _profiles_file()
    profiles_file.parent.mkdir(parents=True, exist_ok=True)

    if profiles_file.exists():
        data = yaml.safe_load(profiles_file.read_text()) or {}
    else:
        data = {}

    data.setdefault("profiles", {})[name] = str(rules_dir)

    profiles_file.write_text(yaml.safe_dump(data, sort_keys=False))

    typer.echo(f"Profile '{name}' created.")
    typer.echo(f"Rules: {rules_yaml}")
    typer.echo(f"Registered in {profiles_file}")


@history_app.command("list")
def history_list(
    limit: int = typer.Option(20, "--limit", "-n"),
    all_runs: bool = typer.Option(False, "--all", help="Include legacy/internal runs without a command label"),
):
    items = iter_run_meta()
    if not all_runs:
        items = [item for item in items if item.get("command")]
    if not items:
        typer.echo("No history found.")
        raise typer.Exit()

    for item in items[:limit]:
        typer.echo(
            f"{item['run_id']} | command={item.get('command') or '-'} | profile={item.get('profile') or '-'} | "
            f"status={item.get('status')} | dry_run={item.get('dry_run')} | "
            f"started_at={item.get('started_at')}"
        )


@history_app.command("show")
def history_show(run_id: str = typer.Argument(...)):
    item = find_run(run_id)
    if not item:
        typer.echo(f"Run '{run_id}' not found.")
        raise typer.Exit(1)

    typer.echo(f"run_id:      {item.get('run_id')}")
    typer.echo(f"command:     {item.get('command') or '-'}")
    typer.echo(f"profile:     {item.get('profile') or '-'}")
    typer.echo(f"status:      {item.get('status')}")
    typer.echo(f"dry_run:     {item.get('dry_run')}")
    typer.echo(f"started_at:  {item.get('started_at')}")
    typer.echo(f"finished_at: {item.get('finished_at')}")
    typer.echo(f"source:      {item.get('source')}")
    typer.echo(f"config_path: {item.get('config_path')}")
    typer.echo("")
    typer.echo("counts:")
    for key, value in sorted((item.get("counts") or {}).items()):
        typer.echo(f"  {key}: {value}")

    events = load_run_events(run_id)
    if not events:
        return

    typer.echo("")
    typer.echo("events:")
    for event in events:
        event_name = event.get("event")
        source = event.get("source") or event.get("path") or "-"
        destination = event.get("destination") or "-"
        typer.echo(f"  - {event_name}: {source} -> {destination}")


@history_app.command("prune")
def history_prune(days: int = typer.Option(retention_days_default(), "--days")):
    removed = prune_history(days=days)
    typer.echo(f"Removed {removed} run(s).")


@history_app.command("clear")
def history_clear(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    if not yes and not typer.confirm("Delete all stored history entries?"):
        raise typer.Exit()

    removed = clear_history()
    typer.echo(f"Removed {removed} run(s).")


@profile_app.command("list")
def profile_list(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show main settings (source/default_target/conflict_policy)"),
):
    """
    List all profiles
    """
    pf = _profiles_file()

    if not pf.exists():
        typer.echo("profiles.yaml not found. Run: filemindr profile init <name>")
        raise typer.Exit(1)

    data = yaml.safe_load(pf.read_text(encoding="utf-8")) or {}
    profiles = data.get("profiles", {})

    if not profiles:
        typer.echo("No profiles defined.")
        raise typer.Exit()

    for name in sorted(profiles.keys()):
        rules_dir = Path(profiles[name]).expanduser()
        rules_yaml = rules_dir / "rules.yaml"

        if not verbose:
            typer.echo(f"{name} -> {rules_dir}")
            continue

        if not rules_yaml.exists():
            typer.echo(f"{name} -> {rules_dir}  (rules.yaml missing)")
            continue

        cfg = yaml.safe_load(rules_yaml.read_text(encoding="utf-8")) or {}
        typer.echo(f"{name} -> {rules_dir}")
        typer.echo(f"  source:         {cfg.get('source')}")
        typer.echo(f"  default_target: {cfg.get('default_target')}")
        typer.echo(f"  conflict_policy:{cfg.get('conflict_policy')}")


@profile_app.command("edit")
def profile_edit(name: str = typer.Argument(...)):
    """
    Open profile rules.yaml in an editor (VISUAL/EDITOR or OS default fallback).
    """
    rules_yaml = Path(resolve_profile_config(name))
    open_in_editor(rules_yaml)


@profile_app.command("open")
def profile_open(name: str = typer.Argument(...)):
    """
    Open profile rules.yaml with the OS default application.
    """
    rules_yaml = Path(resolve_profile_config(name))
    open_with_default_app(rules_yaml)


@profile_app.command("show")
def profile_show(name: str = typer.Argument(...)):
    """
    Show resolved paths and main settings of a profile.
    """
    rules_yaml = Path(resolve_profile_config(name))
    cfg = yaml.safe_load(rules_yaml.read_text(encoding="utf-8")) or {}

    typer.echo(f"profile:         {name}")
    typer.echo(f"profile_dir:     {rules_yaml.parent}")
    typer.echo(f"rules.yaml:      {rules_yaml}")
    typer.echo("")
    typer.echo(f"source:          {cfg.get('source')}")
    typer.echo(f"default_target:  {cfg.get('default_target')}")
    typer.echo(f"conflict_policy: {cfg.get('conflict_policy')}")


@profile_app.command("remove")
def profile_remove(
    name: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """
    Completely remove profile: registry entry + folder (rules.yaml, etc).
    """
    pf = _profiles_file()
    if not pf.exists():
        typer.echo("profiles.yaml not found. Run: filemindr profile init <name>")
        raise typer.Exit(1)

    data = yaml.safe_load(pf.read_text(encoding="utf-8")) or {}
    profiles = data.get("profiles", {})

    if name not in profiles:
        typer.echo(f"Profile '{name}' not found.\n\nCreate it with:\n  filemindr profile init {name}")
        raise typer.Exit(1)

    profile_dir = Path(profiles[name]).expanduser()

    if not yes:
        if not typer.confirm(f"Delete profile '{name}' and ALL its files at:\n{profile_dir}\nContinue?"):
            raise typer.Exit()

    del profiles[name]
    pf.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    if profile_dir.exists():
        import shutil
        shutil.rmtree(profile_dir)

    typer.echo(f"Profile '{name}' completely removed.")



@app.command()
def doctor():
    """
    Sanity checks on all profiles:
    - profiles.yaml exists
    - profile directory exists
    - rules.yaml exists
    - YAML validates
    - source/default_target paths resolve and exist (warn/create suggestion)
    """
    pf = _profiles_file()

    if not pf.exists():
        typer.echo("profiles.yaml not found. Run: filemindr profile init <name>")
        raise typer.Exit(1)

    data = yaml.safe_load(pf.read_text(encoding="utf-8")) or {}
    profiles = data.get("profiles", {})

    if not profiles:
        typer.echo("No profiles defined.")
        raise typer.Exit()

    ok = True

    for name in sorted(profiles.keys()):
        rules_dir = Path(profiles[name]).expanduser()
        rules_yaml = rules_dir / "rules.yaml"

        if not rules_dir.exists():
            typer.echo(f"[ERROR] {name}: profile directory not found -> {rules_dir}")
            ok = False
            continue

        if not rules_yaml.exists():
            typer.echo(f"[ERROR] {name}: rules.yaml missing -> {rules_yaml}")
            ok = False
            continue

        result = validate_config_file(rules_yaml)
        if not result.ok:
            typer.echo(f"[ERROR] {name}: invalid config")
            for err in result.errors:
                typer.echo(f"   - {err}")
            ok = False
            continue

        cfg = yaml.safe_load(rules_yaml.read_text(encoding="utf-8")) or {}
        source = cfg.get("source")
        default_target = cfg.get("default_target")

        source_path = expand_path(source, base_dir=rules_yaml.parent) if isinstance(source, str) else None
        target_path = expand_path(default_target, base_dir=rules_yaml.parent) if isinstance(default_target, str) else None

        warn = False

        if source_path and not source_path.exists():
            typer.echo(f"[WARN] {name}: source does not exist -> {source_path}")
            warn = True

        if target_path and not target_path.exists():
            typer.echo(f"[WARN] {name}: default_target does not exist -> {target_path}")
            warn = True

        if warn:
            typer.echo(f"[OK] {name}: config valid (with warnings)")
        else:
            typer.echo(f"[OK] {name}: OK")

    if not ok:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
