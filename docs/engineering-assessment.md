# Engineering assessment

Assessment baseline: `eef6fb9` (1.4.0), on a local branch descending from
`development`. Existing 1.1–1.4 commits were retained by fast-forward; the local
`development` ref is older. No remote refs are changed.

## Compatibility decisions recorded before implementation

- Keep CLI commands, YAML fields, priority ordering, and shallow directory scans.
- Refuse unsafe filename templates, malformed configuration, source symlinks,
  destination symlinks, and targets resolving directly to the watched source.
  Such configurations previously could execute unpredictably or affect other files.
- Undo will skip legacy moves without verifiable identity metadata. It must also
  skip moves that replaced another file: restoring only the incoming file would
  not reverse the whole operation.
- `rename` and `skip` must never overwrite a concurrently created destination.
  Filesystems without safe no-clobber publication support will fail closed.
- Per-file failures remain recorded and processing continues, but the CLI will
  return failure when a run contains errors.
- Report destinations must be unused paths; an existing report is refused before
  processing so `--report` cannot overwrite user data. Choose a fresh filename.
- Profile removal refuses external registry paths and symlink/junction roots.
- Publishing changes to manual dispatch on `development`, gated by a full CI
  matrix. Automatic version-bump publishing/tagging is removed. Configure the
  `pypi` environment's required reviewers separately in GitHub settings.

## Initial findings

| Severity | Evidence at baseline | Classification |
| --- | --- | --- |
| Critical | `runner.py` unlinks the old destination before copy/rename | Confirmed loss on replacement failure |
| High | `Path.rename()` is the only move implementation | Confirmed missing cross-device fallback |
| High | Undo checks existence only | Confirmed unrelated/modified-file recovery risk |
| High | Runtime does not call the validator; rendered names are unrestricted | Confirmed validation bypass and path escape |
| High | Conflict check and rename/copy are separate; POSIX rename replaces | Confirmed no-clobber race |
| High | `profile remove` trusts a registry path for recursive deletion | Confirmed unsafe deletion boundary |
| Medium | Watch queue is unbounded and stability checking loops indefinitely | Confirmed memory/shutdown risks |
| Medium | Unstable pending paths are revisited only after a new event | Confirmed stalled processing |
| Medium | Every history event rewrites metadata; reports accumulate without request | Confirmed avoidable I/O and memory costs; benchmark pending |
| Medium | Publishing tests only Ubuntu/3.12 on version bump and omits lint | Confirmed CI gap |
| High | Initial sdist inspection included the untracked local `report.json` and `AGENTS.md` | Confirmed unintended local-file packaging; explicit package allowlist added |
| Low | Unused import in `tests/test_rules.py` | Confirmed Ruff failure |

Baseline: `uv sync --extra dev` succeeded after sandbox cache/network retry;
`uv run filemindr --help` passed; pytest: **59 passed**; Ruff: **1 unused import**.

Power loss, hostile concurrent directory replacement, device failure, network
filesystem semantics, and preservation of ACLs/alternate streams require explicit
limitations; they are not demonstrated universal guarantees.

## Implemented changes and evidence

- Replaced destructive unlink-before-copy with destination-local staging,
  sync, size/SHA-256 verification, and replacement. Trash retains a private
  recovery copy until publication or ordinary rollback completes.
- Added an independently testable operation plan and executor. No-clobber
  publication uses filesystem primitives rather than a check followed by an
  overwriting copy. On Windows, staged publication uses non-overwriting rename;
  POSIX uses hard links. Same-filesystem moves use link-and-unlink, with a staged
  cross-device fallback. A changed published destination prevents source removal
  when detected before the final deletion.
- Partial-operation errors include a recovery path; unsuccessful source removal
  leaves the published destination and source for inspection. Runs with per-file
  errors get failed history status and a nonzero CLI exit status.
