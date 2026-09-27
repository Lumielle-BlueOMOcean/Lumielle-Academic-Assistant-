"""Full-document literature extraction, synthesis, and chapter-bound evidence retrieval."""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

from document_support import DEFAULT_CHUNK_CHARS, chunk_document_text
from writing_support import LLMOutputError, require_valid_llm_output


MAX_CHUNK_ATTEMPTS = 2
MAX_SYNTHESIS_ATTEMPTS = 2
MAX_EVIDENCE_CHARS = 1800


def _json_object(response):
    text = str(response or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def build_chunk_extraction_prompt(title, chunk, index, total, locale="en", retry=False):
    if locale == "zh":
        retry_note = "上一次输出无效。请仅根据本分块重新提取并输出合法 JSON。\n" if retry else ""
        return (
            "你是严谨的学术文献分析器。只提取本分块明确陈述的内容，不推测缺失结论，也不补全其他分块的信息。\n"
            f"文献标题：{title}\n本分块：{index}/{total}\n"
            "提取研究问题、理论、方法、样本、数据、结果、明确主张、局限、结论和重要定义。每项主张尽可能附带原文短引句，不能编造定位。只输出 JSON 对象，键包括 research_question、theory、method、sample、data、results、claims、limitations、conclusion、definitions。claims 为含 section、claim、evidence_text 的数组。\n"
            + retry_note + f"[SOURCE CHUNK {index}/{total}]\n{chunk}\n[/SOURCE CHUNK]"
        )
    retry_note = "The previous output was invalid. Re-extract only from this chunk and return valid JSON.\n" if retry else ""
    return (
        "You are a careful academic literature analyst. Extract only information explicitly stated in this source chunk. Do not infer missing conclusions or complete material from other chunks.\n"
        f"Title: {title}\nSource chunk: {index}/{total}\n"
        "Extract the research question, theory, method, sample, data, results, explicit claims, limitations, conclusion, and important definitions. Attach a short exact source quotation to each claim when possible. Do not invent page locators. Return one JSON object with research_question, theory, method, sample, data, results, claims, limitations, conclusion, definitions. claims is an array of {section, claim, evidence_text}.\n"
        + retry_note + f"[SOURCE CHUNK {index}/{total}]\n{chunk}\n[/SOURCE CHUNK]"
    )


def build_synthesis_prompt(title, extractions, locale="en", retry=False):
    ordered = "\n\n".join(
        f"[CHUNK EXTRACTION {index}/{len(extractions)}]\n{json.dumps(item, ensure_ascii=False)}"
        for index, item in enumerate(extractions, 1)
    )
    if locale == "zh":
        retry_note = "上次输出无效，请只输出符合字段要求的 JSON 对象。\n" if retry else ""
        return (
            "[DOCUMENT SYNTHESIS]\n"
            "根据以下完整文献的有序分块提取结果形成整篇文献档案。合并重复内容；若源文献不同部分存在矛盾，应保留并标明矛盾，不要自行裁决；不得编造。评级与分类必须依据全部分块。\n"
            f"文献标题：{title}\n" + retry_note
            + "仅输出 JSON 对象，字段：rating(1-5整数)、category、research_question、methods、sample、key_findings、limitations、quality_assessment、summary。\n"
            "[ORDERED CHUNK EXTRACTIONS]\n" + ordered
        )
    retry_note = "The previous output was invalid. Return only a valid JSON object with all required fields.\n" if retry else ""
    return (
        "[DOCUMENT SYNTHESIS]\n"
        "Build one profile for the complete literature document from all ordered chunk extractions below. Merge duplicates, preserve conflicts present in the source instead of resolving them, and do not fabricate. Rating and category must reflect the whole document.\n"
        f"Title: {title}\n" + retry_note
        + "Return only a JSON object with rating (integer 1-5), category, research_question, methods, sample, key_findings, limitations, quality_assessment, and summary.\n"
        "[ORDERED CHUNK EXTRACTIONS]\n" + ordered
    )


def _checked_call(llm_call, prompt):
    try:
        return require_valid_llm_output(llm_call(prompt, max_tokens=1400)).strip()
    except LLMOutputError:
        raise
    except Exception as exc:
        raise LLMOutputError(str(exc)) from exc


def _call_json_with_retry(llm_call, prompt_factory, attempts=2):
    last_error = None
    for attempt in range(attempts):
        try:
            response = _checked_call(llm_call, prompt_factory(attempt == 1))
            parsed = _json_object(response)
            if parsed is not None:
                return parsed
            last_error = LLMOutputError("The model returned invalid JSON.")
        except LLMOutputError as exc:
            last_error = exc
    raise last_error or LLMOutputError("The model returned no usable literature analysis.")


def _normalize_chunk_extraction(value):
    def field(name):
        result = value.get(name, "")
        if isinstance(result, list):
            return "; ".join(str(part).strip() for part in result if str(part).strip())
        return str(result or "").strip()

    claims = value.get("claims", [])
    if not isinstance(claims, list):
        claims = []
    return {
        "research_question": field("research_question"),
        "theory": field("theory"),
        "method": field("method"),
        "sample": field("sample"),
        "data": field("data"),
        "results": field("results"),
        "claims": [item for item in claims if isinstance(item, dict)],
        "limitations": field("limitations"),
        "conclusion": field("conclusion"),
        "definitions": field("definitions"),
    }


def _normalize_profile(value):
    try:
        rating = int(value.get("rating"))
    except (TypeError, ValueError):
        return None
    category = value.get("category")
    if isinstance(value.get("rating"), bool) or not 1 <= rating <= 5 or not isinstance(category, str) or not category.strip():
        return None
    fields = ("research_question", "methods", "sample", "key_findings", "limitations", "quality_assessment", "summary")
    profile = {key: str(value.get(key, "") or "").strip() for key in fields}
    profile.update({"rating": rating, "category": category.strip(), "analysis_status": "ok"})
    return profile


def analyze_literature_document(document_text, title, llm_call, *, locale="en", max_chars=DEFAULT_CHUNK_CHARS):
    """Analyze every ordered chunk, then synthesize a profile and provenance-bearing evidence."""
    if not isinstance(document_text, str) or not document_text.strip():
        return {"status": "error", "profile": None, "evidence": [], "chunks_total": 0, "failed_chunk": 0, "message": "No extractable text was provided."}
    chunks = chunk_document_text(document_text, max_chars=max_chars)
    extractions = []
    evidence = []
    for index, chunk in enumerate(chunks, 1):
        try:
            parsed = _call_json_with_retry(
                llm_call,
                lambda retry, chunk=chunk, index=index: build_chunk_extraction_prompt(title, chunk, index, len(chunks), locale, retry),
                MAX_CHUNK_ATTEMPTS,
            )
        except LLMOutputError as exc:
            return {"status": "error", "profile": None, "evidence": [], "chunks_total": len(chunks), "failed_chunk": index, "message": f"Literature chunk {index} could not be analyzed after one retry: {exc}"}
        extraction = _normalize_chunk_extraction(parsed)
        extractions.append(extraction)
        for claim in extraction["claims"]:
            claim_text = str(claim.get("claim", "") or "").strip()
            quote = str(claim.get("evidence_text", "") or "").strip()
            # Store only quotations that can be located verbatim in this source chunk.
            if not claim_text or not quote or quote not in chunk:
                continue
            evidence.append({
                "literature_id": "",
                "chunk_index": index,
                "section": str(claim.get("section", "") or "").strip(),
                "claim": claim_text[:500],
                "evidence_text": quote[:MAX_EVIDENCE_CHARS],
                "source_locator": f"chunk {index}",
            })
    try:
        profile_raw = _call_json_with_retry(
            llm_call,
            lambda retry: build_synthesis_prompt(title, extractions, locale, retry),
            MAX_SYNTHESIS_ATTEMPTS,
        )
    except LLMOutputError as exc:
        return {"status": "error", "profile": None, "evidence": [], "chunks_total": len(chunks), "failed_chunk": "synthesis", "message": f"The complete literature profile could not be synthesized: {exc}"}
    profile = _normalize_profile(profile_raw)
    if profile is None:
        return {"status": "error", "profile": None, "evidence": [], "chunks_total": len(chunks), "failed_chunk": "synthesis", "message": "The complete literature profile was invalid; prior analysis was kept."}
    return {"status": "ok", "profile": profile, "evidence": evidence, "chunks_total": len(chunks), "failed_chunk": 0, "message": ""}


def update_evidence_store_after_success(evidence_store, literature_id, result):
    """Copy-on-success evidence replacement; failures leave the previous store byte-for-byte equivalent."""
    if not isinstance(result, dict) or result.get("status") != "ok" or not isinstance(result.get("evidence"), list):
        return evidence_store if isinstance(evidence_store, dict) else {}
    updated = dict(evidence_store) if isinstance(evidence_store, dict) else {}
    updated[str(literature_id)] = [dict(item, literature_id=str(literature_id)) for item in result["evidence"]]
    return updated


def apply_analysis_to_literature_record(record, evidence_store, result):
    """Update an existing literature profile and its evidence together, on complete success only."""
    if not isinstance(record, dict) or not isinstance(result, dict) or result.get("status") != "ok":
        return record, evidence_store if isinstance(evidence_store, dict) else {}, False
    literature_id = str(record.get("id", ""))
    profile = result.get("profile")
    if not literature_id or not isinstance(profile, dict):
        return record, evidence_store if isinstance(evidence_store, dict) else {}, False
    updated_record = dict(record)
    updated_record["analysis"] = dict(profile)
    updated_record["rating"] = int(profile["rating"])
    updated_record["category"] = str(profile["category"])
    updated_record["summary"] = str(profile.get("summary", ""))
    updated_record["analysis_status"] = "ok"
    updated_record["evidence_count"] = len(result.get("evidence", []))
    updated_store = update_evidence_store_after_success(evidence_store, literature_id, result)
    return updated_record, updated_store, True


def save_literature_source(raw_dir, literature_id, filename, raw_bytes):
    """Save an imported original under RAW_DIR using only a sanitized basename and supported suffix."""
    root = Path(raw_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    safe_input = str(filename or "source.txt").replace("\\", "/")
    original_name = Path(safe_input).name
    suffix = Path(original_name).suffix.casefold()
    if suffix not in {".pdf", ".docx", ".txt"}:
        suffix = ".txt"
    stem = Path(original_name).stem or "source"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")[:100] or "source"
    target = (root / f"{literature_id}_{stem}{suffix}").resolve()
    if target.parent != root:
        raise ValueError("Literature source path escaped RAW_DIR.")
    target.write_bytes(raw_bytes)
    return str(target)


def new_literature_record(literature_id, title, text, *, source, link="", file_path=""):
    """Create a readable legacy-compatible library record before optional AI analysis."""
    return {
        "id": str(literature_id or uuid.uuid4()),
        "title": str(title or "Untitled literature"),
        "summary": str(text or "")[:500] + ("..." if len(str(text or "")) > 500 else ""),
        "source": str(source or ""),
        "link": str(link or ""),
        "file_path": str(file_path or ""),
        "important": False,
        "rating": 0,
        "category": "Unrated",
        "analysis_status": "unavailable",
        "analysis": {"analysis_status": "unavailable"},
    }


def select_literature_evidence_for_chapter(evidence_store, bound_literature_ids, chapter_title, chapter_description="", *, limit=8):
    """Select concise evidence only from references explicitly bound to this chapter."""
    if not isinstance(evidence_store, dict):
        return []
    bound = {str(value) for value in (bound_literature_ids or [])}
    query_terms = set(re.findall(r"[a-z0-9]{2,}", f"{chapter_title or ''} {chapter_description or ''}".casefold()))
    candidates = []
    for literature_id in bound:
        for item in evidence_store.get(literature_id, []) or []:
            if not isinstance(item, dict):
                continue
            terms = set(re.findall(r"[a-z0-9]{2,}", f"{item.get('claim', '')} {item.get('section', '')} {item.get('evidence_text', '')}".casefold()))
            score = len(query_terms & terms)
            if not query_terms or score:
                candidates.append((score, item))
    candidates.sort(key=lambda row: (-row[0], str(row[1].get("literature_id", "")), int(row[1].get("chunk_index", 0))))
    selected = []
    for _, item in candidates[:max(0, int(limit))]:
        selected.append({key: item.get(key, "") for key in ("literature_id", "chunk_index", "section", "claim", "evidence_text", "source_locator")})
    return selected


def format_literature_evidence_for_chapter(evidence_store, bound_literature_ids, chapter_title, chapter_description="", *, locale="en", limit=8, literatures=None):
    selected = select_literature_evidence_for_chapter(
        evidence_store, bound_literature_ids, chapter_title, chapter_description, limit=limit
    )
    if not selected:
        return "本章没有可用的已绑定文献证据。" if locale == "zh" else "No evidence chunks are available for the references bound to this chapter."
    title_by_id = {
        str(item.get("id")): str(item.get("title", item.get("id", "")))
        for item in (literatures or []) if isinstance(item, dict) and item.get("id") is not None
    }
    citation_number = {str(item): index for index, item in enumerate(dict.fromkeys(str(value) for value in (bound_literature_ids or [])), 1)}
    lines = []
    for item in selected:
        literature_id = str(item["literature_id"])
        title = title_by_id.get(literature_id, literature_id)
        lines.append(
            f"- [{citation_number.get(literature_id, '?')}] {title}: {item.get('claim', '')} "
            f"| {item.get('evidence_text', '')} | {item.get('source_locator', '')}"
        )
    return "\n".join(lines)


def legacy_profile_for_display(literature):
    """Display old single-analysis records without forcing a data migration."""
    if not isinstance(literature, dict):
        return {}
    analysis = literature.get("analysis")
    return dict(analysis) if isinstance(analysis, dict) else {}
