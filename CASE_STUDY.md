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

The runner handles selection, conflict naming, counters, and audit events.
An immutable `OperationPlan` describes a regular-file operation and its expected
destination state. The operations module owns staging, checksums, publication,
and source removal. Undo uses that same operation boundary. This separation
makes faults testable without a database or an additional execution framework.

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

## Cross-platform and release discipline

Local validation covers Windows/NTFS on Python 3.12 and 3.14. Linux/macOS and Python
3.13 are configured in CI, with their execution still required before claiming
cross-platform release readiness. Symlink creation tests skip when privileges are
unavailable. Cross-device errors are simulated; physical multi-volume transfers
and network shares require further integration testing.

The first archive inspection revealed that Hatch's default source selection
included a local report. Explicit inclusion patterns now keep unrelated local
files out. Publishing requires an intentional dispatch on `development` after the
test matrix. Environment approval rules must be configured in GitHub itself.
No release, tag, push, or merge was performed during this local review.

The next useful work is platform integration evidence: real cross-volume moves,
locked files under Windows sharing modes, representative large media files,
network filesystems, and native watcher overflow/reconciliation behavior. The
[assessment](docs/engineering-assessment.md) tracks the remaining limits.