- Shared validation now runs before execution and watch startup. It rejects
  malformed mappings/lists, invalid policies and regexes, invalid integer fields,
  unsupported template syntax, and unsafe generated names. Runtime checks reject
  direct source/target overlap, aliases of the same file, and destination symlinks.
- New history records include identity metadata. Undo checks it, refuses occupied
  originals, skips legacy/unreversible events, and records successful reversals
  for replay protection. Interrupted final JSONL appends are tolerated; malformed
  complete records fail closed. Metadata is replaced as a complete document at
  start/finish instead of being rewritten on every event.
- Replaced the watcher's unbounded queue with a bounded deduplicated handoff.
  Stability sampling runs across pending paths; missing paths are removed and
  unstable paths are retried on ticks. A bounded processed cache suppresses
  unchanged duplicates. Signal handlers are restored and producer failures reach
  the caller. One-shot watch failures return failure.
- Restricted recursive profile deletion to the exact managed profile directory.
  History cleanup refuses directory symlinks/junctions. Added a real Windows
  junction regression test without touching directories outside test fixtures.
- Added `python -m filemindr`, explicit build inclusion patterns, archive
  inspection, isolated package smoke scripts, and a three-OS/three-Python CI
  matrix. Publishing is manual and waits for that matrix.

## Verification of the initial hardening snapshot

Commands are run from the documented project root, whose junction resolves to
`C:\Users\jefer\Dev\filemindr`. The original untracked `AGENTS.md` and `report.json`
were left untouched. No production profile or user-data pipeline was executed.
Tests use dedicated repository-local temporary roots and fake home directories.

| Check | Actual result |
| --- | --- |
| Baseline `uv sync --extra dev` | Passed after cache/network sandbox retry |
| Baseline `uv run pytest -q` | 59 passed |
| Baseline Ruff | Failed: one unused import, subsequently removed |
| Python 3.12 fault-injection suite | 131 passed, 2 skipped (3.61 s under coverage) |
| Python 3.14 locked dependency suite | 131 passed, 2 skipped (2.81 s) |
| Python 3.14 fresh dependency resolution | 127 passed, 2 skipped at that revision |
| Branch-aware coverage | 82% combined; operations 89%, runner 85%, watcher 90%; ephemeral `coverage` |
| Ruff and `git diff --check` | Passed |
| Console script and `python -m filemindr --help` | Passed |
| `uv build` and `uvx twine check dist/*` | Passed |
| Archive-content check | Passed after fixing local-file inclusion |
| Isolated wheel/sdist install | Passed; templates, typed marker, dry-run, move, undo |
| Linux/macOS, Python 3.13 | Configured in CI; not executed locally |
| Symlink file tests | Two skipped on Windows due to creation privileges |

Final commands include `uv run --with coverage coverage run --branch
--source=filemindr -m pytest -q --basetemp=<repository>/.pytest-work-coverage`,
`uv run --with coverage coverage report`, `uv run ruff check .`, `uv build`,
`uv run python scripts/check_distributions.py`, `uvx twine check dist/*`, and
`python -I scripts/check_installed.py` using each isolated installation's
interpreter. Python 3.14's locked environment was created with
`UV_PROJECT_ENVIRONMENT=<repository>/.package-check-314-locked uv sync --locked
--extra dev --python 3.14`; this environment did not replace `.venv`.

The locked environment uses Python 3.12.9/3.14.0, pytest 9.0.2, Ruff 0.14.14,
Typer 0.21.1, watchfiles 1.1.1, and uv 0.7.5. The isolated fresh installs also
exercised newer dependency resolutions without changing `uv.lock`.

Benchmark methodology, raw measurements, watcher handoff results, and safety
overhead are recorded in [benchmarks.md](benchmarks.md). These are local synthetic
measurements, not general throughput promises.

