from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from string import Formatter

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
    for _, field_name, spec, conversion in Formatter().parse(template):
        if field_name is not None:
            if not field_name or spec or conversion:
                raise ValueError(
                    "Only named placeholders without conversions or format specs are supported"
                )
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
    if "{" not in template and "}" not in template:
        return template
    error = template_syntax_error(template)
    if error or invalid_template_fields(template):
        raise ValueError(error or "Unsupported template fields")
    return template.format_map(template_context(file))


def render_target_template(
    template: str, *, file: Path, base_dir: Path | None = None
) -> Path:
    # Expand configuration variables before interpolation: filenames are data.
    expanded = os.path.expandvars(template)
    if expanded == "~" or expanded.startswith(("~/", "~\\")):
        expanded = str(Path.home()) + expanded[1:]
    else:
        expanded = os.path.expanduser(expanded)
    fields = template_fields(expanded)
    if invalid_template_fields(expanded):
        raise ValueError("Unsupported template fields")
    context = template_context(file) if fields else {}
    for field in fields:
        if context[field]:
            validate_file_name(context[field])
    rendered = Path(expanded.format_map(context))
    if not rendered.is_absolute() and base_dir is not None:
        rendered = base_dir / rendered
    return rendered.resolve()


def render_name_template(template: str, *, file: Path) -> str:
    name = render_template(template, file=file)
    validate_file_name(name)
    return name


def validate_file_name(name: str) -> None:
    if not name or name in {".", ".."} or any(c in name for c in '/\\\x00:<>"|?*'):
        raise ValueError(f"Unsafe generated file name: {name!r}")
    if name.endswith((".", " ")) or name.split(".")[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        raise ValueError(f"Non-portable generated file name: {name!r}")
