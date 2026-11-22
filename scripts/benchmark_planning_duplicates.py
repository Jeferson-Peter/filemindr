from __future__ import annotations

import argparse
import ctypes
import gc
import json
import os
import platform
import subprocess
import sys
import threading
import time
import tracemalloc
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from filemindr.core.duplicates import scan_duplicates
from filemindr.core.runner import run_pipeline

ROOT = Path(__file__).resolve().parents[1]
COUNTS = (100, 10_000, 100_000)


def _current_rss_bytes() -> int | None:
    if os.name == "nt":

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(Counters),
            ctypes.c_ulong,
        ]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        process = kernel32.GetCurrentProcess()
        ok = psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
        return counters.WorkingSetSize if ok else None
    statm = Path("/proc/self/statm")
    if statm.exists():
        resident_pages = int(statm.read_text(encoding="ascii").split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE")
    try:
        output = subprocess.check_output(
            ["ps", "-o", "rss=", "-p", str(os.getpid())], text=True
        )
        return int(output.strip()) * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _measure(operation):
    gc.collect()
    tracemalloc.start()
    tracemalloc.clear_traces()
    baseline_rss = _current_rss_bytes()
    peak_rss = [baseline_rss]
    stop = threading.Event()

    def sample_rss() -> None:
        while not stop.wait(0.01):
            value = _current_rss_bytes()
            if value is not None and (peak_rss[0] is None or value > peak_rss[0]):
                peak_rss[0] = value

    sampler = threading.Thread(target=sample_rss, daemon=True)
    sampler.start()
    tracemalloc.reset_peak()
    started = time.perf_counter()
    result = operation()
    elapsed = time.perf_counter() - started
    _current, traced_peak = tracemalloc.get_traced_memory()
    current_rss = _current_rss_bytes()
    if current_rss is not None and (peak_rss[0] is None or current_rss > peak_rss[0]):
        peak_rss[0] = current_rss
    stop.set()
    sampler.join()
    tracemalloc.stop()
    return result, {
        "seconds": round(elapsed, 6),
        "python_traced_peak_bytes": traced_peak,
        "process_rss_baseline_bytes": baseline_rss,
        "process_rss_sampled_peak_bytes": peak_rss[0],
        "rss_sampling_interval_ms": 10,
    }


def _workload(count: int) -> dict:
    print(f"Preparing {count} temporary files", flush=True)
    with TemporaryDirectory(
        prefix=f"filemindr-{count}-", dir=ROOT / ".benchmark-work"
    ) as tmp:
        root = Path(tmp)
        source = root / "source"
        source.mkdir()
        for index in range(count):
            (source / f"item-{index:06d}.dat").write_bytes(b"payload!")
        config = root / "rules.yaml"
        config.write_text(
            yaml.safe_dump(
                {
                    "source": str(source),
                    "default_target": str(root / "sorted"),
                    "conflict_policy": "rename",
                    "rules": [],
                }
            ),
            encoding="utf-8",
        )

        print(f"Measuring {count}-file planning", flush=True)
        dry_summary, planning_memory = _measure(
            lambda: run_pipeline(str(config), dry_run=True)
        )
        planning_memory["summary"] = dry_summary
        gc.collect()
        print(f"Measuring {count}-file duplicate scan", flush=True)
        duplicate_result, duplicate_memory = _measure(lambda: scan_duplicates(source))
        duplicate_summary = {
            "files_scanned": duplicate_result.files_scanned,
            "files_hashed": duplicate_result.files_hashed,
            "groups": len(duplicate_result.groups),
            "largest_group": max(
                (len(group.paths) for group in duplicate_result.groups), default=0
            ),
            "unverified": len(duplicate_result.unverified),
        }
        return {
            "files": count,
            "planning": {**planning_memory, "summary": {"planned_moves": count}},
            "duplicates": {**duplicate_memory, "summary": duplicate_summary},
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure planner and duplicate-scan costs"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "planning-duplicate-benchmarks.json",
    )
    args = parser.parse_args()
    (ROOT / ".benchmark-work").mkdir(exist_ok=True)
    from loguru import logger

    logger.disable("filemindr")
    result = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
            "memory_method": "10ms sampled current RSS (process APIs; not tracemalloc)",
        },
        "workloads": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for count in COUNTS:
        workload = _workload(count)
        result["workloads"].append(workload)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        planning = workload["planning"]
        duplicate = workload["duplicates"]
        print(
            f"{count}: planning {planning['seconds']:.3f}s / "
            f"{planning['python_traced_peak_bytes'] / 1048576:.2f} MiB traced; "
            f"duplicates {duplicate['seconds']:.3f}s / "
            f"{duplicate['python_traced_peak_bytes'] / 1048576:.2f} MiB traced",
            flush=True,
        )
    print(f"Wrote measurements to {args.output}")


if __name__ == "__main__":
    main()