For 100,000 files the baseline/final move samples were 108.702/271.442 seconds;
history samples were 69.931/31.072 seconds. Actual-run traced Python allocation
peaks were 64.280/7.983 MiB, while dry-run peaks increased from 55.884 to
98.641 MiB. This is a deliberate correctness/performance trade-off, with noisy
single-sample timings and no promise of a general speedup.

## Remaining risks and limits

| Severity | Residual issue | Practical boundary / next improvement |
| --- | --- | --- |
| High | Check-to-replace/unlink races remain | No portable compare-and-swap filesystem API here; use cooperative writers and one organizer per source |
| High | A pipeline, cross-device move, or trash replacement is not a transaction | Errors expose partial state; inspect recovery paths and keep independent backups |
| High | Abrupt termination or power failure can interrupt publication/history | Staging data is synced; directories/history are not a durable transactional journal; no automatic crash replay |
| Medium | Hard-link support is required for same-filesystem fast moves and POSIX no-clobber publication | Unsupported filesystems fail closed; test actual network/FAT/mounted volumes before use |
| Medium | Destination ACLs/ownership/alternate streams/resource forks may change | `copy2` cannot preserve all metadata; suitability must be assessed for metadata-sensitive files |
| Medium | Hashing adds full-file reads; dry-run reservations use linear memory | Benchmark large files and many rename collisions before choosing optimizations |
| Medium | Native watcher event batches/overflow are outside the bounded handoff | No automatic reconciliation scan or durable queue; continuously busy files can apply sustained backpressure |
| Medium | Stability is observational, not a writer lock | Writers should finish to ignored temporary names and rename on completion |
| Medium | Undo replay protection depends on retained successful undo records | History pruning/clearing removes audit/replay information; filesystem identities must be reliable |
| Medium | Linux/macOS/3.13 and physical cross-device transfers remain unverified locally | Require green matrix and real-volume integration tests before a broad release |
| Low | Regex configuration is trusted; Python regex has no timeout here | Do not accept arbitrary untrusted patterns or promise bounded matching time |
| Low | No dedicated license file despite README's MIT declaration | Owner should confirm and add the intended license text before publishing |

