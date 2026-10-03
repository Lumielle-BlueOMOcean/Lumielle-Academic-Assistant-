"""Full-document literature extraction, synthesis, and chapter-bound evidence retrieval."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

from context_digest_support import balanced_excerpt
from document_support import DEFAULT_CHUNK_CHARS, chunk_document_text
from relevance_support import multilingual_terms
from writing_support import LLMOutputError, require_valid_llm_output
from structured_output_support import classify_provider_exception, parse_first_json_value


MAX_CHUNK_ATTEMPTS = 2
MAX_SYNTHESIS_ATTEMPTS = 2
MAX_EVIDENCE_CHARS = 1800
MAX_TRANSPORT_ATTEMPTS = 3
TRANSPORT_RETRY_BACKOFF_SECONDS = (1, 2)
LITERATURE_CHUNK_TOKEN_BUDGETS = (2200, 4000)
LITERATURE_SYNTHESIS_TOKEN_BUDGETS = (2600, 4000)
TRANSIENT_PROVIDER_ERRORS = frozenset({"rate_limited", "timeout", "connection_error", "server_error"})
TRANSIENT_FINISH_REASONS = frozenset({"insufficient_system_resource", "aborted"})


def _json_object(response):
    value = parse_first_json_value(response)
    return value if isinstance(value, dict) else None


class _StructuredCallFailure(LLMOutputError):
    def __init__(self, failure_stage, failure_code=None, diagnostic=None):
        super().__init__(failure_stage)
        self.failure_stage = failure_stage
        self.failure_code = failure_code or failure_stage
        self.diagnostic = diagnostic if isinstance(diagnostic, dict) else {}


def build_chunk_extraction_prompt(title, chunk, index, total, locale="en", retry=False):
    if locale == "zh":
        retry_note = "上一次输出无效。请仅根据本分块重新提取并输出合法 JSON。\n" if retry else ""
        return (
            "你是严谨的学术文献分析器。只提取本分块明确陈述的内容，不推测缺失结论，也不补全其他分块的信息。\n"
            f"文献标题：{title}\n本分块：{index}/{total}\n"
            "尽可能提取本分块实际支持的研究问题、理论、方法、样本、数据、结果、明确主张、局限、结论和重要定义。缺少的文字字段使用空字符串，没有主张使用空数组。每项主张尽可能附带原文短引句；evidence_text 必须是原文连续短引文。不得编造事实或引文。严格返回以下完整 JSON 形状：\n"
            '{"research_question":"","theory":"","method":"","sample":"","data":"","results":"","claims":[{"section":"","claim":"","evidence_text":""}],"limitations":"","conclusion":"","definitions":""}\n'
            + retry_note + f"[SOURCE CHUNK {index}/{total}]\n{chunk}\n[/SOURCE CHUNK]"
        )
    retry_note = "The previous output was invalid. Re-extract only from this chunk and return valid JSON.\n" if retry else ""
    return (
        "You are a careful academic literature analyst. Extract only information explicitly stated in this source chunk. Do not infer missing conclusions or complete material from other chunks.\n"
        f"Title: {title}\nSource chunk: {index}/{total}\n"
        "Extract only the academic dimensions supported by this chunk. Use an empty string for text fields with no support and an empty array when there are no claims. Any evidence_text must be a short exact quotation copied from this source chunk. Do not invent facts or quotations. Return exactly this complete JSON shape:\n"
        '{"research_question":"","theory":"","method":"","sample":"","data":"","results":"","claims":[{"section":"","claim":"","evidence_text":""}],"limitations":"","conclusion":"","definitions":""}\n'
        + retry_note + f"[SOURCE CHUNK {index}/{total}]\n{chunk}\n[/SOURCE CHUNK]"
    )


def build_synthesis_prompt(title, extractions, locale="en", retry=False, *, research_topic="", research_context=""):
    ordered = "\n\n".join(
        f"[CHUNK EXTRACTION {index}/{len(extractions)}]\n{json.dumps(item, ensure_ascii=False)}"
        for index, item in enumerate(extractions, 1)
    )
    if locale == "zh":
        retry_note = "上次输出无效，请只输出合法 JSON；rating 必须为 1–5 整数，没有支持内容的字段使用空字符串。\n" if retry else ""
        return (
            "[DOCUMENT SYNTHESIS]\n"
            "根据以下完整文献的有序分块提取结果形成整篇文献档案。合并重复内容；若源文献不同部分存在矛盾，应保留并标明矛盾，不要自行裁决；不得编造。\n"
            "rating 仅表示该文献与当前研究项目的相关性和实用性；quality_assessment 单独评估严谨性、证据强度、局限，以及原文支持的时效性，不得将两者混为一项。\n"
            f"文献标题：{title}\n" + retry_note
            + "仅输出完整 JSON 对象。rating 为 1–5 整数，category 缺失时使用 Other；没有材料支持的文字字段使用空字符串，不能编造，至少一个学术分析字段必须有实际内容。严格采用此形状：\n"
            + '{"rating":4,"category":"Other","research_question":"","methods":"","sample":"","key_findings":"","limitations":"","quality_assessment":"","relevance_reason":"","summary":""}\n'
            + f"[GLOBAL RESEARCH TOPIC]\n{research_topic}\n\n[RESEARCH PLANNING CONTEXT]\n{research_context}\n\n"
            "[ORDERED CHUNK EXTRACTIONS]\n" + ordered
        )
    retry_note = "The previous output was invalid. Return valid JSON; rating must be an integer from 1 to 5, and unsupported text fields must be empty strings.\n" if retry else ""
    return (
        "[DOCUMENT SYNTHESIS]\n"
        "Build one profile for the complete literature document from all ordered chunk extractions below. Merge duplicates, preserve conflicts present in the source instead of resolving them, and do not fabricate.\n"
        "The rating measures relevance and usefulness to the current research project. Assess intrinsic quality separately in quality_assessment: rigor, evidence strength, limitations, and timeliness only when supported by the source.\n"
        f"Title: {title}\n" + retry_note
        + "Return one complete JSON object. rating must be an integer from 1 to 5; use category Other if absent. Use empty strings for unsupported text fields, do not invent content, and ensure at least one academic analysis field contains meaningful content. Use exactly this shape:\n"
        + '{"rating":4,"category":"Other","research_question":"","methods":"","sample":"","key_findings":"","limitations":"","quality_assessment":"","relevance_reason":"","summary":""}\n'
        + f"[GLOBAL RESEARCH TOPIC]\n{research_topic}\n\n[RESEARCH PLANNING CONTEXT]\n{research_context}\n\n"
        "[ORDERED CHUNK EXTRACTIONS]\n" + ordered
    )


_PROVIDER_ERROR_CODES = TRANSIENT_PROVIDER_ERRORS | frozenset({
    "authentication_failed", "insufficient_balance", "model_not_available",
    "invalid_request", "provider_error", "incomplete_response", "content_filtered",
    "insufficient_system_resource", "aborted",
})
_SAFE_FINISH_REASONS = frozenset({
    "stop", "length", "content_filter", "insufficient_system_resource", "aborted",
})


def _safe_finish_reason(value):
    if not isinstance(value, str):
        return None
    return value if value in _SAFE_FINISH_REASONS else "unknown"


def _coerce_structured_result(value):
    if isinstance(value, str):
        if not value.strip():
            return {
                "content": "", "finish_reason": "stop", "structured_mode": "legacy",
                "http_status": None, "error_code": None,
            }
        try:
            content = require_valid_llm_output(value).strip()
        except LLMOutputError:
            raise _StructuredCallFailure("provider_call", "provider_error") from None
        return {
            "content": content, "finish_reason": "stop", "structured_mode": "legacy",
            "http_status": None, "error_code": None,
        }
    if not isinstance(value, dict) or "content" not in value:
        raise _StructuredCallFailure("provider_call", "incomplete_response")
    content = value.get("content")
    mode = value.get("structured_mode")
    status = value.get("http_status")
    error_code = value.get("error_code")
    return {
        "content": content if isinstance(content, str) else "",
        "finish_reason": _safe_finish_reason(value.get("finish_reason")),
        "structured_mode": mode if isinstance(mode, str) and mode in {"native", "compatibility", "legacy"} else "unknown",
        "http_status": status if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599 else None,
        "error_code": error_code if isinstance(error_code, str) and error_code in _PROVIDER_ERROR_CODES else ("provider_error" if error_code else None),
    }


def _safe_exception_status(exc):
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599 else None


def _checked_call(llm_call, prompt, max_tokens):
    try:
        value = llm_call(prompt, max_tokens=max_tokens, json_mode=True, return_metadata=True)
    except Exception as exc:
        diagnostic = {
            "finish_reason": None,
            "http_status": _safe_exception_status(exc),
            "response_chars": 0,
            "structured_mode": "unknown",
        }
        raise _StructuredCallFailure("provider_call", classify_provider_exception(exc), diagnostic) from None
    try:
        return _coerce_structured_result(value)
    except _StructuredCallFailure as exc:
        if exc.diagnostic:
            raise
        diagnostic = {
            "finish_reason": None,
            "http_status": None,
            "response_chars": 0,
            "structured_mode": "unknown",
        }
        raise _StructuredCallFailure(exc.failure_stage, exc.failure_code, diagnostic) from None


def _retryable_provider_error(code):
    return code in TRANSIENT_PROVIDER_ERRORS or code in TRANSIENT_FINISH_REASONS


def _call_with_transport_retry(llm_call, prompt, max_tokens):
    retry_count = 0
    for transport_attempt in range(MAX_TRANSPORT_ATTEMPTS):
        try:
            result = _checked_call(llm_call, prompt, max_tokens)
        except _StructuredCallFailure as exc:
            code = exc.failure_code or "provider_error"
            diagnostic = dict(exc.diagnostic)
            if _retryable_provider_error(code) and transport_attempt < MAX_TRANSPORT_ATTEMPTS - 1:
                time.sleep(TRANSPORT_RETRY_BACKOFF_SECONDS[transport_attempt])
                retry_count += 1
                continue
            diagnostic["retry_count"] = retry_count
            diagnostic["failure_code"] = code
            raise _StructuredCallFailure("provider_call", code, diagnostic) from None

        code = result.get("error_code")
        finish_reason = result.get("finish_reason")
        transient_code = finish_reason if finish_reason in TRANSIENT_FINISH_REASONS else code
        if transient_code:
            if _retryable_provider_error(transient_code) and transport_attempt < MAX_TRANSPORT_ATTEMPTS - 1:
                time.sleep(TRANSPORT_RETRY_BACKOFF_SECONDS[transport_attempt])
                retry_count += 1
                continue
            diagnostic = _response_diagnostic(result, retry_count=retry_count)
            diagnostic["failure_code"] = transient_code
            raise _StructuredCallFailure("provider_call", transient_code, diagnostic)
        return result, retry_count
    raise _StructuredCallFailure("provider_call", "provider_error", {"retry_count": retry_count})


def _safe_top_level_shape(value):
    if not isinstance(value, dict):
        return {"_top_level": "list" if isinstance(value, list) else type(value).__name__[:24]}
    shape = {}
    allowed_keys = {
        alias.casefold()
        for aliases in _CHUNK_FIELD_ALIASES.values()
        for alias in aliases
    } | {"claims", "rating", "category", "type", "literature_type"}
    for key, item in list(value.items())[:24]:
        if not isinstance(key, str) or key.casefold() not in allowed_keys:
            continue
        if item is None:
            kind = "null"
        elif isinstance(item, bool):
            kind = "bool"
        elif isinstance(item, str):
            kind = "str"
        elif isinstance(item, dict):
            kind = "dict"
        elif isinstance(item, list):
            kind = "list"
        elif isinstance(item, (int, float)):
            kind = "number"
        else:
            kind = "other"
        shape[key] = kind
    return shape


def _response_diagnostic(result, *, retry_count=0, top_level_shape=None):
    return {
        "finish_reason": result.get("finish_reason"),
        "http_status": result.get("http_status"),
        "response_chars": len(result.get("content", "")),
        "structured_mode": result.get("structured_mode", "unknown"),
        "retry_count": retry_count,
        "top_level_shape": top_level_shape if top_level_shape is not None else {},
    }


def _call_json_with_retry(llm_call, prompt_factory, attempts=2, validator=None, *, token_budgets=(2200, 4000)):
    business_attempts = max(1, min(2, int(attempts)))
    budgets = tuple(max(1, int(value)) for value in token_budgets) or (2200, 4000)
    last_stage = "invalid_structured_output"
    last_diagnostic = {}
    for attempt in range(business_attempts):
        try:
            result, transport_retries = _call_with_transport_retry(
                llm_call, prompt_factory(attempt > 0), budgets[min(attempt, len(budgets) - 1)],
            )
        except _StructuredCallFailure:
            # Provider/transport failures already consumed their bounded retries.
            # Do not add a schema-correction call on top of a failed provider call.
            raise

        content = result["content"]
        finish_reason = result.get("finish_reason")
        if finish_reason == "content_filter":
            diagnostic = _response_diagnostic(result, retry_count=attempt + transport_retries)
            diagnostic["failure_code"] = "content_filtered"
            raise _StructuredCallFailure("content_filtered", "content_filtered", diagnostic)
        if finish_reason == "length":
            last_stage = "truncated_output"
            last_diagnostic = _response_diagnostic(result, retry_count=attempt + transport_retries)
            last_diagnostic["failure_code"] = last_stage
            if attempt + 1 < business_attempts:
                continue
            break
        if finish_reason != "stop":
            last_stage = "incomplete_response"
            last_diagnostic = _response_diagnostic(result, retry_count=attempt + transport_retries)
            last_diagnostic["failure_code"] = last_stage
            raise _StructuredCallFailure(last_stage, last_stage, last_diagnostic)
        if not content.strip():
            last_stage = "empty_structured_output"
            last_diagnostic = _response_diagnostic(result, retry_count=attempt + transport_retries)
            last_diagnostic["failure_code"] = last_stage
            continue

        parsed = parse_first_json_value(content)
        if parsed is None or not isinstance(parsed, dict):
            last_stage = "invalid_structured_output"
            last_diagnostic = _response_diagnostic(result, retry_count=attempt + transport_retries)
            last_diagnostic["top_level_shape"] = _safe_top_level_shape(parsed) if parsed is not None else {}
            last_diagnostic["failure_code"] = last_stage
            continue
        if not parsed:
            last_stage = "empty_structured_output"
            last_diagnostic = _response_diagnostic(result, retry_count=attempt + transport_retries, top_level_shape={})
            last_diagnostic["failure_code"] = last_stage
            continue
        try:
            validated = validator(parsed) if validator is not None else parsed
        except Exception:
            validated = None
        if validated is None:
            last_stage = "schema_validation_failure"
            last_diagnostic = _response_diagnostic(
                result, retry_count=attempt + transport_retries, top_level_shape=_safe_top_level_shape(parsed),
            )
            last_diagnostic["failure_code"] = last_stage
            continue
        return validated
    raise _StructuredCallFailure(last_stage, last_stage, last_diagnostic)


_CHUNK_TEXT_FIELDS = (
    "research_question", "theory", "method", "sample", "data", "results",
    "limitations", "conclusion", "definitions",
)
_CHUNK_FIELD_ALIASES = {
    "research_question": ("research_question", "research_questions", "question", "questions"),
    "theory": ("theory", "theoretical_framework", "framework"),
    "method": ("method", "methods", "methodology"),
    "sample": ("sample", "participants", "subjects"),
    "data": ("data", "dataset", "datasets", "data_source", "data_sources"),
    "results": ("results", "findings", "key_findings"),
    "limitations": ("limitations", "limitation"),
    "conclusion": ("conclusion", "conclusions"),
    "definitions": ("definitions", "definition", "key_definitions"),
}
_PROFILE_TEXT_FIELDS = (
    "research_question", "methods", "sample", "key_findings", "limitations",
    "quality_assessment", "relevance_reason", "summary",
)
_PROFILE_FIELD_ALIASES = {
    "research_question": ("research_question", "research_questions", "question", "questions"),
    "methods": ("methods", "method", "methodology"),
    "sample": ("sample", "participants", "subjects"),
    "key_findings": ("key_findings", "findings", "results"),
    "limitations": ("limitations", "limitation"),
    "quality_assessment": ("quality_assessment", "quality", "quality_evaluation", "assessment"),
    "relevance_reason": ("relevance_reason", "relevance", "relevance_to_topic"),
    "summary": ("summary", "abstract", "overview"),
}
_MAX_NORMALIZED_FIELD_CHARS = 6000
_MAX_NORMALIZED_DEPTH = 6
_MAX_NORMALIZED_CLAIMS = 200


def _normalize_chunk_text(value, depth=0):
    if depth > _MAX_NORMALIZED_DEPTH:
        return None
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()[:_MAX_NORMALIZED_FIELD_CHARS]
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            text = _normalize_chunk_text(item, depth + 1)
            if text is None:
                return None
            if text:
                parts.append(text)
        return "; ".join(parts)[:_MAX_NORMALIZED_FIELD_CHARS]
    if isinstance(value, dict):
        parts = []
        keys = sorted(value, key=lambda item: (str(item).casefold(), str(item)))
        for key in keys:
            label = str(key).strip()[:80]
            if not label:
                continue
            text = _normalize_chunk_text(value[key], depth + 1)
            if text is None:
                return None
            if text:
                parts.append(f"{label}: {text}")
        return "; ".join(parts)[:_MAX_NORMALIZED_FIELD_CHARS]
    return None


def _first_text_alias(value, aliases):
    for alias in aliases:
        if alias not in value:
            continue
        text = _normalize_chunk_text(value[alias])
        if text is None:
            return None
        if text:
            return text
    return ""


def normalize_chunk_extraction(value):
    """Normalize optional per-chunk dimensions while requiring meaningful content."""
    if not isinstance(value, dict):
        return None
    normalized = {}
    for name in _CHUNK_TEXT_FIELDS:
        text = _first_text_alias(value, _CHUNK_FIELD_ALIASES[name])
        if text is None:
            return None
        normalized[name] = text

    raw_claims = value.get("claims")
    if raw_claims is None:
        raw_claims = []
    if isinstance(raw_claims, str):
        raw_claims = [raw_claims]
    elif isinstance(raw_claims, dict):
        raw_claims = [raw_claims]
    if not isinstance(raw_claims, list):
        return None
    if len(raw_claims) > _MAX_NORMALIZED_CLAIMS:
        return None
    claims = []
    for claim in raw_claims:
        if isinstance(claim, str):
            item = {"section": "", "claim": claim.strip(), "evidence_text": ""}
        elif isinstance(claim, dict):
            item = {}
            aliases = {
                "section": ("section", "dimension", "type"),
                "claim": ("claim", "statement", "assertion", "content", "text"),
                "evidence_text": ("evidence_text", "evidence", "quote", "quotation", "source_quote"),
            }
            for name, fields in aliases.items():
                text = _first_text_alias(claim, fields)
                if text is None:
                    return None
                item[name] = text
        else:
            return None
        if item["claim"]:
            claims.append(item)
    normalized["claims"] = claims

    if not any(normalized[name] for name in _CHUNK_TEXT_FIELDS) and not claims:
        return None
    return normalized


def _validate_chunk_extraction(value):
    return normalize_chunk_extraction(value)


def normalize_profile(value):
    """Normalize a complete-document profile without inventing academic content."""
    if not isinstance(value, dict):
        return None
    raw_rating = value.get("rating")
    if isinstance(raw_rating, bool):
        return None
    if isinstance(raw_rating, int):
        rating = raw_rating
    elif isinstance(raw_rating, str) and re.fullmatch(r"[1-5]", raw_rating.strip()):
        rating = int(raw_rating.strip())
    else:
        return None
    if not 1 <= rating <= 5:
        return None

    category = "Other"
    for alias in ("category", "type", "literature_type"):
        if alias not in value or value[alias] is None:
            continue
        raw_category = value[alias]
        if not isinstance(raw_category, str):
            return None
        category = raw_category.strip() or "Other"
        break
    profile = {}
    for key in _PROFILE_TEXT_FIELDS:
        field = _first_text_alias(value, _PROFILE_FIELD_ALIASES[key])
        if field is None:
            return None
        profile[key] = field
    if not any(profile.values()):
        return None
    profile.update({"rating": rating, "category": category, "analysis_status": "ok"})
    return profile


def analyze_literature_document(document_text, title, llm_call, *, locale="en", max_chars=DEFAULT_CHUNK_CHARS, research_topic="", research_context=""):
    """Analyze every ordered chunk, then synthesize a profile and provenance-bearing evidence."""
    if not isinstance(document_text, str) or not document_text.strip():
        return {
            "status": "error", "profile": None, "evidence": [], "chunks_total": 0, "failed_chunk": 0,
            "failure_stage": "empty_source", "failure_code": "empty_source",
            "diagnostic": {
                "stage": "input", "chunk_index": None, "chunks_total": 0,
                "failure_code": "empty_source", "finish_reason": None, "http_status": None,
                "response_chars": 0, "structured_mode": "unknown", "retry_count": 0,
                "top_level_shape": {},
            },
            "message": "No extractable text was provided.",
        }
    chunks = chunk_document_text(document_text, max_chars=max_chars)
    extractions = []
    evidence = []
    for index, chunk in enumerate(chunks, 1):
        try:
            parsed = _call_json_with_retry(
                llm_call,
                lambda retry, chunk=chunk, index=index: build_chunk_extraction_prompt(title, chunk, index, len(chunks), locale, retry),
                MAX_CHUNK_ATTEMPTS,
                validator=_validate_chunk_extraction,
                token_budgets=LITERATURE_CHUNK_TOKEN_BUDGETS,
            )
        except _StructuredCallFailure as exc:
            diagnostic = {
                "stage": "chunk",
                "chunk_index": index,
                "chunks_total": len(chunks),
                "failure_code": exc.failure_code,
                **exc.diagnostic,
            }
            return {
                "status": "error", "profile": None, "evidence": [], "chunks_total": len(chunks),
                "failed_chunk": index, "failure_stage": exc.failure_stage,
                "failure_code": exc.failure_code,
                "diagnostic": diagnostic,
                "message": "A literature source chunk could not be analyzed; no partial analysis was stored.",
            }
        extraction = parsed
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
            lambda retry: build_synthesis_prompt(
                title, extractions, locale, retry,
                research_topic=research_topic, research_context=research_context,
            ),
            MAX_SYNTHESIS_ATTEMPTS,
            validator=normalize_profile,
            token_budgets=LITERATURE_SYNTHESIS_TOKEN_BUDGETS,
        )
    except _StructuredCallFailure as exc:
        diagnostic = {
            "stage": "synthesis",
            "chunk_index": None,
            "chunks_total": len(chunks),
            "failure_code": exc.failure_code,
            **exc.diagnostic,
        }
        return {
            "status": "error", "profile": None, "evidence": [], "chunks_total": len(chunks),
            "failed_chunk": "synthesis", "failure_stage": "document_synthesis",
            "failure_code": exc.failure_code,
            "diagnostic": diagnostic,
            "message": "The complete literature profile could not be synthesized; prior analysis was kept.",
        }
    profile = profile_raw
    return {"status": "ok", "profile": profile, "evidence": evidence, "chunks_total": len(chunks), "failed_chunk": 0, "message": ""}


def build_literature_profile_digest(literatures, max_chars=12000, locale="en"):
    """Create a bounded ordered digest that represents every stored literature profile."""
    records = [item for item in literatures if isinstance(item, dict)] if isinstance(literatures, list) else []
    labels = (
        ("research question", "methods", "sample", "key findings", "limitations", "quality", "relevance")
        if locale != "zh" else ("研究问题", "方法", "样本", "主要发现", "局限", "质量", "相关性")
    )

    def profile_line(record, budget=None):
        analysis = record.get("analysis") if isinstance(record.get("analysis"), dict) else {}
        title = str(record.get("title", "Untitled"))
        rating = record.get("rating", analysis.get("rating", 0))
        category = str(record.get("category", analysis.get("category", "Other")))
        prefix = f"- [{category}][{rating}/5] {title}: "
        fields = (
            analysis.get("research_question", ""), analysis.get("methods", ""),
            analysis.get("sample", ""), analysis.get("key_findings", ""),
            analysis.get("limitations", ""), analysis.get("quality_assessment", ""),
            analysis.get("relevance_reason", ""),
        )
        values = [(label, str(value).strip()) for label, value in zip(labels, fields) if str(value or "").strip()]
        if budget is None:
            body = "; ".join(f"{label}: {value}" for label, value in values)
            return prefix + body
        remaining = max(0, budget - len(prefix))
        if not values:
            return balanced_excerpt(prefix, budget)
        per_field = max(1, remaining // len(values))
        body = "; ".join(f"{label}: {balanced_excerpt(value, per_field)}" for label, value in values)
        line = prefix + body
        return line if len(line) <= budget else balanced_excerpt(line, budget)

    full_lines = [profile_line(record) for record in records]
    full = "\n".join(full_lines)
    budget = max(0, int(max_chars))
    if len(full) <= budget:
        return full
    if not records or budget == 0:
        return ""
    separator_budget = max(0, len(records) - 1)
    per_record = max(0, (budget - separator_budget) // len(records))
    digest = "\n".join(profile_line(record, per_record) for record in records)
    if len(digest) <= budget:
        return digest
    # Preserve records from both ends and the middle if the per-record metadata
    # itself exceeds the budget; never fall back to a head-only prefix.
    return balanced_excerpt(digest, budget)


def build_literature_library_review_prompt(research_topic, research_context, literatures, *, locale="en", max_chars=12000):
    """Assemble the shared, all-profile literature quality/gap-analysis prompt."""
    digest = build_literature_profile_digest(literatures, max_chars=max_chars, locale=locale)
    if locale == "zh":
        instructions = (
            "你是严格的学术评审委员。评估文献库覆盖、均衡性和学术质量，并指出缺失方向与具体检索建议。"
            "相关性评级衡量文献对当前课题的用途；质量评价独立考察严谨性和证据强度。只输出简洁结论。\n"
        )
    else:
        instructions = (
            "You are a rigorous academic reviewer. Assess literature coverage, balance, and scholarly quality; identify missing directions and concrete search suggestions. "
            "Relevance ratings describe usefulness to this project; assess intrinsic quality separately by rigor and evidence strength. Return concise conclusions only.\n"
        )
    return (
        instructions
        + f"[GLOBAL RESEARCH TOPIC]\n{research_topic}\n\n"
        + f"[RESEARCH PLANNING CONTEXT]\n{research_context}\n\n"
        + f"[LITERATURE PROFILE DIGEST]\n{digest}"
    )


def _full_literature_profile_lines(literatures, locale="en"):
    labels = (
        ("research question", "methods", "sample", "key findings", "limitations", "quality", "relevance")
        if locale != "zh" else ("研究问题", "方法", "样本", "主要发现", "局限", "质量", "相关性")
    )
    lines = []
    for record in literatures if isinstance(literatures, list) else []:
        if not isinstance(record, dict):
            continue
        analysis = record.get("analysis") if isinstance(record.get("analysis"), dict) else {}
        title = str(record.get("title", "Untitled"))
        rating = record.get("rating", analysis.get("rating", 0))
        category = str(record.get("category", analysis.get("category", "Other")))
        fields = (
            analysis.get("research_question", ""), analysis.get("methods", ""),
            analysis.get("sample", ""), analysis.get("key_findings", ""),
            analysis.get("limitations", ""), analysis.get("quality_assessment", ""),
            analysis.get("relevance_reason", ""),
        )
        details = "; ".join(
            f"{label}: {str(value).strip()}"
            for label, value in zip(labels, fields) if str(value or "").strip()
        )
        lines.append(f"- [{category}][{rating}/5] {title}: {details}")
    return lines


def build_literature_profile_batches(literatures, max_chars=12000, locale="en"):
    """Split every structured profile into bounded ordered batches; never drop late profiles."""
    budget = max(1, int(max_chars))
    batches = []
    current = []
    current_size = 0
    for line in _full_literature_profile_lines(literatures, locale):
        if len(line) > budget:
            line = balanced_excerpt(line, budget)
        extra = len(line) + (1 if current else 0)
        if current and current_size + extra > budget:
            batches.append("\n".join(current))
            current = []
            current_size = 0
            extra = len(line)
        current.append(line)
        current_size += extra
    if current:
        batches.append("\n".join(current))
    return batches


def build_literature_profile_batch_review_prompt(research_topic, research_context, batch, index, total, *, locale="en"):
    if locale == "zh":
        instructions = (
            "你正在分批审查文献档案。只总结本批覆盖范围、质量差异和明确的研究缺口；不要把本批结论冒充全库结论，"
            "不要编造档案没有提供的信息，并保留重要的不确定性。\n"
        )
    else:
        instructions = (
            "Review this batch of literature profiles only. Summarize its coverage, quality differences, and supported research gaps. "
            "Do not present batch findings as whole-library conclusions or invent details absent from the profiles; preserve uncertainty.\n"
        )
    return (
        instructions
        + f"[GLOBAL RESEARCH TOPIC]\n{research_topic}\n\n"
        + f"[RESEARCH PLANNING CONTEXT]\n{research_context}\n\n"
        + f"[LITERATURE PROFILE BATCH {index}/{total}]\n{batch}"
    )


def build_literature_review_summary_prompt(research_topic, research_context, summaries, *, locale="en"):
    if locale == "zh":
        instructions = (
            "以下是按顺序生成的全库文献档案批次总结。整合覆盖范围、质量差异、共同与相互矛盾的发现、缺口和检索建议；"
            "区分已报告内容与推断，不要编造。相关性针对当前课题，来源质量单独评价。\n"
        )
    else:
        instructions = (
            "Synthesize these ordered batch reviews of the full literature library. Integrate coverage, quality differences, shared or conflicting findings, "
            "gaps, and search suggestions. Distinguish reported content from inference and do not fabricate. Relevance is project-specific; source quality is separate.\n"
        )
    body = "\n\n".join(f"[BATCH REVIEW {index}]\n{summary}" for index, summary in enumerate(summaries, 1))
    return (
        instructions
        + f"[GLOBAL RESEARCH TOPIC]\n{research_topic}\n\n"
        + f"[RESEARCH PLANNING CONTEXT]\n{research_context}\n\n"
        + "[LITERATURE REVIEW BATCH SUMMARIES]\n" + body
    )


def _checked_review_call(llm_call, prompt, max_tokens):
    try:
        return require_valid_llm_output(llm_call(prompt, max_tokens=max_tokens)).strip()
    except LLMOutputError:
        raise
    except Exception as exc:
        raise LLMOutputError(str(exc)) from exc


def _group_summary_items(items, max_chars):
    groups = []
    current = []
    size = 0
    for item in items:
        value = str(item)
        if len(value) > max_chars:
            value = balanced_excerpt(value, max_chars)
        extra = len(value) + (2 if current else 0)
        if current and size + extra > max_chars:
            groups.append(current)
            current = []
            size = 0
            extra = len(value)
        current.append(value)
        size += extra
    if current:
        groups.append(current)
    return groups


def review_literature_library(research_topic, research_context, literatures, llm_call, *, locale="en", max_chars=12000):
    """Review the full library directly when bounded, otherwise batch and hierarchically synthesize every profile."""
    budget = max(1, int(max_chars))
    full_lines = _full_literature_profile_lines(literatures, locale)
    if len("\n".join(full_lines)) <= budget:
        prompt = build_literature_library_review_prompt(
            research_topic, research_context, literatures, locale=locale, max_chars=budget,
        )
        return _checked_review_call(llm_call, prompt, 4000)

    profile_batches = build_literature_profile_batches(literatures, budget, locale)
    summaries = []
    for index, batch in enumerate(profile_batches, 1):
        prompt = build_literature_profile_batch_review_prompt(
            research_topic, research_context, batch, index, len(profile_batches), locale=locale,
        )
        summary = _checked_review_call(llm_call, prompt, 1200)
        batch_label = f"[PROFILE REVIEW BATCH {index}/{len(profile_batches)}]\n"
        summaries.append(batch_label + balanced_excerpt(summary, min(2400, budget)))

    for level in range(6):
        if sum(len(item) for item in summaries) + max(0, len(summaries) - 1) * 2 <= budget:
            break
        groups = _group_summary_items(summaries, budget)
        merged = []
        for group in groups:
            prompt = build_literature_review_summary_prompt(
                research_topic, research_context, group, locale=locale,
            )
            prompt += "\n\nReturn a concise intermediate synthesis, preserving source-batch distinctions and conflicts."
            value = _checked_review_call(llm_call, prompt, 1200)
            merged.append(balanced_excerpt(value, max(1, min(2400, budget // 6))))
        if len(merged) >= len(summaries) and sum(map(len, merged)) >= sum(map(len, summaries)):
            raise LLMOutputError("The literature review summaries could not be reduced to the context budget.")
        summaries = merged
    else:
        raise LLMOutputError("The full literature library could not be consolidated within the context budget.")

    prompt = build_literature_review_summary_prompt(
        research_topic, research_context, summaries, locale=locale,
    )
    return _checked_review_call(llm_call, prompt, 4000)


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
    bound = list(dict.fromkeys(str(value) for value in (bound_literature_ids or [])))
    query_terms = multilingual_terms(f"{chapter_title or ''} {chapter_description or ''}")
    candidates = []
    for bound_index, literature_id in enumerate(bound):
        for evidence_index, item in enumerate(evidence_store.get(literature_id, []) or []):
            if not isinstance(item, dict):
                continue
            terms = multilingual_terms(f"{item.get('claim', '')} {item.get('section', '')} {item.get('evidence_text', '')}")
            score = len(query_terms & terms)
            if not query_terms or score:
                candidates.append((score, bound_index, evidence_index, item))
    if query_terms:
        candidates.sort(key=lambda row: (-row[0], row[1], int(row[3].get("chunk_index", 0)), row[2]))
    selected = []
    reason = "no_query_terms_fallback" if not query_terms else "multilingual_term_overlap"
    for score, _, _, item in candidates[:max(0, int(limit))]:
        selected.append({
            **{key: item.get(key, "") for key in ("literature_id", "chunk_index", "section", "claim", "evidence_text", "source_locator")},
            "relevance_score": score,
            "selection_reason": reason,
        })
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
