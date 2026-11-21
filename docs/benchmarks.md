# Benchmark methodology and results

These are **historical, pre-review measurements**. The independent pre-merge
review subsequently added source claiming, additional verification, and failed
watch-attempt suppression. The tables and raw JSON remain unchanged evidence
of the earlier implementation; they are not timings for the reviewed executor.
Rerun the benchmark serially before claiming current throughput or memory use.

Measured locally on 2026-11-21. Raw data is stored in
[benchmark-results.json](benchmark-results.json) (baseline and initial hardening),
[benchmark-final-results.json](benchmark-final-results.json) (final destination
verification), and [watch-buffer-results.json](watch-buffer-results.json).

## Environment

- Windows 11, build 26300, NTFS on drive C:.
- Intel Core i7-13620H, 16 logical CPUs; 34,085,175,296 bytes RAM (31.74 GiB).
- CPython 3.12.9, 64-bit; uv 0.7.5; locked project dependencies.
- All fixtures under `.benchmark-work` inside the repository; no real downloads,
  profile files, or external user data. Temporary trees are cleaned after each
  benchmark invocation. No physical cross-device move is measured.

## Method

```powershell
uv run python scripts/benchmark.py
uv run python scripts/benchmark.py --versions hardened --output docs/benchmark-final-results.json
uv run python scripts/benchmark_watch_buffer.py
```

Each worker creates 100, 10,000, or 100,000 separate eight-byte files, then measures
shallow scanning, matching 20 rules, full dry-run planning, and actual moves to
an unused directory on the same volume. Nineteen extension predicates miss; the
last extension/regex rule matches. File creation, import/setup, verification
assertions, and cleanup are outside timed stages. Logs are disabled. History
append overhead is measured separately using the same number of representative
move events and a fake home; it does not include file operations or identity
hashing. It therefore does not represent the full profiled-run cost.

The baseline code is extracted read-only from commit `eef6fb9`; no checkout of
`development` or mutation of baseline source is required. The second invocation
measures the final extra destination verification before source deletion. Use
baseline rows from the first JSON and final hardened rows from the second for
the table below.

`tracemalloc` is enabled throughout timed stages. Peak memory means traced Python
allocations during that stage, **not total process RSS**, filesystem cache, or
native library memory. Scanning/matching allocate path names too. These are
single samples with warm caches; no confidence interval or cold-cache control is
claimed. Local tests/package checks and part of the final rerun overlapped other
benchmark work, so timing comparisons are indicative and noisy. Repeat serially
on an idle machine before making performance guarantees.

## Measurements

| Version | Files | Scan seconds (files/s) | Match 20 rules, seconds | Dry-run seconds | Move seconds | History seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | 100 | 0.0027 (37,064) | 0.0013 | 0.078 | 0.121 | 0.059 |
| Final hardened | 100 | 0.0024 (42,212) | 0.0014 | 0.096 | 0.316 | 0.035 |
| Baseline | 10,000 | 0.2225 (44,940) | 0.1010 | 5.548 | 14.405 | 8.240 |
| Final hardened | 10,000 | 0.1755 (56,988) | 0.0766 | 7.083 | 27.268 | 3.845 |
| Baseline | 100,000 | 1.9323 (51,752) | 0.8465 | 54.041 | 108.702 | 69.931 |
| Final hardened | 100,000 | 2.1348 (46,842) | 0.8543 | 68.218 | 271.442 | 31.072 |

| Version | Files | Dry-run peak traced MiB | Actual move peak traced MiB |
| --- | ---: | ---: | ---: |
| Baseline | 100 | 0.172 | 0.169 |
| Final hardened | 100 | 0.173 | 0.300 |
| Baseline | 10,000 | 5.518 | 7.269 |
| Final hardened | 10,000 | 9.610 | 0.909 |
| Baseline | 100,000 | 55.884 | 64.280 |
| Final hardened | 100,000 | 98.641 | 7.983 |

The final 100,000-file worker's OS peak working set, observed during execution,
was **277.2 MiB**. That counter includes the whole worker and tracing overhead;
it is not an isolated stage measurement or a baseline comparison. The raw
[process-memory snapshot](benchmark-process-memory.json) records the timestamp
and byte values. Native allocations, Python allocator retention, and tracing
overhead explain why traced stage peaks should not be read as total RSS.

At 100,000 files, actual moves took longer while history writes and traced
actual-run memory improved. Dry-run took longer and used more memory to reserve
destinations. No statistical comparison of the additional identity check is
possible from these single noisy samples.

## Watcher handoff burst microbenchmark

| Submitted unique events | Capacity | Peak pending paths | Seconds | Peak traced MiB |
| ---: | ---: | ---: | ---: | ---: |
| 100 | 1,000 | 100 | 0.020 | 0.059 |
| 10,000 | 1,000 | 1,000 | 0.250 | 1.439 |
| 100,000 | 1,000 | 1,000 | 2.729 | 2.136 |

This measures the bounded producer/consumer handoff with synthetic paths and a
consumer draining every millisecond. It verifies all events are consumed and
the capacity is respected. It excludes native watchfiles delivery, on-disk
stability sampling, and pipeline execution. End-to-end OS watcher throughput and
overflow behavior are not measured. Regression tests separately exercise event
deduplication, file writes during settling, shutdown, and a slow pipeline.

## Interpretation and trade-offs

History no longer rewrites aggregate metadata for every event; counts are
finalized at completion and JSONL remains the live audit log. Actual runs avoid
accumulating report events when no JSON report was requested. Those changes
address confirmed I/O and allocation costs without changing rule matching.

Moves now hash identities repeatedly and verify the published destination before
removing the source. Expect additional latency and full-file reads; eight-byte
fixtures do not predict performance on videos or archives. Dry-run keeps a set
of reserved destination paths so duplicate names are planned consistently, which
increases memory compared with the original optimistic dry-run. Matching itself
was not optimized because no rule-matching bottleneck was demonstrated.

Additional measurements should cover representative large files, copies and
replacement staging, pathological regexes, heavy name collisions, native watcher
overflow, total process RSS, and real external/network volumes. None of these
results demonstrates universally atomic or lossless operations.
