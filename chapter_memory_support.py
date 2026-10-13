"""Source-faithful chapter memory extraction and local context retrieval."""

from __future__ import annotations

import hashlib
import math
import re
import time

from chapter_memory_store import draft_hash, read_chapter_memory, save_chapter_memory
from context_digest_support import balanced_excerpt
from document_support import DEFAULT_CHUNK_CHARS, chunk_document_text
from structured_output_support import parse_first_json_value


MEMORY_CHUNK_CHARS = DEFAULT_CHUNK_CHARS
MEMORY_CHUNK_TOKEN_BUDGET = 1800
MAX_LENGTH_SPLITS = 2
MAX_STRUCTURED_ATTEMPTS = 3
MAX_CHUNK_SUMMARY_CHARS = 2000
MAX_ITEM_STATEMENT_CHARS = 1200
MAX_SOURCE_QUOTE_CHARS = 500
_TRANSIENT_ERRORS = {"rate_limited", "timeout", "connection_error", "server_error"}
_TRANSIENT_FINISH_REASONS = {"insufficient_system_resource", "aborted"}
DEFAULT_PROMPT_TOKEN_BUDGET = 7000
_ALLOWED_MEMORY_TYPES = {
    "definition", "terminology", "method", "data_or_number", "finding",
    "conclusion", "limitation", "hypothesis", "cross_chapter_dependency",
}


class MemoryBuildError(RuntimeError):
    """Raised when a complete, source-checked memory cannot be built."""


class ContextBudgetError(ValueError):
    """Raised when the required prompt sections cannot fit the local budget."""


def structured_memory_llm_call(llm_call, locale="en"):
    """Bind the existing configured model to safe structured chapter-memory calls."""
    system_prompt = "你是章节记忆提取助手，只返回规定的 JSON 结构。" if locale == "zh" else "You extract chapter memory and return only the requested JSON structure."

    def call(prompt, **kwargs):
        return llm_call(prompt, system_prompt=system_prompt, **kwargs)

    return call


def _chunk_prompt(chunk, part_number, total_parts, locale, correction=False):
    if locale == "zh":
        retry = "上一次输出格式无效。请严格按以下 JSON 示例修正，不要输出代码围栏或解释。\n" if correction else ""
        return (
            "你要从论文单个有序分块中提取供后续章节使用的学术记忆。只使用本分块明确存在的信息，不补全、不猜测、不引入其他来源。\n"
            "保留必要主体、适用范围、年份和限定条件，尤其是定义、术语、方法、数据或数字、发现、结论、限制、假设与跨章节依赖。"
            "不要把每句话都变成记忆。每条原子信息必须给出本分块中的连续短引文。引文必须逐字复制原文。\n"
            "若没有值得后续章节保持一致的信息，返回 chunk_status=\"no_relevant_content\"、summary=\"\"、items=[]。\n"
            "只输出一个 JSON 对象，结构示例：\n"
            '{"chunk_status":"content","summary":"本块主要论证","items":[{"memory_type":"data_or_number","statement":"某企业在正文所述评价中达到 DCMM 4级。","source_quote":"正文中的连续原文"}]}\n'
            "chunk_status 只能是 content 或 no_relevant_content；没有 items 时用 []；不得输出来源中不存在的信息。\n"
            + retry
            + f"[CHAPTER CHUNK {part_number}/{total_parts}]\n[CHAPTER TEXT]\n"
            + chunk
        )
    retry = "The previous response was invalid. Correct it to the exact JSON shape below, with no code fence or explanation.\n" if correction else ""
    return (
        "Extract academic memory from one ordered chapter chunk for later writing. Use only information explicitly present in this chunk; do not complete, guess, or import external facts.\n"
        "Preserve the subject, scope, year, and qualifiers. Capture useful definitions, terminology, methods, data or numbers, findings, conclusions, limitations, hypotheses, and cross-chapter dependencies. Do not convert every sentence into a memory. Every atomic item needs a short continuous quote copied exactly from this chunk.\n"
        "If the chunk has no useful continuity information, return chunk_status=\"no_relevant_content\", summary=\"\", items=[].\n"
        "Return one JSON object in this shape:\n"
        '{"chunk_status":"content","summary":"The chunk’s main argument","items":[{"memory_type":"data_or_number","statement":"The enterprise reached DCMM Level 4 in the evaluation described in the chapter.","source_quote":"A continuous exact quote from this source"}]}\n'
        "chunk_status is content or no_relevant_content. Use [] when there are no items. Never add unsupported information.\n"
        + retry
        + f"[CHAPTER CHUNK {part_number}/{total_parts}]\n[CHAPTER TEXT]\n"
        + chunk
    )


