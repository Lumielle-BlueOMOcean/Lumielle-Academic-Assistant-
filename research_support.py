"""Shared Research Fact Layer parsing, migration, planning, and chapter retrieval."""

from __future__ import annotations

import json
import re
import uuid

from context_digest_support import balanced_excerpt
from document_support import DEFAULT_CHUNK_CHARS, parse_document_in_chunks
from relevance_support import multilingual_terms
from structured_output_support import parse_first_json_value


PRESET_SECTIONS = (
    {"id": "background", "name_en": "Research Background", "name_zh": "研究背景", "allow_writing_grounding": False},
    {"id": "requirements", "name_en": "Assignment / Research Requirements", "name_zh": "任务书与研究要求", "allow_writing_grounding": False},
    {"id": "objectives", "name_en": "Research Objectives & Questions", "name_zh": "研究目标与研究问题", "allow_writing_grounding": True},
    {"id": "theory", "name_en": "Theory / Concepts / Variables", "name_zh": "理论、概念与变量定义", "allow_writing_grounding": True},
    {"id": "methods", "name_en": "Research / Experimental Methods", "name_zh": "研究方法与试验方法", "allow_writing_grounding": True},
    {"id": "data", "name_en": "Experimental / Survey Data", "name_zh": "实验、调查与统计数据", "allow_writing_grounding": True},
    {"id": "results", "name_en": "Results / Findings", "name_zh": "研究结果与发现", "allow_writing_grounding": True},
    {"id": "constraints", "name_en": "Constraints / Boundaries", "name_zh": "限制条件与适用边界", "allow_writing_grounding": True},
)
LEGACY_SECTION_ID = "legacy_unclassified"


def _new_section(definition):
    return {
        "id": definition["id"],
        "name": definition["name_en"],
        "name_en": definition["name_en"],
        "name_zh": definition["name_zh"],
        "kind": "preset",
        "allow_writing_grounding": definition["allow_writing_grounding"],
        "modules": [],
    }


def create_default_research_base():
    sections = {item["id"]: _new_section(item) for item in PRESET_SECTIONS}
    sections[LEGACY_SECTION_ID] = {
        "id": LEGACY_SECTION_ID,
        "name": "Legacy / Unclassified Research Content",
        "name_en": "Legacy / Unclassified Research Content",
        "name_zh": "旧版 / 未分类科研内容",
        "kind": "system",
        "allow_writing_grounding": False,
        "modules": [],
    }
    return {"schema_version": 2, "sections": sections}


def _normalize_fact(module, section_id, allow_grounding, source_id=None, source_chunk=None):
    if not isinstance(module, dict):
        return None
    result = dict(module)
    result["id"] = str(result.get("id") or uuid.uuid4())
    result["section"] = section_id
    result["title"] = str(result.get("title") or result.get("name") or "Untitled fact").strip()
    kind = str(result.get("type", "text")).strip().lower()
    result["type"] = kind if kind in {"text", "table", "fact"} else "text"
    if result["type"] == "table":
        result["columns"] = [str(value) for value in result.get("columns", []) if value is not None]
        rows = result.get("data", [])
        result["data"] = rows if isinstance(rows, list) else []
        result["content"] = str(result.get("content") or "")
    else:
        result["content"] = str(result.get("content") or result.get("text") or "")
    tags = result.get("tags", [])
    result["tags"] = [str(tag).strip() for tag in tags if str(tag).strip()] if isinstance(tags, list) else []
    result["role"] = str(result.get("role", "") or "")[:240]
    result["source_id"] = str(result.get("source_id") or source_id or "")
    chunk = result.get("source_chunk", source_chunk or 1)
    result["source_chunk"] = int(chunk) if isinstance(chunk, int) and not isinstance(chunk, bool) and chunk > 0 else int(source_chunk or 1)
    # The explicit section toggle is authoritative; per-fact values may only narrow it.
    result["allow_writing_grounding"] = bool(allow_grounding and result.get("allow_writing_grounding", True))
    binding = result.get("binding_mode", "auto")
    result["binding_mode"] = binding if binding in {"auto", "global", "chapters"} else "auto"
    chapter_ids = result.get("chapter_ids", [])
    result["chapter_ids"] = [str(value) for value in chapter_ids] if isinstance(chapter_ids, list) else []
    return result


def _normalize_modules(modules, section_id, allow_grounding):
    result = []
    for module in modules if isinstance(modules, list) else []:
        normalized = _normalize_fact(module, section_id, allow_grounding)
        if normalized:
            result.append(normalized)
    return result


