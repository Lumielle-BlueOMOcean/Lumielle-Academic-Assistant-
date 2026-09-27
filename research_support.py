"""Shared Research Fact Layer parsing, migration, planning, and chapter retrieval."""

from __future__ import annotations

import json
import re
import uuid

from document_support import DEFAULT_CHUNK_CHARS, parse_document_in_chunks


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
    text = str(response or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(?:\[.*\]|\{.*\})", text, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if isinstance(parsed, dict):
        for key in ("facts", "modules", "items"):
            if isinstance(parsed.get(key), list):
                return parsed[key]
        return None
    return parsed if isinstance(parsed, list) else None


def parse_research_source(document_text, section_id, llm_call, *, locale="en", max_chars=DEFAULT_CHUNK_CHARS, source_id=None, allow_writing_grounding=True):
    """Extract facts from every ordered document chunk and merge exact duplicates in order."""
    if not isinstance(document_text, str) or not document_text.strip():
        return {"status": "error", "facts": [], "chunks_total": 0, "failed_chunk": 0, "message": "No text was provided."}
    section = next((item for item in PRESET_SECTIONS if item["id"] == section_id), None)
    purpose = section["name_zh"] if locale == "zh" and section else section["name_en"] if section else str(section_id)

    def normalize(modules):
        return [fact for fact in (_normalize_fact(m, section_id, allow_writing_grounding, source_id) for m in modules) if fact]

    result = parse_document_in_chunks(
        document_text,
        purpose + ("。只输出包含 title、type、content、tags 的事实模块 JSON 数组。" if locale == "zh" else ". Return structured facts as a JSON array with title, type, content, and tags."),
        llm_call,
        parse_response=_parse_array,
        normalize_modules=normalize,
        locale=locale,
        max_chars=max_chars,
    )
    if result["status"] != "ok":
        return {"status": "error", "facts": [], "chunks_total": result.get("chunks_total", 0), "failed_chunk": result.get("failed_chunk", 0), "message": result.get("message", "Research parsing failed.")}
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


def build_research_planning_context(research_base, max_chars=12000):
    """Planning sees all parsed research facts, including sections disabled for prose grounding."""
    base = migrate_research_base(research_base)
    lines = []
    for section_id, section in base["sections"].items():
        modules = section.get("modules", [])
        if not modules:
            continue
        lines.append(f"[{section.get('name_en') or section.get('name') or section_id} | planning context]")
        for fact in modules:
            title = fact.get("title", "")
            content = fact.get("content", "")
            if fact.get("type") == "table":
                content = f"columns={fact.get('columns', [])}; rows={fact.get('data', [])}"
            tags = ", ".join(fact.get("tags", []))
            lines.append(f"- {title}: {content}" + (f" (tags: {tags})" if tags else ""))
    joined = "\n".join(lines)
    return joined if len(joined) <= max_chars else joined[:max_chars] + "\n[Planning context display truncated; stored facts remain intact.]"


def _terms(text):
    ascii_terms = {part.casefold() for part in re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*", str(text or "")) if len(part) > 1}
    cjk = re.findall(r"[\u3400-\u9fff]+", str(text or ""))
    for sequence in cjk:
        ascii_terms.update(sequence[i:i + 2] for i in range(len(sequence) - 1))
    return ascii_terms


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
    constraints = []
    facts = []
    for item in selected:
        title = str(item.get("title", ""))[:180]
        if item.get("type") == "table":
            content = f"columns={item.get('columns', [])}; rows={item.get('data', [])}"
        else:
            content = str(item.get("content", ""))
        content = content[:1200]
        tags = ", ".join(str(tag) for tag in item.get("tags", [])[:12])
        line = f"- {title}: {content}" + (f" (tags: {tags})" if tags else "")
        if item.get("section") == "constraints" or "hard_constraint" in item.get("tags", []):
            constraints.append(line)
        else:
            facts.append(line)
    constraint_block = "\n".join(constraints)
    fact_block = "\n".join(facts)
    if len(constraint_block) + len(fact_block) > max_chars:
        remaining = max_chars
        constraint_block = constraint_block[:remaining]
        remaining -= len(constraint_block)
        fact_block = fact_block[:max(0, remaining)]
    if locale == "zh":
        constraint_label = "无已启用的全局研究约束。"
        facts_label = "无匹配且已启用的研究事实。"
    else:
        constraint_label = "No enabled global research constraints."
        facts_label = "No matching, enabled research facts."
    return {
        "research_constraints": constraint_block or constraint_label,
        "research_facts": fact_block or facts_label,
        "selected_research_facts": selected,
    }


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
