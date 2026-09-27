"""Versioned, data-only FormatSpec validation and user preset persistence."""

from __future__ import annotations

import json
from pathlib import Path


class FormatSpecError(ValueError):
    """Raised when a format preset is malformed or contains unsupported fields."""


_ROOT_KEYS = {"schema_version", "name", "page", "body", "headings", "table", "figure"}
_PAGE_KEYS = {"size", "margins_cm", "page_number"}
_MARGIN_KEYS = {"top", "bottom", "left", "right"}
_PAGE_NUMBER_KEYS = {"enabled", "position"}
_BODY_KEYS = {"font_latin", "font_cjk", "font_size_pt", "alignment", "line_spacing", "first_line_indent_chars", "space_before_pt", "space_after_pt"}
_HEADING_KEYS = {"font_latin", "font_cjk", "font_size_pt", "alignment", "bold", "space_before_pt", "space_after_pt"}
_TABLE_KEYS = {"style", "font_size_pt", "caption_prefix"}
_FIGURE_KEYS = {"caption_prefix"}


def _strict_keys(value, allowed, path):
    if not isinstance(value, dict):
        raise FormatSpecError(f"{path} must be an object.")
    unknown = set(value) - allowed
    if unknown:
        field = sorted(unknown)[0]
        raise FormatSpecError(f"Unsupported field: {path}.{field}")