def migrate_research_base(value):
    """Read old broad sections without discarding any material or custom sections."""
    if not isinstance(value, dict):
        return create_default_research_base()
    if value.get("schema_version", 0) >= 2 and isinstance(value.get("sections"), dict):
        base = dict(value)
        sections = dict(value["sections"])
        for definition in PRESET_SECTIONS:
            section_id = definition["id"]
            section = sections.get(section_id)
            if not isinstance(section, dict):
                section = _new_section(definition)
            else:
                section = dict(section)
                section.setdefault("id", section_id)
                section.setdefault("name_en", definition["name_en"])
                section.setdefault("name_zh", definition["name_zh"])
                section.setdefault("kind", "preset")
                section.setdefault("allow_writing_grounding", definition["allow_writing_grounding"])
                section["modules"] = _normalize_modules(section.get("modules", []), section_id, bool(section["allow_writing_grounding"]))
            sections[section_id] = section
        if LEGACY_SECTION_ID not in sections:
            sections[LEGACY_SECTION_ID] = create_default_research_base()["sections"][LEGACY_SECTION_ID]
        else:
            legacy = dict(sections[LEGACY_SECTION_ID])
            legacy["allow_writing_grounding"] = False
            legacy["modules"] = _normalize_modules(legacy.get("modules", []), LEGACY_SECTION_ID, False)
            sections[LEGACY_SECTION_ID] = legacy
        for section_id, section in list(sections.items()):
            if section_id in {item["id"] for item in PRESET_SECTIONS} | {LEGACY_SECTION_ID}:
                continue
            if isinstance(section, dict):
                section = dict(section)
                section.setdefault("id", section_id)
                section.setdefault("kind", "custom")
                section.setdefault("allow_writing_grounding", False)
                section["modules"] = _normalize_modules(section.get("modules", []), section_id, bool(section["allow_writing_grounding"]))
                sections[section_id] = section
        base["schema_version"] = 2
        base["sections"] = sections
        return base

    base = create_default_research_base()
    sections = base["sections"]
    for old_key, target in (("background", "background"), ("requirements", "requirements")):
        old = value.get(old_key, {})
        sections[target]["modules"] = _normalize_modules(old.get("modules", []) if isinstance(old, dict) else [], target, sections[target]["allow_writing_grounding"])
    content = value.get("content", {})
    mixed = content.get("modules", []) if isinstance(content, dict) else []
    sections[LEGACY_SECTION_ID]["modules"] = _normalize_modules(mixed, LEGACY_SECTION_ID, False)
    # Preserve unrecognized legacy fields for possible future recovery.
    base["legacy_fields"] = {key: item for key, item in value.items() if key not in {"background", "requirements", "content", "schema_version"}}
    return base


def add_custom_section(research_base, name, allow_writing_grounding=False):
    base = migrate_research_base(research_base)
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("A custom section name is required.")
    section_id = "custom_" + uuid.uuid4().hex[:12]
    base["sections"][section_id] = {
        "id": section_id, "name": clean_name, "name_en": clean_name, "name_zh": clean_name,
        "kind": "custom", "allow_writing_grounding": bool(allow_writing_grounding), "modules": [],
    }
    return base, section_id


def remove_custom_section(research_base, section_id):
    base = migrate_research_base(research_base)
    section = base["sections"].get(section_id)
    if not isinstance(section, dict) or section.get("kind") != "custom":
        return base, False
    del base["sections"][section_id]
    return base, True


def normalize_research_source(source, filename="pasted.txt", source_id=None):
    """Normalize typed text or extracted UTF-8/TXT file bytes to one parse input."""
    if isinstance(source, bytes):
        try:
            text = source.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("The text file is not valid UTF-8.") from exc
        source_type = "file"
    elif isinstance(source, str):
        text = source
        source_type = "pasted_text"
    else:
        raise TypeError("Research source must be text or bytes.")
    return {
        "source_id": str(source_id or uuid.uuid4()),
        "title": str(filename or "Pasted research text"),
        "filename": str(filename or ""),
        "source_type": source_type,
        "text": text,
    }


def _parse_array(response):
    parsed = parse_first_json_value(response)
    if isinstance(parsed, dict):
        for key in ("facts", "modules", "items"):
            if key in parsed:
                return parsed[key] if isinstance(parsed[key], list) else None
        if (parsed.get("title") or parsed.get("name")) and (
            "content" in parsed or "text" in parsed or "columns" in parsed or "data" in parsed
        ):
            return [parsed]
        return None
    return parsed if isinstance(parsed, list) else None


