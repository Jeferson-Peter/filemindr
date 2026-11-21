# Filemindr

Declarative local file automation using profiles.

Filemindr lets you describe what should happen to your files, not how.

You define rule pipelines in YAML, group them into profiles, and run them safely through a clean CLI.

Built as a learning and portfolio project with strong focus on:

- predictable behavior
- safety by default
- excellent CLI DX

---

## Features

- Profile-based configuration in `~/.filemindr`
- Declarative YAML rules
- Rule engine with priority, where the highest match wins
- Match by:
  - file extension
  - filename regex
  - file age with `older_than_days`
- Actions:
  - `move_to`
  - `copy_to`
  - `rename_template`
  - `stem_safe` placeholder support
- Global and per-rule conflict policies:
  - `rename`
  - `skip`
  - `overwrite`
  - `trash`
- Dry-run mode
- JSON execution reports
- Explain mode
- Watch mode
- Run history with `list`, `show`, `prune`, and `clear`
- Final summary report
- Structured logging with `INFO` and `DEBUG`
- Portable implementation targeting Windows, macOS, and Linux; see the
  [assessment](docs/engineering-assessment.md) for platforms actually verified.

---

## Installation

```bash
pipx install filemindr
```

or

```bash
pip install filemindr
```

Development setup:

```bash
uv sync --extra dev
```

---

## Profiles

Instead of a single global YAML, Filemindr uses profiles.

Each profile lives in:

```text
~/.filemindr/rules/<profile>/rules.yaml
```

And all profiles are registered in:

```text
~/.filemindr/profiles.yaml
```

This allows:

- multiple setups such as `home`, `work`, or `media`
- explicit selection via CLI
- zero ambiguity about which config is running

---

## Quick Start

Create your first profile:

```bash
filemindr profile init home
```

This creates:

```text
~/.filemindr/
|-- profiles.yaml
`-- rules/
    `-- home/
        `-- rules.yaml
```

Open and edit the rules:

```bash
filemindr profile open home
```

Example `rules.yaml`:

```yaml
source: ~/Downloads
default_target: ~/Downloads/others
conflict_policy: rename

rules:
  - name: invoices
    priority: 100
    match:
      extensions: ["pdf"]
      regex: "(?i)invoice|nota|nf"
    action:
      move_to: ~/Downloads/finance/invoices

  - name: dated-pdfs
    priority: 70
    match:
      extensions: ["pdf"]
    action:
      move_to: ~/Downloads/archive/{yyyy}/{mm}
      rename_template: "{stem}_{yyyy}-{mm}{suffix}"

  - name: images
    priority: 40
    match:
      extensions: ["jpg", "png", "webp"]
    action:
      move_to: ~/Downloads/images
```

Important notes:

- Rule priority matters. If two rules match the same file, the higher priority wins.
- `rename_template` only defines the final file name, not folders.
- `move_to` and `copy_to` can use templates in the destination path.
- `ignore` accepts glob-style patterns and skips matching files in `run`, `watch`, and `explain`.

Example ignore rules:

```yaml
ignore:
  - "*.tmp"
  - "*.crdownload"
  - "~$*"
```

Example with ignore plus a rule:

```yaml
source: ~/Downloads
default_target: ~/Downloads/others
ignore:
  - "*.tmp"
  - "*.crdownload"

rules:
  - name: documents
    priority: 50
    match:
      extensions: ["pdf"]
    action:
      move_to: ~/Downloads/documents
