"""Repeatable local synthetic benchmark. Creates/removes only its own temp tree."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path


def measure(call):
    tracemalloc.start()
    start = time.perf_counter()
    value = call()
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "seconds": round(elapsed, 6),
        "peak_python_mib": round(peak / 2**20, 3),
    }, value


def worker(args):
    sys.path.insert(0, str(Path(args.source_root).resolve()))
    import yaml
    from loguru import logger
    from filemindr.core.history import HistoryWriter
    from filemindr.core.runner import _load_rules, _match_rule, run_pipeline

    logger.remove()
    work = Path(args.work).resolve()
    source = work / "input"
    source.mkdir()
    for index in range(args.count):
        (source / f"file-{index:06d}.txt").write_bytes(b"data1234")
    rules = [
        dict(
            name=f"miss-{i}",
            priority=20 - i,
            match=dict(extensions=[f"unused{i}"]),
            action=dict(move_to="output"),
        )
        for i in range(19)
    ]
    rules.append(
        dict(
            name="text",
            priority=0,
            match=dict(extensions=["txt"], regex="^file-"),
            action=dict(move_to="output"),
        )
    )
    config = dict(source=str(source), default_target="output", rules=rules)
    cfg = work / "rules.yaml"
    cfg.write_text(yaml.safe_dump(config), encoding="utf-8")
    loaded = _load_rules(config)
    results = {"files": args.count}
    results["scan"], count = measure(lambda: sum(p.is_file() for p in source.iterdir()))
    assert count == args.count
    results["scan"]["files_per_second"] = round(args.count / results["scan"]["seconds"])
    results["matching_20_rules"], count = measure(
        lambda: sum(_match_rule(p, loaded) is not None for p in source.iterdir())
    )
    assert count == args.count
    results["dry_run"], _ = measure(lambda: run_pipeline(str(cfg), dry_run=True))
    results["move"], summary = measure(lambda: run_pipeline(str(cfg)))
    assert not list(source.iterdir())
    assert sum(1 for _ in (work / "output").iterdir()) == args.count
    if summary is not None:
        assert summary["errors"] == 0
    # Measure journal overhead independently from file I/O, using a realistic event.
    Path.home = classmethod(lambda cls: work / "home")

    def write_history():
        writer = HistoryWriter(
            profile="benchmark",
            command="run",
            config_path=cfg,
            source=source,
            dry_run=False,
        )
        for index in range(args.count):
            writer.record(
                dict(
                    event="moved",
                    source=str(source / f"file-{index:06d}.txt"),
                    destination=str(work / "output" / f"file-{index:06d}.txt"),
                    undo_supported=False,
                )
            )
        writer.complete()

    results["history"], _ = measure(write_history)
    print(json.dumps(results), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--counts", nargs="+", type=int, default=[100, 10_000, 100_000])
    parser.add_argument("--baseline", default="eef6fb9")
    parser.add_argument(
        "--versions",
        nargs="+",
        choices=["baseline", "hardened"],
        default=["baseline", "hardened"],
    )
    parser.add_argument("--output", default="docs/benchmark-results.json")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--source-root")
    parser.add_argument("--work")
    parser.add_argument("--count", type=int)
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    root = Path(__file__).resolve().parents[1]
    assert Path.cwd().resolve() == root, "Run from the project root"
    work_root = root / ".benchmark-work"
    work_root.mkdir(exist_ok=True)
    assert work_root.resolve().is_relative_to(root)
    results = dict(
        environment=dict(
            python=sys.version,
            platform=platform.platform(),
            processor=platform.processor(),
            cpu_count=os.cpu_count(),
            timestamp_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            file_bytes=8,
            rules=20,
            repeats=1,
            tracing="tracemalloc enabled during all timed stages",
        ),
        baseline_ref=args.baseline,
        measurements=[],
    )
    with tempfile.TemporaryDirectory(prefix="run-", dir=work_root) as temporary:
        temporary_root = Path(temporary)
        baseline = temporary_root / "baseline" / "src"
        paths = subprocess.check_output(
            ["git", "ls-tree", "-r", "--name-only", args.baseline, "src"], text=True
        ).splitlines()
        for relative in paths:
            target = baseline.parent / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(
                subprocess.check_output(["git", "show", f"{args.baseline}:{relative}"])
            )
        for version, code in (("baseline", baseline), ("hardened", root / "src")):
            if version not in args.versions:
                continue
            for count in args.counts:
                case = temporary_root / f"{version}-{count}"
                case.mkdir()
                output = subprocess.check_output(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--worker",
                        "--source-root",
                        str(code),
                        "--work",
                        str(case),
                        "--count",
                        str(count),
                    ],
                    text=True,
                )
                result = {"version": version, **json.loads(output)}
                results["measurements"].append(result)
                Path(args.output).write_text(
                    json.dumps(results, indent=2), encoding="utf-8"
                )
                print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