def _is_valid_research_module(module):
    if not isinstance(module, dict):
        return False
    if not str(module.get("title") or module.get("name") or "").strip():
        return False
    kind = str(module.get("type", "text") or "text").strip().casefold()
    if kind in {"text", "fact"}:
        content = module.get("content", module.get("text", ""))
        return isinstance(content, str) and bool(content.strip())
    if kind == "table":
        columns = module.get("columns")
        rows = module.get("data")
        return (
            isinstance(columns, list)
            and any(str(column).strip() for column in columns)
            and isinstance(rows, list)
            and any(isinstance(row, list) and row for row in rows)
        )
    return False


def parse_research_source(document_text, section_id, llm_call, *, locale="en", max_chars=DEFAULT_CHUNK_CHARS, source_id=None, allow_writing_grounding=True):
    """Extract facts from every ordered document chunk and merge exact duplicates in order."""
    if not isinstance(document_text, str) or not document_text.strip():
        return {"status": "error", "facts": [], "chunks_total": 0, "failed_chunk": 0, "message": "No text was provided."}
    section = next((item for item in PRESET_SECTIONS if item["id"] == section_id), None)
    purpose = section["name_zh"] if locale == "zh" and section else section["name_en"] if section else str(section_id)

    def normalize(modules):
        return [
            fact
            for module in modules
            if _is_valid_research_module(module)
            for fact in [_normalize_fact(module, section_id, allow_writing_grounding, source_id)]
            if fact
        ]

    result = parse_document_in_chunks(
        document_text,
        purpose + ("。只输出顶层 facts 为数组的 JSON 对象；模块包含 title、type、content 或表格 columns/data。" if locale == "zh" else ". Return a JSON object with a top-level facts array; modules contain title, type, content, or table columns/data."),
        llm_call,
        parse_response=_parse_array,
        normalize_modules=normalize,
        locale=locale,
        max_chars=max_chars,
    )
    if result["status"] != "ok":
        return {
            "status": "error", "facts": [], "chunks_total": result.get("chunks_total", 0),
            "failed_chunk": result.get("failed_chunk", 0), "error_type": result.get("error_type"),
            "failure_stage": result.get("failure_stage"), "failure_code": result.get("failure_code"),
            "message": result.get("message", "Research parsing failed."),
        }
    facts = []
    seen = set()
    for fact in result["modules"]:
        fact = _normalize_fact(fact, section_id, allow_writing_grounding, source_id, fact.get("source_chunk"))
        signature = (fact["type"], fact["title"].casefold(), fact["content"].strip().casefold(), json.dumps(fact.get("data", []), ensure_ascii=False, sort_keys=True))
        if signature in seen:
            continue
        seen.add(signature)
        facts.append(fact)
    return {"status": "ok", "facts": facts, "chunks_total": result["chunks_total"], "failed_chunk": 0, "message": ""}


def replace_section_facts(research_base, section_id, parse_result, *, append=True):
    """Return a new research base only when the complete parse succeeded."""
    base = migrate_research_base(research_base)
    if not isinstance(parse_result, dict) or parse_result.get("status") != "ok":
        return base, False
    section = base["sections"].get(section_id)
    if not isinstance(section, dict):
        return base, False
    incoming = parse_result.get("facts", [])
    if not isinstance(incoming, list):
        return base, False
    section["modules"] = list(section.get("modules", [])) + incoming if append else incoming
    return base, True


def _table_rows_text(columns, rows):
    return f"columns={json.dumps(columns, ensure_ascii=False, separators=(',', ':'))}; rows=" + json.dumps(
        rows, ensure_ascii=False, separators=(",", ":")
    )


def _table_excerpt(columns, rows, max_chars):
    full = _table_rows_text(columns, rows)
    if len(full) <= max_chars:
        return full
    if max_chars <= 0:
        return ""
    rows = rows if isinstance(rows, list) else []
    omitted_note = " [evenly sampled rows; omitted rows remain stored]"
    for count in range(len(rows), 0, -1):
        if count == 1:
            indices = [len(rows) - 1]
        else:
            indices = sorted({round(position * (len(rows) - 1) / (count - 1)) for position in range(count)})
        sampled = [rows[index] for index in indices]
        value = _table_rows_text(columns, sampled)
        if len(indices) < len(rows):
            value += omitted_note
        if len(value) <= max_chars:
            return value
    header = f"columns={json.dumps(columns, ensure_ascii=False, separators=(',', ':'))}; rows="
    marker = " [rows omitted; source table remains stored]"
    if max_chars <= len(marker):
        return balanced_excerpt(header + marker, max_chars)
    return balanced_excerpt(header, max_chars - len(marker)) + marker