def _response_content(llm_call, prompt, *, max_tokens=MEMORY_CHUNK_TOKEN_BUDGET, sleep=None):
    """Make a bounded metadata-aware call without exposing provider response details."""
    sleep = sleep or time.sleep
    for attempt in range(MAX_STRUCTURED_ATTEMPTS):
        try:
            result = llm_call(
                prompt,
                max_tokens=max_tokens,
                json_mode=True,
                return_metadata=True,
            )
        except Exception as exc:
            code = getattr(exc, "error_code", None)
            if code not in _TRANSIENT_ERRORS or attempt + 1 >= MAX_STRUCTURED_ATTEMPTS:
                raise MemoryBuildError("The configured model could not complete memory extraction.") from None
            sleep(1 if attempt == 0 else 2)
            continue
        if not isinstance(result, dict):
            raise MemoryBuildError("The structured memory response was unavailable.")
        error_code = result.get("error_code")
        if error_code:
            if error_code in _TRANSIENT_ERRORS and attempt + 1 < MAX_STRUCTURED_ATTEMPTS:
                sleep(1 if attempt == 0 else 2)
                continue
            raise MemoryBuildError("The configured model could not complete memory extraction.")
        finish_reason = result.get("finish_reason")
        if finish_reason in _TRANSIENT_FINISH_REASONS:
            if attempt + 1 < MAX_STRUCTURED_ATTEMPTS:
                sleep(1 if attempt == 0 else 2)
                continue
            raise MemoryBuildError("The model response was interrupted repeatedly.")
        if finish_reason == "length":
            return None, "length"
        if finish_reason != "stop":
            raise MemoryBuildError("The memory response was incomplete or filtered.")
        content = result.get("content")
        if not isinstance(content, str) or not content.strip():
            return None, "empty"
        value = parse_first_json_value(content)
        if not isinstance(value, dict):
            return None, "invalid"
        return value, "ok"
    raise MemoryBuildError("The configured model could not complete memory extraction.")