```

Supported template fields:

- `{name}`
- `{stem}`
- `{stem_safe}`
- `{suffix}`
- `{ext}`
- `{parent}`
- `{yyyy}`
- `{mm}`
- `{dd}`

Preview:

```bash
filemindr run -p home --dry-run
```

Verbose preview:

```bash
filemindr run -p home --dry-run --log-level DEBUG
```

Run:

```bash
filemindr run -p home
```

Write a JSON report:

```bash
filemindr run -p home --report report.json
```

Example report payload:

```json
{
  "profile": "home",
  "command": "run",
  "dry_run": false,
  "summary": {
    "files_scanned": 55,
    "moved": 55,
    "copied": 0,
    "errors": 0
  },
  "by_rule": {
    "dated-pdfs": 22,
    "images": 12
  },
  "events": [
    {
      "event": "moved",
      "rule": "dated-pdfs",
      "source": "C:/Users/you/Downloads/Day Trade-2025.pdf",
      "destination": "C:/Users/you/Downloads/archive/2026/03/day-trade-2025_2026-03.pdf"
    }
  ]
}
```

---

## Profile Commands

Create:

```bash
filemindr profile init home
```

List:

```bash
filemindr profile list
```

Show path:

```bash
filemindr profile show home
```

Open in editor:

```bash
filemindr profile open home
```

Remove completely:

```bash
filemindr profile remove home
```

---

## Watch Mode

Watch mode observes direct children only. File size, identity, permission mode,
and nanosecond timestamps must remain unchanged for the stability interval before processing.
This is a heuristic: another program can resume writing later. Prefer writing
to an ignored `.part` file and renaming it to its final name when finished.

The pending handoff deduplicates paths and backpressures ingestion at
`max_pending_paths` (10,000 by default). Stability checks run together; a busy
file does not block checks for other pending files. Long file operations run
serially while ingestion continues up to that bound. Ctrl+C/SIGTERM stops
between file operations; the current filesystem call must finish first.

Unchanged duplicate events are suppressed after failed attempts too, preventing
recovery notifications from triggering repeated copies. A source permission or
data change permits another attempt. If only destination conditions change
(for example, disk space is freed), retry with `filemindr run` or restart watch.

Continuous:

```bash
filemindr watch -p home
```

Single batch:

```bash
filemindr watch -p home --once
```

---

## Explain Mode

Explain a directory:

```bash
filemindr explain -p home ~/Downloads
```

Explain a single file with spaces in the path:

```bash
filemindr explain -p home "C:\Users\you\Downloads\Day Trade-2025.pdf"
```

Explain with more detail about matched rules, templates, and rendered names:

```bash
filemindr explain -p home "C:\Users\you\Downloads\Day Trade-2025.pdf" --verbose
```

Example output:

```text
Day Trade-2025.pdf -> C:\Users\you\Downloads\archive\2026\03\day-trade-2025_2026-03.pdf [MOVE] rule=dated-pdfs prio=70 policy=rename (source=C:\Users\you\Downloads ext=.pdf matched_rule=dated-pdfs matched_candidates=dated-pdfs(prio=70) target_template=~/Downloads/archive/{yyyy}/{mm} rename_template={stem_safe}_{yyyy}-{mm}{suffix} rendered_name=day-trade-2025_2026-03.pdf dest_exists=NO conflict=none)
```

Example of a normalized file name:

```yaml
rename_template: "{stem_safe}_{yyyy}-{mm}{suffix}"
```

---

## Validate

```bash
filemindr validate -p home
```

---

## Doctor

```bash
filemindr doctor
```

---

## History

List recent runs:

```bash
filemindr history list
```

Include legacy/internal entries too:

```bash
filemindr history list --all
```

Inspect one run:

```bash
filemindr history show <run_id>
```

Example output:

```text
run_id:      5ffb71382410
command:     run
profile:     home
status:      completed
dry_run:     True
started_at:  2026-03-13T16:54:18.073692+00:00
finished_at: 2026-03-13T16:54:18.115000+00:00
source:      C:\Users\you\Downloads
config_path: C:\Users\you\.filemindr\rules\home\rules.yaml

counts:
  planned_move: 55

events:
  - planned_move: C:\Users\you\Downloads\Day Trade-2025.pdf -> C:\Users\you\Downloads\archive\2026\03\day-trade-2025_2026-03.pdf