def _planning_fact_line(fact, budget=None):
    title = str(fact.get("title", ""))
    tags = ", ".join(str(tag) for tag in fact.get("tags", []) if str(tag).strip())
    suffix = f" (tags: {tags})" if tags else ""
    prefix = f"- {title}: "
    if fact.get("type") == "table":
        columns, rows = fact.get("columns", []), fact.get("data", [])
        content = _table_rows_text(columns, rows)
        if budget is not None and len(prefix) + len(content) + len(suffix) > budget:
            content = _table_excerpt(columns, rows, max(0, budget - len(prefix) - len(suffix)))
    else:
        content = str(fact.get("content", ""))
        if budget is not None:
            content = balanced_excerpt(content, max(0, budget - len(prefix) - len(suffix)))
    line = prefix + content + suffix
    if budget is not None and len(line) > budget:
        line = balanced_excerpt(line, budget)
    return line


def build_research_planning_context(research_base, max_chars=12000):
    """Build a bounded, ordered digest in which every saved section/fact gets representation."""
    base = migrate_research_base(research_base)
    sections = []
    records = []
    for section_id, section in base["sections"].items():
        modules = section.get("modules", [])
        if not modules:
            continue
        header = f"[{section.get('name_en') or section.get('name') or section_id} | planning context]"
        sections.append((section_id, header, modules))
        records.extend((section_id, fact) for fact in modules)
    if not records:
        return ""
    full_lines = []
    for _, header, modules in sections:
        full_lines.append(header)
        full_lines.extend(_planning_fact_line(fact) for fact in modules)
    full = "\n".join(full_lines)
    budget = max(0, int(max_chars))
    if len(full) <= budget:
        return full

    # Allocate by fact count, not source position. Every section and fact therefore
    # contributes even when later records would have fallen after a prefix limit.
    header_chars = sum(len(header) + 1 for _, header, _ in sections)
    available = max(0, budget - header_chars - len(records))
    per_fact_budget = available // len(records) if records else 0
    section_map = {section_id: header for section_id, header, _ in sections}
    result_lines = []
    for _, header, modules in sections:
        if not modules:
            continue
        result_lines.append(header)
        result_lines.extend(_planning_fact_line(fact, per_fact_budget) for fact in modules)
    digest = "\n".join(result_lines)
    if len(digest) <= budget:
        return digest
    # If metadata alone exceeds the target, deterministically distribute the
    # remaining characters across the already ordered fact lines.
    headers = set(section_map.values())
    compact_headers = [line for line in result_lines if line in headers]
    available = max(0, budget - sum(len(line) + 1 for line in compact_headers))
    fact_lines = [line for line in result_lines if line not in headers]
    per_line = available // max(1, len(fact_lines))
    return balanced_excerpt("\n".join(compact_headers + [balanced_excerpt(line, per_line) for line in fact_lines]), budget)


def build_research_planning_prompt_context(research_base, global_topic="", *, locale="en", max_chars=12000):
    """Create the exact all-fact planning block consumed by outline and library review."""
    digest = build_research_planning_context(research_base, max_chars=max_chars)
    if locale == "zh":
        return f"【GLOBAL RESEARCH TOPIC】\n{global_topic}\n\n【RESEARCH PLANNING DIGEST】\n{digest}"
    return f"[GLOBAL RESEARCH TOPIC]\n{global_topic}\n\n[RESEARCH PLANNING DIGEST]\n{digest}"


_terms = multilingual_terms


def select_research_grounding_for_chapter(research_base, chapter_title, chapter_description="", outline_position="", *, chapter_id=None, limit=8):
    """Select only user-enabled facts; explicit chapter/global bindings outrank auto relevance."""
    base = migrate_research_base(research_base)
    query = _terms(" ".join((chapter_title or "", chapter_description or "", outline_position or "")))
    eligible = []
    for section_id, section in base["sections"].items():
        if not section.get("allow_writing_grounding", False):
            continue
        for fact in section.get("modules", []):
            if not fact.get("allow_writing_grounding", section.get("allow_writing_grounding", False)):
                continue
            item = dict(fact)
            item.setdefault("section", section_id)
            eligible.append(item)
    explicit = [f for f in eligible if f.get("binding_mode") == "chapters" and chapter_id and str(chapter_id) in f.get("chapter_ids", [])]
    global_facts = [f for f in eligible if f.get("binding_mode") == "global" or f.get("section") == "constraints" or "hard_constraint" in f.get("tags", [])]
    excluded = {f.get("id") for f in explicit + global_facts}
    ranked = []
    for fact in eligible:
        if fact.get("id") in excluded or fact.get("binding_mode") != "auto":
            continue
        fact_terms = _terms(" ".join((fact.get("title", ""), fact.get("content", ""), " ".join(fact.get("tags", [])))))
        score = len(query & fact_terms)
        if score:
            ranked.append((score, fact))
    ranked.sort(key=lambda pair: (-pair[0], str(pair[1].get("id", ""))))
    max_auto = max(0, int(limit) - len(explicit) - len(global_facts))
    return explicit + global_facts + [fact for _, fact in ranked[:max_auto]]