def _normalize_chunk(value, source_chunk, global_offset):
    if not isinstance(value, dict) or set(value) - {"chunk_status", "summary", "items"}:
        raise MemoryBuildError("The structured memory shape was invalid.")
    status = value.get("chunk_status")
    summary = value.get("summary")
    raw_items = value.get("items")
    if not isinstance(status, str) or status not in {"content", "no_relevant_content"}:
        raise MemoryBuildError("The structured memory status was invalid.")
    if not isinstance(summary, str) or not isinstance(raw_items, list):
        raise MemoryBuildError("The structured memory fields were invalid.")
    if len(summary) > MAX_CHUNK_SUMMARY_CHARS:
        raise MemoryBuildError("The structured memory summary was too long.")
    if status == "no_relevant_content":
        if summary.strip() or raw_items:
            raise MemoryBuildError("An empty memory chunk contained unexpected content.")
        return {
            "status": status,
            "summary": "",
            "items": [],
            "source_start": global_offset,
            "source_end": global_offset + len(source_chunk),
        }

    items = []
    for raw in raw_items:
        if not isinstance(raw, dict) or set(raw) - {"memory_type", "statement", "source_quote"}:
            raise MemoryBuildError("A structured memory item had an invalid shape.")
        memory_type = raw.get("memory_type")
        statement = raw.get("statement")
        quote = raw.get("source_quote")
        if not isinstance(memory_type, str) or memory_type not in _ALLOWED_MEMORY_TYPES:
            raise MemoryBuildError("A structured memory item used an unsupported type.")
        if not isinstance(statement, str) or not statement.strip():
            raise MemoryBuildError("A structured memory item had no statement.")
        if len(statement) > MAX_ITEM_STATEMENT_CHARS:
            raise MemoryBuildError("A structured memory statement was too long.")
        if not isinstance(quote, str) or not quote.strip():
            continue
        if len(quote) > MAX_SOURCE_QUOTE_CHARS:
            raise MemoryBuildError("A structured memory source quote was too long.")
        local_start = source_chunk.find(quote)
        if local_start < 0:
            continue
        items.append({
            "memory_type": memory_type,
            "statement": statement.strip(),
            "source_quote": quote,
            "source_start": global_offset + local_start,
            "source_end": global_offset + local_start + len(quote),
            "confirmed_for_writing": False,
            "verification_status": "draft_provisional",
        })
    summary = summary.strip()
    if not summary and not items:
        raise MemoryBuildError("The chunk contained no verifiable academic memory.")
    return {
        "status": status,
        "summary": summary,
        "items": items,
        "source_start": global_offset,
        "source_end": global_offset + len(source_chunk),
    }


def _split_source_segment(source, source_offset, part_number, total_parts, locale, llm_call, depth):
    if depth >= MAX_LENGTH_SPLITS or len(source) < 2:
        raise MemoryBuildError("The structured memory response remained truncated.")
    middle = len(source) // 2
    return (
        _extract_source_segment(source[:middle], source_offset, part_number, total_parts, locale, llm_call, depth + 1)
        + _extract_source_segment(source[middle:], source_offset + middle, part_number, total_parts, locale, llm_call, depth + 1)
    )


def _extract_source_segment(source, source_offset, part_number, total_parts, locale, llm_call, depth=0):
    value, state = _response_content(llm_call, _chunk_prompt(source, part_number, total_parts, locale))
    if state == "length":
        return _split_source_segment(source, source_offset, part_number, total_parts, locale, llm_call, depth)

    normalized = None
    needs_correction = state in {"invalid", "empty"}
    if not needs_correction:
        try:
            normalized = _normalize_chunk(value, source, source_offset)
        except MemoryBuildError:
            needs_correction = True
    if needs_correction:
        value, state = _response_content(
            llm_call,
            _chunk_prompt(source, part_number, total_parts, locale, correction=True),
        )
        if state == "length":
            return _split_source_segment(source, source_offset, part_number, total_parts, locale, llm_call, depth)
        if state != "ok":
            raise MemoryBuildError("The corrected memory response was invalid.")
        try:
            normalized = _normalize_chunk(value, source, source_offset)
        except MemoryBuildError:
            raise MemoryBuildError("The corrected memory response was invalid.") from None
    normalized["chunk_index"] = part_number
    return [normalized]


def _chunk_spans(text, max_chars):
    chunks = chunk_document_text(text, max_chars=max_chars)
    spans = []
    offset = 0
    for chunk in chunks:
        spans.append((chunk, offset))
        offset += len(chunk)
    if "".join(chunk for chunk, _ in spans) != text:
        raise MemoryBuildError("The chapter could not be divided without losing source text.")
    return spans


