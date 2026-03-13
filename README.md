# Filemindr --- Rule-Driven Local File Automation (v2)

**Declarative local file automation using profiles.**

Filemindr lets you describe *what should happen* to your files --- not
*how*.

You define rule pipelines in YAML (extensions, regex, age, priority),
group them into **profiles**, and run them safely via a clean CLI.

Built as a learning + portfolio project with strong focus on:

-   predictable behavior\
-   safety by default\
-   excellent CLI DX

------------------------------------------------------------------------

## ✨ Features (v2)

### Core

-   Profiles-based configuration (`~/.filemindr`)
-   Declarative YAML rules
-   Rule engine with priority (highest wins)
-   Match by:
    -   file extensions
    -   regex on filename
    -   file age (`older_than_days`)
-   Actions:
    -   `move_to`
    -   `copy_to`
    -   `rename_template`
-   Global and per-rule conflict policies:
    -   `rename`
    -   `skip`
    -   `overwrite`
    -   `trash`
-   Dry-run mode
-   Final summary report
-   Structured logging (`INFO`, `DEBUG`)
-   Cross-platform (Windows, macOS, Linux)

------------------------------------------------------------------------

## 📦 Installation

``` bash
pipx install filemindr
```

or

``` bash
pip install filemindr
```

Dev:

``` bash
uv sync
```

------------------------------------------------------------------------

## 🧠 Profiles (core concept)

Instead of a single global YAML, Filemindr uses **profiles**.

Each profile lives in:

    ~/.filemindr/rules/<profile>/rules.yaml

And all profiles are registered in:

    ~/.filemindr/profiles.yaml

This allows:

-   multiple setups (home, work, media, etc)
-   explicit selection via CLI
-   zero ambiguity about which config is running

------------------------------------------------------------------------

## 🚀 Quick Start

Create your first profile:

``` bash
filemindr profile init home
```

This creates:

    ~/.filemindr/
    ├── profiles.yaml
    └── rules/
        └── home/
            └── rules.yaml

Open and edit the rules:

``` bash
filemindr profile open home
```

Example `rules.yaml`:

``` yaml
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

  - name: images
    priority: 40
    match:
      extensions: ["jpg", "png", "webp"]
    action:
      move_to: ~/Downloads/images

  - name: dated-pdfs
    priority: 30
    match:
      extensions: ["pdf"]
    action:
      move_to: ~/Downloads/archive/{yyyy}/{mm}
      rename_template: "{stem}_{yyyy}-{mm}{suffix}"
```

Supported template fields:

-   `{name}`
-   `{stem}`
-   `{suffix}`
-   `{ext}`
-   `{parent}`
-   `{yyyy}`
-   `{mm}`
-   `{dd}`

Preview:

``` bash
filemindr run -p home --dry-run
```

Run:

``` bash
filemindr run -p home
```

Verbose:

``` bash
filemindr run -p home --log-level DEBUG
```

------------------------------------------------------------------------

## 📂 Profile Commands

Create:

``` bash
filemindr profile init home
```

List:

``` bash
filemindr profile list
```

Show path:

``` bash
filemindr profile show home
```

Open in editor:

``` bash
filemindr profile open home
```

Remove completely:

``` bash
filemindr profile remove home
```

------------------------------------------------------------------------

## 👀 Watch Mode

Continuous:

``` bash
filemindr watch -p home
```

Single batch:

``` bash
filemindr watch -p home --once
```

------------------------------------------------------------------------

## 🔍 Explain Mode

``` bash
filemindr explain -p home ~/Downloads
```

------------------------------------------------------------------------

## ✅ Validate

``` bash
filemindr validate -p home
```

------------------------------------------------------------------------

## 🩺 Doctor

``` bash
filemindr doctor
```

------------------------------------------------------------------------

## ⚔ Conflict Policy

Supported:

-   `rename`
-   `skip`
-   `overwrite`
-   `trash`

------------------------------------------------------------------------

## 🧪 Development

``` bash
uv run pytest
```

------------------------------------------------------------------------

## 🛠 Status

Stable v2.

------------------------------------------------------------------------

## 📄 License

MIT