def build_chapter_research_context(research_base, chapter_title, chapter_description="", outline_position="", *, chapter_id=None, locale="en", limit=8, max_chars=8000):
    """Format a bounded, selected set of eligible facts into distinct prompt blocks."""
    selected = select_research_grounding_for_chapter(
        research_base, chapter_title, chapter_description, outline_position,
        chapter_id=chapter_id, limit=limit,
    )
    def is_constraint(item):
        return item.get("section") == "constraints" or "hard_constraint" in item.get("tags", [])

    constraint_items = [item for item in selected if is_constraint(item)]
    fact_items = [item for item in selected if not is_constraint(item)]
    omitted = False
    notice = (
        "[Additional selected facts omitted by context budget; saved facts are unchanged.]"
        if locale != "zh" else "【上下文预算不足，省略了部分已选事实；已保存的事实未被修改。】"
    )
    usable_budget = max(0, int(max_chars))
    included = []
    for item in constraint_items + fact_items:
        line = _planning_fact_line(item)
        used = sum(len(value) + 1 for _, value in included)
        reserve = len(notice) + 1 if len(line) + used > usable_budget else 0
        remaining = usable_budget - reserve - used
        if len(line) <= remaining:
            included.append((item, line))
        elif not included and remaining > 0:
            included.append((item, _planning_fact_line(item, remaining)))
        else:
            omitted = True
    if omitted:
        while included and sum(len(value) + 1 for _, value in included) + len(notice) > usable_budget:
            included.pop()
        if len(notice) <= usable_budget:
            included.append((None, notice))
    constraint_lines = []
    fact_lines = []
    for item, line in included:
        if item is None:
            continue
        destination = constraint_lines if is_constraint(item) else fact_lines
        destination.append(line)
    if omitted and included and included[-1][0] is None:
        if fact_lines:
            fact_lines.append(notice)
        else:
            constraint_lines.append(notice)
    constraint_block = "\n".join(constraint_lines)
    fact_block = "\n".join(fact_lines)
    included_facts = [item for item, _ in included if item is not None]
    if locale == "zh":
        constraint_label = "无已启用的全局研究约束。"
        facts_label = "无匹配且已启用的研究事实。"
    else:
        constraint_label = "No enabled global research constraints."
        facts_label = "No matching, enabled research facts."
    return {
        "research_constraints": constraint_block or constraint_label,
        "research_facts": fact_block or facts_label,
        "selected_research_facts": included_facts,
        "omitted_research_fact_count": max(0, len(selected) - sum(item is not None for item, _ in included)),
    }


def accept_smart_inbox_facts(pending, section_id, tags, role, allow_grounding):
    """Apply reviewed Smart Inbox fields to normalized facts before persistence."""
    if not isinstance(pending, dict):
        return []
    source_title = str((pending.get("source") or {}).get("title", ""))
    accepted = []
    for item in pending.get("facts", []):
        if not isinstance(item, dict):
            continue
        fact = dict(item)
        fact["section"] = str(section_id)
        fact["tags"] = list(dict.fromkeys(
            [str(value).strip() for value in fact.get("tags", []) if str(value).strip()]
            + [str(value).strip() for value in tags if str(value).strip()]
        ))
        fact["role"] = str(role or "").strip()[:240]
        fact["allow_writing_grounding"] = bool(allow_grounding)
        fact["source_title"] = source_title
        accepted.append(fact)
    return accepted


def parse_classification_suggestion(response, valid_section_ids):
    """Parse advisory Smart Inbox classification; this never mutates or moves any facts."""
    try:
        value = json.loads(str(response or ""))
    except json.JSONDecodeError:
        return {"section": "", "tags": [], "role": ""}
    if not isinstance(value, dict):
        return {"section": "", "tags": [], "role": ""}
    section = value.get("section") if value.get("section") in set(valid_section_ids) else ""
    tags = value.get("tags", [])
    tags = [str(tag).strip() for tag in tags if str(tag).strip()] if isinstance(tags, list) else []
    return {"section": section, "tags": tags, "role": str(value.get("role", ""))[:240]}