def build_chapter_memory_v2(chapter_text, llm_call, locale="en", max_chars=MEMORY_CHUNK_CHARS):
    """Extract complete, source-anchored memory; no snapshot is persisted here."""
    if not isinstance(chapter_text, str) or not chapter_text.strip():
        raise MemoryBuildError("There is no saved chapter text to analyze.")
    spans = _chunk_spans(chapter_text, max_chars)
    extracted = []
    for index, (chunk, offset) in enumerate(spans, start=1):
        extracted.extend(_extract_source_segment(chunk, offset, index, len(spans), locale, llm_call))
    chunk_statuses = [
        {
            "chunk_index": group["chunk_index"],
            "source_start": group["source_start"],
            "source_end": group["source_end"],
            "status": group["status"],
        }
        for group in extracted
    ]
    summaries = []
    for group in extracted:
        if group["status"] != "content":
            continue
        summary = group["summary"].strip()
        if summary and summary not in summaries:
            summaries.append(summary)
    items = []
    seen = set()
    for group in extracted:
        for item in group["items"]:
            identity = (item["memory_type"], item["statement"], item["source_quote"])
            if identity in seen:
                continue
            seen.add(identity)
            items.append(item)
    items.sort(key=lambda item: (item["source_start"], item["source_end"], item["memory_type"]))
    if not summaries and not items:
        raise MemoryBuildError("No useful academic continuity information was found in the chapter.")
    if not summaries:
        summaries = [item["statement"] for item in items[:30]]
    summary = "\n".join(summaries)
    if len(summary) > 8000:
        summary = summary[:7997].rstrip() + "..."
    return {
        "narrative_summary": summary,
        "completion_status": "complete",
        "items": items,
        "draft_hash": draft_hash(chapter_text),
        "chunks_processed": len(spans),
        "segments_processed": len(chunk_statuses),
        "chunk_statuses": chunk_statuses,
        "source_characters": len(chapter_text),
    }


def save_draft_and_maybe_refresh_memory(
    data_dir,
    drafts,
    chapter_id,
    draft_text,
    save_drafts,
    llm_call,
    *,
    locale="en",
    refresh_memory=False,
    legacy_summary=None,
    max_chars=MEMORY_CHUNK_CHARS,
):
    """Persist the body first; replace its derived memory only after a complete build."""
    updated_drafts = dict(drafts) if isinstance(drafts, dict) else {}
    updated_drafts[chapter_id] = draft_text
    try:
        if not save_drafts(updated_drafts):
            return {"saved": False, "memory_updated": False, "memory_reused": False, "memory_error": "draft_save_failed", "drafts": drafts}
    except Exception:
        return {"saved": False, "memory_updated": False, "memory_reused": False, "memory_error": "draft_save_failed", "drafts": drafts}

    result = {"saved": True, "memory_updated": False, "memory_reused": False, "memory_error": None, "drafts": updated_drafts}
    if not refresh_memory or not isinstance(draft_text, str) or not draft_text.strip():
        return result
    state = read_chapter_memory(data_dir, chapter_id, draft_text, legacy_summary=legacy_summary)
    if state["status"] == "current":
        result["memory_reused"] = True
        return result
    try:
        refresh_chapter_memory_snapshot(data_dir, chapter_id, draft_text, llm_call, locale=locale, max_chars=max_chars)
    except Exception:
        result["memory_error"] = "memory_update_failed"
        return result
    result["memory_updated"] = True
    return result


def refresh_chapter_memory_snapshot(data_dir, chapter_id, draft_text, llm_call, locale="en", max_chars=MEMORY_CHUNK_CHARS):
    """Build a complete new snapshot before atomically replacing the prior one."""
    if not isinstance(draft_text, str) or not draft_text.strip():
        raise MemoryBuildError("There is no saved chapter text to analyze.")
    state = read_chapter_memory(data_dir, chapter_id, draft_text)
    if state["status"] == "current":
        return {"updated": False, "reused": True, "memory": state}
    memory = build_chapter_memory_v2(draft_text, llm_call, locale=locale, max_chars=max_chars)
    save_chapter_memory(data_dir, chapter_id, draft_text, memory)
    return {"updated": True, "reused": False, "memory": memory}