def _number(value, name, minimum, maximum, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FormatSpecError(f"{name} must be a number from {minimum} to {maximum}.")
    if not minimum <= value <= maximum or (integer and int(value) != value):
        raise FormatSpecError(f"{name} must be a number from {minimum} to {maximum}.")
    return int(value) if integer else float(value)


def _string(value, name, maximum=80):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise FormatSpecError(f"{name} must be a non-empty string up to {maximum} characters.")
    return value.strip()


def _caption_prefix(value, name):
    if not isinstance(value, str) or len(value.strip()) > 32:
        raise FormatSpecError(f"{name} must be text up to 32 characters.")
    return value.strip()


def validate_format_spec(spec):
    """Validate supported presentation data. Unknown keys are rejected, never executed."""
    _strict_keys(spec, _ROOT_KEYS, "FormatSpec")
    if spec.get("schema_version") != 1:
        raise FormatSpecError("schema_version must be 1.")
    name = _string(spec.get("name"), "name")

    page = spec.get("page")
    _strict_keys(page, _PAGE_KEYS, "page")
    if page.get("size") != "A4":
        raise FormatSpecError("page.size must be A4.")
    margins = page.get("margins_cm")
    _strict_keys(margins, _MARGIN_KEYS, "page.margins_cm")
    margins_clean = {key: _number(margins.get(key), f"page.margins_cm.{key}", 0.5, 5.0) for key in _MARGIN_KEYS}
    # A4 is 21 x 29.7 cm; keep a usable text area.
    if margins_clean["left"] + margins_clean["right"] >= 16 or margins_clean["top"] + margins_clean["bottom"] >= 24:
        raise FormatSpecError("Page margins leave too little printable area.")
    page_number = page.get("page_number")
    _strict_keys(page_number, _PAGE_NUMBER_KEYS, "page.page_number")
    if not isinstance(page_number.get("enabled"), bool):
        raise FormatSpecError("page.page_number.enabled must be a boolean.")
    if page_number.get("position") not in {"left", "center", "right"}:
        raise FormatSpecError("page.page_number.position must be left, center, or right.")

    body = spec.get("body")
    _strict_keys(body, _BODY_KEYS, "body")
    body_clean = {
        "font_latin": _string(body.get("font_latin"), "body.font_latin"),
        "font_cjk": _string(body.get("font_cjk"), "body.font_cjk"),
        "font_size_pt": _number(body.get("font_size_pt"), "body.font_size_pt", 6, 30),
        "alignment": body.get("alignment"),
        "line_spacing": _number(body.get("line_spacing"), "body.line_spacing", 0.8, 3.0),
        "first_line_indent_chars": _number(body.get("first_line_indent_chars"), "body.first_line_indent_chars", 0, 4, integer=True),
        "space_before_pt": _number(body.get("space_before_pt"), "body.space_before_pt", 0, 36),
        "space_after_pt": _number(body.get("space_after_pt"), "body.space_after_pt", 0, 36),
    }
    if body_clean["alignment"] not in {"left", "center", "right", "justify"}:
        raise FormatSpecError("body.alignment must be left, center, right, or justify.")

    headings = spec.get("headings")
    _strict_keys(headings, {"h1", "h2", "h3"}, "headings")
    headings_clean = {}
    for level in ("h1", "h2", "h3"):
        item = headings.get(level)
        _strict_keys(item, _HEADING_KEYS, f"headings.{level}")
        heading = {
            "font_latin": _string(item.get("font_latin"), f"headings.{level}.font_latin"),
            "font_cjk": _string(item.get("font_cjk"), f"headings.{level}.font_cjk"),
            "font_size_pt": _number(item.get("font_size_pt"), f"headings.{level}.font_size_pt", 6, 36),
            "alignment": item.get("alignment"),
            "bold": item.get("bold"),
            "space_before_pt": _number(item.get("space_before_pt"), f"headings.{level}.space_before_pt", 0, 48),
            "space_after_pt": _number(item.get("space_after_pt"), f"headings.{level}.space_after_pt", 0, 36),
        }
        if heading["alignment"] not in {"left", "center", "right", "justify"}:
            raise FormatSpecError(f"headings.{level}.alignment is unsupported.")
        if not isinstance(heading["bold"], bool):
            raise FormatSpecError(f"headings.{level}.bold must be a boolean.")
        headings_clean[level] = heading

    table = spec.get("table")
    _strict_keys(table, _TABLE_KEYS, "table")
    if table.get("style") != "three_line":
        raise FormatSpecError("table.style must be three_line.")
    table_clean = {
        "style": "three_line",
        "font_size_pt": _number(table.get("font_size_pt"), "table.font_size_pt", 6, 24),
        "caption_prefix": _caption_prefix(table.get("caption_prefix"), "table.caption_prefix"),
    }
    figure = spec.get("figure")
    _strict_keys(figure, _FIGURE_KEYS, "figure")
    figure_clean = {"caption_prefix": _caption_prefix(figure.get("caption_prefix"), "figure.caption_prefix")}
    return {
        "schema_version": 1,
        "name": name,
        "page": {"size": "A4", "margins_cm": margins_clean, "page_number": {"enabled": page_number["enabled"], "position": page_number["position"]}},
        "body": body_clean,
        "headings": headings_clean,
        "table": table_clean,
        "figure": figure_clean,
    }


def parse_format_spec(source):
    if not isinstance(source, str) or len(source) > 100_000:
        raise FormatSpecError("FormatSpec JSON must be text under 100 KB.")
    try:
        parsed = json.loads(source)
    except json.JSONDecodeError as exc:
        raise FormatSpecError(f"Invalid JSON at line {exc.lineno}, column {exc.colno}.") from exc
    return validate_format_spec(parsed)


def load_builtin_presets(directory):
    root = Path(directory)
    presets = {}
    for path in sorted(root.glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as handle:
                spec = validate_format_spec(json.load(handle))
        except (OSError, json.JSONDecodeError, FormatSpecError) as exc:
            raise FormatSpecError(f"Built-in preset {path.name} is invalid: {exc}") from exc
        if spec["name"] in presets:
            raise FormatSpecError(f"Duplicate built-in preset name: {spec['name']}")
        presets[spec["name"]] = spec
    return presets


def load_user_presets(path):
    file_path = Path(path)
    if not file_path.exists():
        return {}
    try:
        content = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FormatSpecError(f"Custom preset storage is unreadable: {exc}") from exc
    if not isinstance(content, dict):
        raise FormatSpecError("Custom preset storage must be an object.")
    result = {}
    for name, spec in content.items():
        normalized = validate_format_spec(spec)
        if normalized["name"] != name:
            raise FormatSpecError(f"Preset key {name!r} does not match its name field.")
        result[name] = normalized
    return result


def save_user_preset(path, name, spec, *, builtin_names=()):
    clean_name = _string(name, "preset name")
    if clean_name in set(builtin_names):
        raise FormatSpecError("Built-in presets cannot be overwritten.")
    updated_spec = dict(spec)
    updated_spec["name"] = clean_name
    normalized = validate_format_spec(updated_spec)
    file_path = Path(path)
    presets = load_user_presets(file_path)
    presets[clean_name] = normalized
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(presets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return normalized


def delete_user_preset(path, name, *, builtin_names=()):
    if name in set(builtin_names):
        raise FormatSpecError("Built-in presets cannot be deleted.")
    file_path = Path(path)
    presets = load_user_presets(file_path)
    existed = name in presets
    presets.pop(name, None)
    if file_path.exists():
        file_path.write_text(json.dumps(presets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return existed
