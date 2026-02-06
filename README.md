# Filemindr — Rule-Driven Local File Automation (v2)

**Declarative local file automation for humans and scripts.**

Filemindr lets you describe *what should happen* to your files — not *how*.

You define rules in YAML (extensions, regex, age, priority), and filemindr applies them safely with dry-run support, conflict handling, real-time watching, and a clean CLI.

Built as a learning + portfolio project with strong focus on:
- predictable behavior
- safety by default
- excellent CLI DX

---

## ✨ Features (v2)

### Core

- Declarative YAML configuration
- Rule engine with priority (highest wins)
- Match by:
  - file extensions
  - regex on filename
  - file age (`older_than_days`)
- Actions:
  - `move_to`
  - `copy_to`
- Global and per-rule conflict policies:
  - `rename`
  - `skip`
  - `overwrite`
  - `trash`
- Dry-run mode (preview changes before touching files)
- Final summary report
- Structured logging (`INFO`, `DEBUG`, etc)
- Cross-platform (Windows, macOS, Linux)

### CLI

- `init` (local or global config)
- Config resolution:
  **CLI → local → global**
- `run` + `--dry-run`
- `watch` (continuous)
- `watch --once`
- `explain` (one-liner preview per file, with optional limit)
- `validate` (schema + rule validation)
- Real trash support via `send2trash` (with unlink fallback)
- Watcher debounce + file stability detection

---

## 📦 Installation

### Recommended (pipx)

```bash
pipx install filemindr
```

### pip

```bash
pip install filemindr
```

### Local development

```bash
pip install -e .
```

With uv:

```bash
uv sync
```

---

## 🚀 Quick Start

Create a config:

```bash
filemindr init
```

This creates `filemindr.yaml` in the current directory.

Example:

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

  - name: documents
    priority: 50
    match:
      extensions: ["pdf", "docx", "xlsx"]
    action:
      move_to: ~/Downloads/documents

  - name: images
    priority: 40
    match:
      extensions: ["jpg", "jpeg", "png", "webp"]
    action:
      move_to: ~/Downloads/images

  - name: old_installers
    priority: 80
    match:
      extensions: ["exe", "msi"]
      older_than_days: 14
    action:
      move_to: ~/Downloads/installers/old
```

Preview:

```bash
filemindr run --dry-run
```

Run for real:

```bash
filemindr run
```

Verbose:

```bash
filemindr run --log-level DEBUG
```

---

## 🧠 How it works

1. Filemindr scans the source directory
2. Rules are evaluated by priority (highest first)
3. The first matching rule wins
4. The configured action is applied
5. Conflicts are resolved via the selected policy
6. A summary is printed

---

## ⚔ Conflict Policy

Can be defined globally or per rule.

Supported values:

- `rename` (default): `file (1).ext`, `file (2).ext`, etc
- `skip`: keep existing file
- `overwrite`: replace destination
- `trash`: send existing file to system trash

Per rule:

```yaml
action:
  move_to: ~/archive
  conflict_policy: overwrite
```

---

## 📁 Config Resolution

Filemindr loads configuration in this order:

1. CLI flags (highest priority)
2. Local `filemindr.yaml`
3. Global `~/.filemindr/config.yaml`

You can create global config with:

```bash
filemindr init --global
```

---

## 👀 Watch Mode

Continuous watching:

```bash
filemindr watch
```

Single batch (useful for scripts):

```bash
filemindr watch --once
```

Watcher includes debounce + file stability checks to avoid processing half-written files.

---

## 🔍 Explain Mode

Preview which rule each file would use:

```bash
filemindr explain
```

Limit output:

```bash
filemindr explain --limit 20
```

Outputs one-liners:

```
photo.jpg → images
invoice_2024.pdf → invoices
```

---

## ✅ Validate Config

Validate schema + rules:

```bash
filemindr validate
```

Fails fast with clear errors.

---

## 🧪 Development

Run tests:

```bash
uv run pytest
```

Project layout:

```
filemindr/
├── src/filemindr/
│   ├── core/
│   │   ├── pipeline.py
│   │   └── watcher.py
│   ├── cli.py
│   └── rules.py
├── tests/
├── pyproject.toml
└── README.md
```

---

## 🛠 Status

Stable v2.

APIs are considered usable but may evolve.

---

## 📄 License

MIT

---