def _leaf_nodes(outline):
    leaves = []

    def visit(nodes):
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            children = [child for child in (node.get("children") or []) if isinstance(child, dict)]
            if children:
                visit(children)
            elif node.get("id") is not None:
                leaves.append(node)

    visit(outline)
    return leaves


def _format_full_outline(outline):
    lines = []

    def visit(nodes, depth=0):
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            title = str(node.get("title", ""))
            description = str(node.get("desc", "") or "")
            detail = f": {description}" if description else ""
            lines.append(f"{'  ' * depth}- {title}{detail}")
            visit(node.get("children") or [], depth + 1)

    visit(outline)
    return "\n".join(lines)


def _terms(text, locale):
    value = str(text or "").casefold()
    if locale == "zh":
        terms = set()
        for token in re.findall(r"[\u3400-\u9fff]+|[a-z0-9]+", value):
            if re.fullmatch(r"[\u3400-\u9fff]+", token):
                terms.add(token)
                terms.update(token[index:index + 2] for index in range(len(token) - 1))
            elif token:
                terms.add(token)
        return terms
    return {token for token in re.findall(r"[a-z0-9]{2,}", value) if token}


def _context_line(chapter, item, locale):
    title = chapter.get("title", "")
    label = "已写章节记忆" if locale == "zh" else "Written chapter memory"
    quote_label = "原文依据" if locale == "zh" else "Source"
    confirmation = "【已确认用于后续写作】" if locale == "zh" and item["confirmed_for_writing"] else "[Confirmed for later writing] " if item["confirmed_for_writing"] else ""
    return f"[{label}: {title}] {confirmation}{item['statement']} ({quote_label}: {item['source_quote']})"


