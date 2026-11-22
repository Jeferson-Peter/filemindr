# FileMindr: hardening a declarative filesystem CLI

FileMindr organizes local files using YAML profiles rather than handwritten
scripts. A user selects a profile, defines extension/regex/age predicates, and
chooses move or copy destinations with optional filename templates. This review
focused on whether that convenience could be trusted around real data.

## Architecture

The Typer CLI resolves profiles and delegates work to small core modules.
Configuration validation is shared by execution, watching, and explain mode.
Rules are compiled once and sorted by descending priority; equal priorities
retain configuration order. The first matching rule wins. Directory traversal
remains shallow, so child output directories do not become new inputs.

The shared execution planner handles selection, ignore rules, conflict naming,
and destination-tree reservations for normal runs, dry-run, and explain mode.
It emits structured plans with source metadata and expected destination state.
The runner executes each plan and aggregates metrics and audit events. The
operations module owns staging, checksums, publication, and source removal.
Undo uses that same operation boundary. This separation makes faults testable
without a database or an additional execution framework.

## The reliability problem

The original overwrite implementation unlinked the destination before attempting
the incoming copy or move. A full disk, locked file, or failed rename could then
destroy the old destination. Moves also assumed a shared filesystem. Undo trusted
recorded paths, allowing a later, unrelated file at the same path to be moved.

The new execution sequence preserves the destination until an incoming staged
file is ready. Copies are synced and verified by size and SHA-256. Replacement
uses `os.replace` within the destination filesystem. Publication without overwrite
uses a hard link on POSIX; staged files use no-clobber rename on Windows. Ordinary
same-filesystem moves publish a hard link, then claim the source name through
a private same-directory rename before verification and deletion. This prevents
ordinary source-name replacement from causing deletion of an unrelated new
file. On failure, recovery data is retained and source restoration cannot clobber
a newly created source. A cross-device error switches to a
verified staged copy, publishing before source removal. The published destination
is checked again before deletion of the source.

Trash replacement needs a different compromise: the recycle-bin API and final
publication cannot form one transaction. A private copy of the old destination
supports rollback on ordinary failure. If rollback fails, its path is retained
and reported for manual recovery. A process crash may leave staging or recovery
files; these are deliberately excluded from future processing.

## Recovery and honesty about guarantees

Only verified moves into an unused destination are eligible for automatic undo.
History records the destination's size, nanosecond modification time, device,
inode, and SHA-256. Undo refuses changed identities and occupied original paths.
It skips older records without identity data and records successful reversals to
avoid replay while history remains available. Copy, overwrite, and trash are not
advertised as fully reversible.

These checks reduce ordinary races; they cannot eliminate the interval between
the last check and a filesystem mutation. FileMindr is intended for cooperative
local automation, not hostile shared directories. Power-loss durability, network
filesystem semantics, and arbitrary filesystem metadata preservation are outside
the guarantees. No pipeline-wide or cross-device atomicity is claimed.

## Watch mode and resource limits

The original watcher queued every event and waited synchronously for each file
to stop changing. A file that never settled could block the consumer indefinitely
while the producer kept allocating memory.

The replacement uses a bounded, deduplicated pending map with producer
backpressure. Stability checks sample all pending files without sleeping per file.
Missing files are removed and unstable files are retried on ticks without waiting
for another event. A bounded attempt cache suppresses unchanged duplicate
notifications after failures as well as successes, preventing recovery-generated
copy loops. Permission mode is part of the signature so fixing a read-only source
permits retry. Fixing destination conditions alone can require a manual run.
SIGINT/SIGTERM interrupts waiting and stops between operations; it cannot cancel
an in-progress OS copy immediately. Native event batches and OS event loss remain
outside the Python handoff's bound, and there is no automatic reconciliation scan.

## Tests as engineering evidence

Fault injection covers partial writes, out-of-space errors, locked files,
replacement failures, source deletion failure, cross-device errors, corruption,
concurrent changes, and failed trash rollback. Assertions inspect actual source,
destination, and staging contents after failures rather than just expecting an
exception. Recovery tests cover same-size edits with restored timestamps,
same-content file replacement, repeated undo, legacy history, truncated final
journal records, and unsafe directory links.

Watch tests exercise deduplication, backpressure, ongoing writes, missing files,
producer failure, and signal restoration. Packaging checks inspect archive
contents and install both the wheel and sdist outside the editable environment,
then exercise dry-run, move, history, and undo through the installed CLI.

## Performance findings

The reproducible benchmark compares the original 1.4.0 implementation with the
hardened implementation on 100, 10,000, and 100,000 eight-byte files and 20 rules.
[Historical measurements and caveats](docs/benchmarks.md) accompany raw JSON output;
they predate the independent review's source-claim fix and do not measure its
additional filesystem work.
Removing per-event metadata rewrites reduces journal I/O. Avoiding unrequested
report accumulation reduces actual-run memory. Checksums and repeated identity
verification add real move overhead. Dry-run destination reservations consume
additional memory to model duplicate names correctly. Those are explicit safety
trade-offs rather than a claim that every path became faster.

The later shared-planner/duplicate scan benchmark is separate from that historical
comparison. On Windows 11 / Python 3.12.9, a 100,000-file dry-run took 36.835 s
and peaked at 96.57 MiB of traced Python allocations (sampled process RSS peaked
at 241.20 MiB). A read-only duplicate scan over 100,000 same-size identical
files took 111.588 s and peaked at 132.01 MiB traced / 372.14 MiB sampled RSS.
That deliberately creates one 100,000-path result group and shows the memory
cost of returning all confirmed paths. Results and limitations are in
[the current benchmark report](docs/benchmarks.md); these are not cross-platform
performance guarantees.

## Shared planning and duplicate detection

The previously separate explain conflict resolver was replaced with the shared
planner. Stable path ordering makes numbered names repeatable. A path reserved
as a file cannot later be treated as a directory; a planned directory cannot
become a file output. The planner reports those collisions without creating
directories, and execution revalidates source, destination, and parent shape
before publication. This reduces stale-plan surprises but cannot provide a
filesystem snapshot or eliminate the check-to-mutation race.

The new `duplicates` command is read-only. It filters by file size, hashes
same-size candidates in bounded chunks, and only labels files duplicates after
SHA-256 matches. Symlinks are skipped; unreadable and changing files are marked
unverified. Optional profile ignore rules keep scan scope consistent with the
user's pipeline. No automatic deletion or deduplication is performed.

## Cross-platform and release discipline

Local integration validation for this feature iteration covers Windows/NTFS on
Python 3.12.9. GitHub Actions passed the full nine-job matrix on Windows,
macOS, and Ubuntu with Python 3.12–3.14. Symlink creation tests skip when
privileges are unavailable. Cross-device errors are simulated; physical
multi-volume transfers and network shares still require integration testing.

The first archive inspection revealed that Hatch's default source selection
included a local report. Explicit inclusion patterns now keep unrelated local
files out. PR #8 was merged into `development` on 2026-11-22 after the full
matrix passed. Versioned release notes and version metadata are prepared
separately; publication runs only through an intentional dispatch on
`development`. Environment approval rules must be configured in GitHub itself.

The remaining platform integration work is real cross-volume moves, locked
files under Windows sharing modes, representative large media files, network
filesystems, and native watcher overflow/reconciliation behavior. The
[assessment](docs/engineering-assessment.md) tracks the remaining limits.
