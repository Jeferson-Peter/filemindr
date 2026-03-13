from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from string import Formatter

from filemindr.core.config import expand_path

ALLOWED_TEMPLATE_FIELDS = {
    "name",
    "stem",
    "stem_safe",
    "suffix",
    "ext",
    "parent",
    "yyyy",
    "mm",
    "dd",
}


def template_fields(template: str) -> set[str]:
    fields: set[str] = set()
    for _, field_name, _, _ in Formatter().parse(template):
        if field_name:
            fields.add(field_name)
    return fields


def invalid_template_fields(template: str) -> set[str]:
    return template_fields(template) - ALLOWED_TEMPLATE_FIELDS


def template_syntax_error(template: str) -> str | None:
    try:
        template_fields(template)
    except ValueError as exc:
        return str(exc)
    return None


def template_context(file: Path) -> dict[str, str]:
    stamp = datetime.fromtimestamp(file.stat().st_mtime)
    stem_safe = re.sub(r"[^a-z0-9]+", "-", file.stem.lower()).strip("-")
    return {
        "name": file.name,
        "stem": file.stem,
        "stem_safe": stem_safe or "file",
        "suffix": file.suffix,
        "ext": file.suffix.lstrip(".").lower(),
        "parent": file.parent.name,
        "yyyy": f"{stamp.year:04d}",
        "mm": f"{stamp.month:02d}",
        "dd": f"{stamp.day:02d}",
    }


def render_template(template: str, *, file: Path) -> str:
    return template.format_map(template_context(file))


def render_target_template(template: str, *, file: Path, base_dir: Path | None = None) -> Path:
    return expand_path(render_template(template, file=file), base_dir=base_dir)


def render_name_template(template: str, *, file: Path) -> str:
    return render_template(template, file=file)