def build_context_sandwich_v2(
    current_chapter_id,
    outline,
    drafts,
    data_dir,
    *,
    legacy_summaries=None,
    global_topic="",
    target_word_count=5000,
    locale="en",
):
    """Select local chapter context in outline order, using actual saved drafts as truth."""
    leaves = _leaf_nodes(outline)
    ids = [str(node.get("id")) for node in leaves]
    current_id = str(current_chapter_id)
    if current_id not in ids:
        raise ValueError("The selected chapter is not a writable outline leaf")
    index = ids.index(current_id)
    drafts = drafts if isinstance(drafts, dict) else {}
    legacy_summaries = legacy_summaries if isinstance(legacy_summaries, dict) else {}
    current = leaves[index]
    states = {}
    entries = []
    missing = []
    irrelevant_memory_ids = []
    for chapter_index, chapter in enumerate(leaves):
        chapter_id = str(chapter.get("id"))
        body = drafts.get(chapter.get("id"), drafts.get(chapter_id, ""))
        side = "upstream" if chapter_index < index else "downstream" if chapter_index > index else None
        distance = abs(chapter_index - index)
        state = read_chapter_memory(
            data_dir, chapter_id, body,
            legacy_summary=legacy_summaries.get(chapter.get("id"), legacy_summaries.get(chapter_id)),
        )
        states[chapter_id] = state["status"]
        if side is None:
            continue
        if not body or not str(body).strip():
            entries.append({
                "memory_id": None,
                "chapter_id": chapter_id,
                "chapter_title": chapter.get("title", ""),
                "side": side,
                "priority": -100 + (1 / (abs(chapter_index - index) + 1)),
                "text": ("未写章节边界" if locale == "zh" else "Unwritten chapter boundary") + f": {chapter.get('title', '')}",
                "confirmed": False,
                "is_boundary": True,
            })
            continue
        if state["status"] == "current":
            current_terms = _terms(f"{current.get('title', '')} {current.get('desc', '')}", locale)
            distance = abs(chapter_index - index)
            for item in state["items"]:
                item_terms = _terms(item["statement"], locale)
                relevance = len(current_terms & item_terms)
                if not (
                    item["confirmed_for_writing"]
                    or relevance
                    or distance <= 1
                    or item["memory_type"] == "cross_chapter_dependency"
                ):
                    irrelevant_memory_ids.append(item["id"])
                    continue
                base_priority = 10000 if item["confirmed_for_writing"] else 0
                kind_priority = 500 if item["memory_type"] == "cross_chapter_dependency" else 100 if item["memory_type"] in {"definition", "terminology", "method", "data_or_number"} else 0
                entries.append({
                    "memory_id": item["id"],
                    "chapter_id": chapter_id,
                    "chapter_title": chapter.get("title", ""),
                    "side": side,
                    "priority": base_priority + relevance * 100 + kind_priority + max(0, 20 - distance),
                    "text": _context_line(chapter, item, locale),
                    "confirmed": bool(item["confirmed_for_writing"]),
                    "statement": item["statement"],
                    "source_quote": item["source_quote"],
                    "is_boundary": False,
                })
            summary = state["narrative_summary"]
            if isinstance(summary, str) and summary.strip():
                entries.append({
                    "memory_id": None,
                    "chapter_id": chapter_id,
                    "chapter_title": chapter.get("title", ""),
                    "side": side,
                    "priority": -1000 + (1 / (distance + 1)),
                    "text": f"[{chapter.get('title', '')}] {summary.strip()}",
                    "confirmed": False,
                    "is_boundary": False,
                    "is_summary": True,
                })
        else:
            missing.append({"chapter_id": chapter_id, "title": chapter.get("title", ""), "status": state["status"]})
            excerpt = balanced_excerpt(str(body), 500)
            if excerpt:
                label = "未验证正文摘录" if locale == "zh" else "unverified_draft_excerpt"
                entries.append({
                    "memory_id": None,
                    "chapter_id": chapter_id,
                    "chapter_title": chapter.get("title", ""),
                    "side": side,
                    "priority": 1 / (distance + 1),
                    "text": f"[{label}: {chapter.get('title', '')}] {excerpt}",
                    "confirmed": False,
                    "is_boundary": False,
                })

    entries.sort(key=lambda item: (-item["priority"], item["chapter_id"], item["memory_id"] or -1))
    global_outline = _format_full_outline(outline)
    upstream = [entry["text"] for entry in entries if entry["side"] == "upstream"]
    downstream = [entry["text"] for entry in entries if entry["side"] == "downstream"]
    selected = [entry["memory_id"] for entry in entries if entry["memory_id"] is not None]
    current_body = drafts.get(current.get("id"), drafts.get(current_id, ""))
    current_legacy = legacy_summaries.get(current.get("id"), legacy_summaries.get(current_id))
    return {
        "current_id": current_id,
        "global_topic": global_topic,
        "target_word_count": target_word_count,
        "global_outline": global_outline,
        "current_title": current.get("title", ""),
        "current_desc": current.get("desc", ""),
        "current_word_count": current.get("word_count", 0) or 0,
        "current_refs": current.get("references", []) or [],
        "upstream": "\n".join(upstream) or ("无已写上游章节。" if locale == "zh" else "No earlier written chapter."),
        "downstream": "\n".join(downstream) or ("无已写下游章节。" if locale == "zh" else "No later written chapter."),
        "current_memory": read_chapter_memory(
            data_dir, current_id, current_body,
            legacy_summary=current_legacy,
        ),
        "memory_statuses": states,
        "_memory_entries": entries,
        "_base_upstream": "",
        "_base_downstream": "",
        "context_manifest": {
            "chapter_id": current_id,
            "selected_memory_ids": selected,
            "omitted_memory_ids": irrelevant_memory_ids,
            "omission_reasons": {str(memory_id): "not_relevant_to_current_chapter" for memory_id in irrelevant_memory_ids},
            "missing_or_stale_chapters": missing,
            "estimated_prompt_size": 0,
            "prompt_characters": 0,
            "prompt_hash": "",
        },
    }


