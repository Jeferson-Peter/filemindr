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
- Explain mode
- Watch mode
- Run history with `list`, `show`, `prune`, and `clear`
- Final summary report
- Structured logging with `INFO` and `DEBUG`
- Cross-platform support for Windows, macOS, and Linux

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

Undo v1 only reverts recorded move operations. It does not restore `trash`, `overwrite`, or `copy` events.

Example workflow:

```bash
filemindr run -p home
filemindr history list
filemindr undo <run_id>
```

---

## Conflict Policy

Supported values:

- `rename`
- `skip`
- `overwrite`
- `trash`

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

Release notes are versioned in the repository under `release-notes/` and consumed automatically by the publish workflow.

---

## Status

Current release line: `1.3.x`

---

## License

MIT
