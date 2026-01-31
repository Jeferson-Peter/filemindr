# Filemindr — Rule-Driven Local File Automation

**Declarative local file pipelines for organizing your files.**

filemindr lets you describe *what should happen* to your files — not *how*.  
You define rules in YAML (extensions, regex, age, priority), and filemindr applies them safely with dry‑run support, conflict handling, and a clean CLI.

This project was built as a learning + portfolio tool focused on clarity, safety, and developer experience.

---

## ✨ Features

- Declarative YAML configuration
- Rule engine with priority (highest wins)
- Match by:
  - File extensions
  - Regex on filename
  - File age (`older_than_days`)
- Conflict policies:
  - `rename`
  - `skip`
  - `overwrite`
- Dry-run mode (preview changes before touching files)
- Summary report after each run
- Proper logging levels (`INFO` / `DEBUG`)
- Cross-platform (Windows, macOS, Linux)

---

## 📦 Installation

### From PyPI (planned / when published)

```bash
pip install Filemindr
```

Or:

```bash
python -m pip install Filemindr
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

Create a `filemindr.yaml`:

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

Preview changes:

```bash
filemindr run --dry-run
```

Run for real:

```bash
filemindr run
```

Verbose mode (per-file logs):

```bash
filemindr run --log-level DEBUG
```

---

## 🧠 How it works

1. Filemind scans the source directory
2. Rules are evaluated by priority (highest first)
3. The first matching rule wins
4. Files are moved according to the selected conflict policy
5. A summary is printed at the end

---

## ⚔ Conflict Policy

- `rename` (default): creates `file (1).ext`, `file (2).ext`, etc
- `skip`: ignores files that already exist
- `overwrite`: replaces existing files

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
├── tests/
├── pyproject.toml
└── README.md
```

---

## 🛠 Status

Early alpha / experimental.

APIs may change.

---

## 📄 License

MIT

---

## Roadmap

- [ ] Watch mode (real-time folder monitoring)
- [ ] Per-rule conflict policies
- [ ] Copy instead of move
- [ ] Trash support
- [ ] Config validation