Follow Python's documented [replacement semantics](https://docs.python.org/3.12/library/os.html#os.replace)
and [copy metadata limits](https://docs.python.org/3.12/library/shutil.html).
The native watcher's cancellation contract is documented in
[watchfiles](https://watchfiles.helpmanual.io/api/watch/).

## Merge and release assessment

Ready for local code review after the recorded checks. Broad cross-platform
release readiness remains conditional on remote matrix results, privileged
symlink coverage, real cross-volume integration, and acceptance of the documented
compatibility restrictions. No push, merge, tag, or publication was performed.
The dedicated branch is `JP/production-hardening`; changes are left uncommitted
for review. The pre-existing 1.4.0 commits are ahead of the local `development`
ref and must be considered when choosing the eventual merge base.

## Independent pre-merge review (2026-11-21)

Review base was refreshed to `origin/development` at
`1997df352614ea63d49fa4090a558aad05a6603c`. Its tree is identical to `HEAD` at
`eef6fb9`; the intervening remote commits are merges, not additional source
changes. The complete tracked diff and the untracked implementation, tests,
scripts, CI, and documentation were included. The untracked local `AGENTS.md`
and `report.json` remain outside the change under review.

Two existing integrity blockers were reproduced. Verification of source claiming
also exposed a watcher retry issue. All three were corrected:

| Severity | Confirmed failure | Correction and verification |
| --- | --- | --- |
| Critical | Moves and undo deleted an unrelated source replacement made during destination hashing, while reporting success | Claim source under a private same-directory name before deletion; verify the claimed file and published destination; preserve newly created source paths. Covers same-device moves, overwrite, cross-device fallback, undo, replacement after the last ordinary path check, and interrupted source claiming |
| High | Trash rollback's verification could raise for an unreadable or non-regular destination, after which `finally` deleted the verified recovery backup | Remove the backup from automatic cleanup before attempting recovery; retain and report it whenever rollback cannot be verified |
| High | Native Windows recovery notifications repeatedly retried a failed cross-volume move of a read-only file, producing three destination copies in a capped reproduction | Cache attempted signatures after failure as well as success; include permission mode in signatures. Native recheck produced exactly one failed attempt, then one successful attempt after clearing read-only status |

Source claiming can leave `.filemindr-*.bak` recovery files in the **source**
directory as well as the existing destination-side staging/recovery files.
Restoration never replaces a newly created source path; failures report the
private recovery path when restoration is unavailable. These remain local
filesystem primitives, not a multi-file transaction or a universal power-loss
guarantee. Active writers holding open handles and hostile private-path/directory
replacement remain outside the guarantee.

The new regressions are in `tests/test_premerge_integrity.py`. Six initial
integrity regressions failed before corrections. The watcher regression separately
reproduced `[failed, failed, successful]` attempts and now observes
`[failed, successful]` when permission mode changes.

Remaining findings were deliberately left without code changes:

| Severity | Finding | Evidence and impact |
| --- | --- | --- |
| Medium | Dry-run reserves leaf filenames, not the planned directory tree | With files `a.txt` and `b.txt`, plan a copy to `out/blocked`, then another into `out/blocked/b.txt`: dry-run reports two planned copies and zero errors; execution copies one and reports one error. Source data remains intact |
| Medium | Explain does not share dry-run reservations | Two inputs using `rename_template: same.txt` both explain to `same.txt`, while dry-run/execution choose `same.txt` and `same (1).txt` |
| Medium | Unsupported hard links, metadata preservation, native overflow, and filesystem-specific races remain platform limits | Fail-closed behavior is safer but is a compatibility restriction; Linux/macOS, physical cross-volume, network, and power-failure checks remain required |
| Low | Existing timings are historical and do not characterize the corrected executor | `docs/benchmarks.md` now labels the measurements accordingly; retain the raw data but rerun serially before making current performance claims |

History verification confirmed JSONL intent/completion ordering and completed
metadata counts equal to the event counter. Running metadata counts remain empty
until completion by design; interrupted runs can therefore have stale summary
counts. Append-close flushes userspace buffers, but neither the event log nor
metadata is fsynced as a durable transaction journal. No stronger recovery
guarantee is claimed.

Review verification: **142 passed, 2 skipped** on Windows with Python 3.12.9,
3.13.3, and 3.14.0 using locked dependencies. Branch-aware coverage remains
**82%** overall (operations 89%, runner 85%, history 86%, watcher 90%). Ruff,
whitespace checks, distribution inspection, Twine, and isolated wheel/sdist
installation checks passed. The two skips require symlink creation privileges.
Normal native Windows watching and the read-only recovery/retry scenario were
also exercised against disposable files, with EXDEV injected for the latter.

CLI/YAML compatibility restrictions from the initial assessment still apply.
Additionally, unchanged failed watch attempts are suppressed; fixing destination
conditions without changing the source can require a manual run or watcher
restart. No configuration field or CLI option was added by this review.

Merge verdict: the reproduced Critical/High blockers are fixed, but approval is
conditional on the full nine-cell CI matrix being green and acceptance of the
documented compatibility restrictions and remaining Medium planning issues.
Linux/macOS CI and physical filesystem integration cannot be inferred from local
Windows tests. No commit, push, merge, tag, or publication was performed.

## Reliability and feature evolution (2026-11-21)

### Shared execution planning

Added `core/planning.py` as the single path for rule priority, ignore handling,
template rendering, conflict policy, deterministic ordering, destination
reservations, and file/directory-tree checks. `run`, dry-run, and `explain` now
consume its structured `PlannedOperation` results. The two previously confirmed
medium findings are resolved: numbered output names are consistent in explain
and dry-run, and a planned file that blocks a later destination directory (or a
planned directory that blocks a file) is reported as an error in both planning
and execution.

Execution passes the source metadata captured during planning to the existing
operation layer. It rejects a changed source, destination identity, or
unsupported destination-parent replacement before publication; operation-level
hash and publication checks remain in place. Planning itself does not create
directories or alter source files. Filesystem changes after planning can still
invalidate a plan, and a narrow race remains between revalidation and mutation.

### Observability

JSON reports and completed/failed history metadata retain existing summary,
`by_rule`, and event fields and add a `metrics` object with duration, files per
second, matching counts, skipped/error categories, operation types,
planned/completed totals, and per-rule counts. Aggregates are finalized once per
run, so metrics do not add per-file history metadata rewrites. Reports contain
paths and metadata, not file contents. Partial failures keep planned and
completed totals distinct.

### Read-only duplicate scanning

Added `filemindr duplicates <directory>` with human and JSON output and optional
profile ignore patterns. It walks without following directory symlinks, groups
regular files by size, and hashes same-size candidates in 1 MiB chunks. A group
is confirmed only by matching size and SHA-256. Read failures, traversal errors,
and files whose identity/metadata changes during hashing are returned as
unverified; an incomplete scan exits nonzero. Unique-size files are ruled out
by size without being read. The command never mutates files and adds no runtime
dependency.

### Performance and evidence

`docs/benchmarks.md` and `docs/planning-duplicate-benchmarks.json` record the
serial Windows 11 / CPython 3.12.9 workloads at 100, 10,000, and 100,000 files.
At 100,000, current dry-run planning took 36.835 seconds (96.57 MiB traced
peak; 52.18 MiB process RSS baseline and 241.20 MiB sampled peak). The duplicate
scan took 111.588 seconds (132.01 MiB traced peak; 141.67 MiB baseline and
372.14 MiB sampled process peak). The scanner workload intentionally made one
100,000-member duplicate group, so it measures a high-output-memory case.
Sampling and single-machine limits are documented; these results do not
establish Linux/macOS performance.

The planner's measured 100,000-file time fell from 74.052 to 36.835 seconds
after reducing repeated path normalization, stopping ancestor reservation walks
at shared parents, and caching static destination templates. The first timing
sample was intermediate; the JSON artifact contains final-source measurements.
No actual move benchmark was run for this feature iteration.

### Compatibility and remaining limits

Existing CLI commands, YAML fields, conflict policies for existing regular-file
collisions, summary fields, report events, and history event ordering remain.
Reports gain the documented `metrics` key and history metadata gains the same
key at finalization. Explain now shares batch reservations and returns a nonzero
exit for planning errors, aligning it with execution. New file/directory
conflicts are explicit errors because leaf-name conflict policies cannot
reconcile a file with a directory.

The duplicate scan is not a snapshot and may miss content rewritten while
preserving all checked metadata; Windows creation-time metadata is excluded
because immediate post-write scans showed unstable creation timestamps. RSS
sampling can miss short peaks. The 100,000-file planner and scanner consume
memory proportional to file count and are slow on this Windows host. Linux/macOS
execution, physical cross-volume operations, network filesystems, and hostile
concurrent filesystem mutation still require platform-specific testing.

### Local verification

The full test suite passed on Windows with CPython 3.12.9, 3.13.3, and 3.14.0:
155 passed and 3 symlink-related tests skipped on each interpreter. The 3.12
coverage run measured 86% statement coverage and 75% branch coverage (83%
combined). Ruff lint passed, and all Python files changed for this feature pass
Ruff formatting checks. The repository-wide format check still identifies 14
unchanged baseline files that would be reformatted.

The wheel and source distribution built successfully, passed Twine metadata
validation, and passed isolated installed-package smoke checks for CLI help,
templates, dry-run, move, undo, and the duplicates command. Linux/macOS CI,
cross-volume behavior, and physical external or network filesystems were not
available for local validation and remain required environmental checks.