```

Prune old history entries:

```bash
filemindr history prune --days 7
```

Clear all stored history:

```bash
filemindr history clear --yes
```

Filemindr also prunes old history automatically on pipeline runs, keeping the last 7 days by default.

---

## Undo

Undo one recorded run:

```bash
filemindr undo <run_id>
```

Undo only reverts new, verified move operations that did not replace an existing
file. It compares SHA-256, size, modification time, device, and inode before
moving anything. Modified/replaced files, missing files, occupied original
paths, and legacy records without identity data are skipped with a reason in
history. Successful undo events prevent replay while those records are retained.
Copies and moves that replaced/trash-displaced an existing file are not reversible
through this command. History is an audit log, not a backup.

Example workflow:

```bash
filemindr run -p home
filemindr history list
filemindr undo <run_id>
```

---

## Conflict Policy

| Policy | Behavior | Recovery |
| --- | --- | --- |
| `rename` (default) | Choose an unused numbered name on conflict | Eligible moves support verified undo |
| `skip` | Leave existing destination untouched | No action to undo |
| `overwrite` | Stage, verify, then replace the destination | Previous destination is permanently replaced; no full undo |
| `trash` | Stage incoming data, retain a private recovery copy, trash old destination, publish | OS trash recovery is manual; no full undo |

Copies and replacement moves are written to a temporary file on the destination
filesystem, synced, and checked before publication. Same-filesystem moves use
no-clobber hard-link publication. Before source removal, moves claim the source
under a private `.filemindr-*.bak` name in its own directory and verify that file
and the published destination. A new file at the original source name is left
untouched. Cross-device moves
fall back to a verified staged copy, then remove the source. Unsupported hard-link
filesystems fail safely rather than falling back to a racy overwrite. Locks,
permission failures, and insufficient space are reported as errors. If source
removal fails after publication, recovery data remains and the event reports a
`recovery_path`. Restoration of the source name never replaces a newly created
file; a retained source backup may need manual inspection instead. The CLI
returns nonzero when a run has file errors.

Atomic replacement of a single directory entry depends on the operating system
and filesystem. A whole move, cross-device copy, trash operation, or pipeline is
not a transaction. Concurrent changes are checked before commit; there remains
a race between the last check and replacement/removal. Do not run competing
organizers against the same files. Power loss and hostile directory/symlink swaps
are outside the guarantees. See [Python's replacement contract](https://docs.python.org/3.12/library/os.html#os.replace).

Source and destination symlink files are unsupported. Targets may be explicit
paths outside the source, including resolved directory aliases; generated filename
components cannot inject separators or parent traversal. Direct source/target
overlap is rejected. Child output directories remain supported because scanning
and watching are shallow. Templates accept named fields only, without format
specifiers or conversions. Escape literal braces as `{{` and `}}`.

If a process is killed, inspect `operation_started` and subsequent history events
and `.filemindr-*.tmp`/`.filemindr-*.bak` files in both source and destination
directories. A retained `.bak` can hold a claimed source or the previous trash
destination when rollback fails or cannot be verified. These files are
excluded from processing; inspect them manually before deleting them. JSONL
history is flushed on append but is not a power-loss-durable transaction journal.
Summary metadata updates at start and finish, so a killed run can remain `running`.

`--report` requires an unused path and may create that report during dry-run;
dry-run does not create target directories or move/copy source data. Profile runs
still write/prune audit history in dry-run. Use a new report filename for each run.

Example: keep downloads untouched while copying PDFs by modification month:

```yaml
source: ~/Downloads
default_target: ~/Downloads/others
conflict_policy: skip
ignore: ['*.part', '*.tmp']
rules:
  - name: monthly-pdf-backup
    priority: 20
    match:
      extensions: [pdf]
    action:
      copy_to: ~/Documents/PDF/{yyyy}/{mm}
      rename_template: '{stem_safe}{suffix}'
```

---

## Development

Install development dependencies:

```bash
uv sync --extra dev
```

Run tests:

```bash
uv run pytest -q
```

Run lint and build distributions:

```bash
uv run ruff check .
uv build
```

CI checks Windows/Linux/macOS with Python 3.12–3.14, including isolated wheel and
source-distribution installation. This matrix must run remotely before a
cross-platform release can be considered verified. Publishing requires manual
dispatch on `development` after CI; configure required reviewers for the `pypi`
environment. The workflow does not automatically create tags or GitHub releases.
Keep versioned notes in `release-notes/` for release preparation.

See the [engineering assessment](docs/engineering-assessment.md),
[benchmark methodology and results](docs/benchmarks.md), and
[portfolio case study](CASE_STUDY.md).

---

## Status

Current release line: `1.4.x`

---

## License

MIT