def estimate_prompt_tokens(prompt):
    """Conservative local estimate; it is not a tokenizer or model-window guarantee."""
    value = str(prompt or "")
    wide = sum("\u3400" <= char <= "\u9fff" for char in value)
    return math.ceil(wide + (len(value) - wide) / 3)


def fit_context_sandwich_to_prompt(sandwich, prompt_builder, max_estimated_tokens=DEFAULT_PROMPT_TOKEN_BUDGET):
    """Fit optional chapter-context entries against the rendered full generation prompt."""
    context = dict(sandwich)
    entries = list(context.get("_memory_entries", []))
    included = list(entries)
    base_upstream = context.get("_base_upstream", "")
    base_downstream = context.get("_base_downstream", "")
    manifest = dict(context.get("context_manifest", {}))

    def render():
        context["upstream"] = "\n".join([base_upstream] + [entry["text"] for entry in included if entry["side"] == "upstream"]).strip()
        context["downstream"] = "\n".join([base_downstream] + [entry["text"] for entry in included if entry["side"] == "downstream"]).strip()
        prompt = prompt_builder(context)
        return prompt, estimate_prompt_tokens(prompt)

    prompt, estimate = render()
    omitted = []
    for entry in sorted((e for e in included if not e.get("confirmed")), key=lambda e: (e.get("priority", 0), str(e.get("chapter_id", "")))):
        if estimate <= max_estimated_tokens:
            break
        included.remove(entry)
        omitted.append(entry)
        prompt, estimate = render()
    if estimate > max_estimated_tokens:
        raise ContextBudgetError("The required writing instructions exceed the safe prompt budget.")
    for entry in omitted:
        if entry.get("memory_id") is not None:
            manifest.setdefault("omitted_memory_ids", []).append(entry["memory_id"])
            manifest.setdefault("omission_reasons", {})[str(entry["memory_id"])] = "prompt_budget"
        else:
            kind = "summary" if entry.get("is_summary") else "excerpt"
            manifest.setdefault("omission_reasons", {})[f"{entry.get('chapter_id')}:{kind}"] = "prompt_budget"
    manifest["selected_memory_ids"] = [entry["memory_id"] for entry in included if entry.get("memory_id") is not None]
    manifest["omitted_memory_ids"] = list(dict.fromkeys(manifest.get("omitted_memory_ids", [])))
    selected_chapters = {}
    omitted_chapters = {}
    for entry in included:
        selected_chapters.setdefault(entry.get("chapter_id"), entry.get("chapter_title", ""))
    for entry in omitted:
        omitted_chapters.setdefault(entry.get("chapter_id"), entry.get("chapter_title", ""))
    manifest["selected_chapters"] = [
        {"chapter_id": chapter_id, "title": title}
        for chapter_id, title in selected_chapters.items()
    ]
    manifest["omitted_chapters"] = [
        {"chapter_id": chapter_id, "title": title}
        for chapter_id, title in omitted_chapters.items()
    ]
    context["confirmed_memory_items"] = [
        {
            "id": entry["memory_id"],
            "statement": entry.get("statement", ""),
            "source_quote": entry.get("source_quote", ""),
            "confirmed_for_writing": True,
        }
        for entry in included if entry.get("confirmed") and entry.get("memory_id") is not None
    ]
    manifest["estimated_prompt_size"] = estimate
    manifest["prompt_characters"] = len(prompt)
    manifest["prompt_hash"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    context["context_manifest"] = manifest
    return context


def save_manifest_for_prompt(sandwich, prompt):
    context = dict(sandwich)
    manifest = dict(context.get("context_manifest", {}))
    manifest["prompt_hash"] = hashlib.sha256(str(prompt).encode("utf-8")).hexdigest()
    manifest["prompt_characters"] = len(str(prompt))
    manifest["estimated_prompt_size"] = estimate_prompt_tokens(prompt)
    context["context_manifest"] = manifest
    return context["context_manifest"]
