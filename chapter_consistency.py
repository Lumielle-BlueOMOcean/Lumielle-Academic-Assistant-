"""Small deterministic checks and one bounded semantic review for chapter drafts."""

from __future__ import annotations

import re
from decimal import Decimal

from structured_output_support import parse_first_json_value


_CITATION = re.compile(r"\[(\d+)\]")
_LINK = re.compile(r"!?\[[^\]\n]+\]\([^\)\n]*\)")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_NUMBER = re.compile(
    r"(?<![\w.])(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>%|percent(?:age)?(?:\s+points?)?|"
    r"million(?:\s+(?:yuan|dollars?|USD|CNY))?|billion(?:\s+(?:yuan|dollars?|USD|CNY))?|"
    r"thousand(?:\s+(?:yuan|dollars?|USD|CNY))?|trillion(?:\s+(?:yuan|dollars?|USD|CNY))?|"
    r"yuan|dollars?|USD|CNY|participants?|respondents?|people|items?|人|名|个|万|亿|千)?",
    re.IGNORECASE,
)
_ENTITY = re.compile(r"\b[A-Z][A-Za-z0-9&.-]{2,}\b")
_ENTITY_STOPWORDS = {
    "the", "annual", "revenue", "operating", "net", "gross", "profit",
    "income", "turnover", "participants", "participant", "sample", "size",
    "rate", "accuracy", "error", "completion", "company", "corporation",
    "institute", "university", "study", "research",
}
_SCOPE_STOPWORDS = {
    "a", "an", "the", "in", "on", "for", "of", "by", "from", "at", "to",
    "was", "were", "is", "are", "reached", "reported", "recorded", "stood",
    "totaled", "amounted", "measured", "value", "valued", "with", "and", "as",
}
_METRICS = (
    ("annual_revenue", re.compile(r"\bannual\s+revenue\b|年度收入|年收入", re.IGNORECASE)),
    ("operating_revenue", re.compile(r"\boperating\s+revenue\b|营业收入", re.IGNORECASE)),
    ("net_profit", re.compile(r"\bnet\s+profit\b|净利润", re.IGNORECASE)),
    ("operating_profit", re.compile(r"\boperating\s+profit\b|营业利润", re.IGNORECASE)),
    ("sample_size", re.compile(r"\bsample\s+size\b|\bnumber\s+of\s+participants\b|\bparticipants\b|样本量|受试者数量", re.IGNORECASE)),
    ("completion_rate", re.compile(r"\bcompletion\s+rate\b|完成率", re.IGNORECASE)),
    ("accuracy", re.compile(r"\baccuracy\b|准确率", re.IGNORECASE)),
    ("error_rate", re.compile(r"\berror\s+rate\b|误差率", re.IGNORECASE)),
    ("revenue", re.compile(r"\bannual\s+revenue\b|\brevenue\b|\bturnover\b|年收入|销售额", re.IGNORECASE)),
    ("profit", re.compile(r"\bprofit\b|利润", re.IGNORECASE)),
)
_MAX_REVIEW_PROMPT_CHARS = 48_000


def _review_context_text(value):
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item) for item in value if item is not None)
    if isinstance(value, dict):
        return "\n".join(f"{key}: {item}" for key, item in value.items())
    return str(value or "")


def _truncate_review_context(value, limit, locale):
    value = _review_context_text(value)
    if len(value) <= limit:
        return value
    marker = "\n[已截断：审阅上下文预算]" if locale == "zh" else "\n[TRUNCATED: review context budget]"
    if limit <= len(marker):
        return marker[:limit]
    return value[: limit - len(marker)] + marker


def _without_links(text):
    return _LINK.sub("", str(text or ""))


def _sentence_for_span(text, start, end):
    left = max((text.rfind(mark, 0, start) for mark in ".!?。！？\n"), default=-1) + 1
    stops = [position for mark in ".!?。！？\n" if (position := text.find(mark, end)) >= 0]
    right = min(stops) if stops else len(text)
    return text[left:right].strip()


def _metric_mentions(text):
    matches = []
    for metric, pattern in _METRICS:
        matches.extend((match.start(), match.end(), metric) for match in pattern.finditer(text))
    selected = []
    for start, end, metric in sorted(matches, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(start < previous_end and end > previous_start for previous_start, previous_end, _ in selected):
            continue
        selected.append((start, end, metric))
    return [(metric, _sentence_for_span(text, start, end)) for start, end, metric in selected]


def _comparison_scope_signature(sentence, entity_anchors):
    remaining = _YEAR.sub(" ", sentence)
    remaining = _NUMBER.sub(" ", remaining)
    for _, pattern in _METRICS:
        remaining = pattern.sub(" ", remaining)
    for anchor in entity_anchors:
        remaining = re.sub(rf"\b{re.escape(anchor)}\b", " ", remaining, flags=re.IGNORECASE)
    return {
        token.casefold()
        for token in re.findall(r"[A-Za-z]{2,}|[\u3400-\u9fff]{2,}", remaining)
        if token.casefold() not in _SCOPE_STOPWORDS
    }


def _single_numeric_value(sentence):
    matches = list(_NUMBER.finditer(_YEAR.sub(" ", sentence)))
    if len(matches) != 1:
        return None
    match = matches[0]
    unit = " ".join((match.group("unit") or "").casefold().split())
    if unit in {"percent", "percentage"}:
        unit = "%"
    elif unit in {"percent point", "percent points", "percentage point", "percentage points"}:
        unit = "percentage points"
    return (Decimal(match.group("number")), unit)


def _explicit_metric_conflict(reference_text, candidate_text):
    """Return true only for the same named metric, named entity, and explicit year."""
    reference_text = str(reference_text or "")
    candidate_text = str(candidate_text or "")
    reference_mentions = _metric_mentions(reference_text)
    candidate_mentions = _metric_mentions(candidate_text)
    for metric, reference_sentence in reference_mentions:
        reference_years = {match.group(0) for match in _YEAR.finditer(reference_sentence)}
        if len(reference_years) != 1:
            continue
        reference_entities = {
            token.casefold() for token in _ENTITY.findall(reference_sentence)
            if token.casefold() not in _ENTITY_STOPWORDS
        }
        if not reference_entities:
            continue
        reference_value = _single_numeric_value(reference_sentence)
        if reference_value is None:
            continue
        reference_scope = _comparison_scope_signature(reference_sentence, reference_entities)
        for candidate_metric, candidate_sentence in candidate_mentions:
            if candidate_metric != metric:
                continue
            candidate_years = {match.group(0) for match in _YEAR.finditer(candidate_sentence)}
            if candidate_years != reference_years:
                continue
            candidate_entities = {
                token.casefold() for token in _ENTITY.findall(candidate_sentence)
                if token.casefold() not in _ENTITY_STOPWORDS
            }
            if not reference_entities.intersection(candidate_entities):
                continue
            shared_entities = reference_entities.intersection(candidate_entities)
            if _comparison_scope_signature(candidate_sentence, shared_entities) != reference_scope:
                continue
            candidate_value = _single_numeric_value(candidate_sentence)
            if candidate_value is not None and candidate_value[1] == reference_value[1] and candidate_value[0] != reference_value[0]:
                return True
    return False


def check_deterministic_consistency(candidate, valid_reference_count=0, confirmed_items=()):
    """Return only high-confidence, machine-checkable findings."""
    conflicts = []
    for match in _CITATION.finditer(_without_links(candidate)):
        number = int(match.group(1))
        if number < 1 or number > int(valid_reference_count or 0):
            conflicts.append({"code": "invalid_citation", "citation": match.group(0), "reason": "Citation has no reference in this chapter."})

    # Hard-check only explicit metric comparisons with the same named entity and year.
    # Topic overlap or unrelated numbers are left to the semantic review.
    for item in confirmed_items or ():
        if not isinstance(item, dict) or not item.get("confirmed_for_writing"):
            continue
        statement = str(item.get("statement") or "")
        quote = str(item.get("source_quote") or "")
        reference_text = quote if quote.strip() else statement
        if _explicit_metric_conflict(reference_text, candidate):
            conflicts.append({
                "code": "confirmed_number_conflict",
                "memory_id": item.get("id"),
                "reason": "The same named entity has a different value for the same explicitly scoped metric.",
            })
    return {"status": "needs_review" if conflicts else "pass", "hard_conflicts": conflicts}


def _semantic_prompt(candidate, context, locale, correction=False):
    context = context if isinstance(context, dict) else {}
    normalized = {
        "title": _review_context_text(context.get("current_title", "")),
        "description": _review_context_text(context.get("current_desc", "")),
        "constraints": _review_context_text(context.get("research_constraints", "")),
        "facts": _review_context_text(context.get("research_facts", "")),
        "evidence": _review_context_text(context.get("literature_evidence", "")),
        "upstream": _review_context_text(context.get("upstream", "")),
        "downstream": _review_context_text(context.get("downstream", "")),
    }
    candidate = str(candidate or "")
    if locale == "zh":
        correction_text = "上次输出不是有效审阅 JSON。请仅按下列结构修正。\n" if correction else ""
        instruction = (
            "你是论文章节一致性审阅助手。只检查候选正文与给定的已写章节信息是否存在明显冲突，不判断学术结论的客观真伪。"
            "重点看重要论断、术语、数值主体/时间/口径、假设被写成事实，以及是否越过未写章节边界。没有明确风险时返回 pass。"
            "只输出 JSON：{\"status\":\"pass\",\"issues\":[]}；status 只能为 pass、warning、needs_review。issues 每项包含简短 reason 与 source。"
            "这是风险提示，不是学术真实性认证；不得补充事实。\n"
        )
        labels = {
            "chapter": "[当前章节]\n", "constraints": "[必须遵守的研究约束]\n",
            "facts": "[本章科研事实]\n", "evidence": "[本章绑定文献证据与来源]\n",
            "upstream": "[已写上游]\n", "downstream": "[已写下游与章节边界]\n",
            "candidate": "[候选正文]\n",
        }
    else:
        correction_text = "The previous output was not valid review JSON. Correct it to the required shape only.\n" if correction else ""
        instruction = (
            "Review a candidate paper chapter only for clear conflicts with the supplied written-chapter context. Do not judge objective academic truth. "
            "Look for contradictions in important claims, terminology drift, changed subjects/timeframes/measurement bases for numbers, assumptions presented as facts, and content that crosses an unwritten chapter boundary. Return pass when no clear risk is present. "
            "Output JSON only: {\"status\":\"pass\",\"issues\":[]}. status is pass, warning, or needs_review. Each issue has a short reason and a source chapter or memory reference. "
            "This is a risk signal, not truth certification. Do not add facts.\n"
        )
        labels = {
            "chapter": "[CURRENT CHAPTER]\n", "constraints": "[REQUIRED RESEARCH CONSTRAINTS]\n",
            "facts": "[SELECTED RESEARCH FACTS]\n", "evidence": "[BOUND LITERATURE EVIDENCE AND SOURCES]\n",
            "upstream": "[WRITTEN UPSTREAM]\n", "downstream": "[WRITTEN DOWNSTREAM AND BOUNDARY]\n",
            "candidate": "[CANDIDATE CHAPTER]\n",
        }
    chapter = normalized["title"] + ("\n" + normalized["description"] if normalized["description"] else "")
    required = [("chapter", chapter), ("constraints", normalized["constraints"]), ("candidate", candidate)]
    optional = {key: normalized[key] for key in ("facts", "evidence", "upstream", "downstream")}
    fixed = instruction + correction_text + "".join(labels[key] + value + "\n" for key, value in required)
    if len(fixed) > _MAX_REVIEW_PROMPT_CHARS:
        return None
    prompt = fixed + "".join(labels[key] + value + "\n" for key, value in optional.items() if value)
    overflow = len(prompt) - _MAX_REVIEW_PROMPT_CHARS
    if overflow > 0:
        # Reduce low-priority surrounding prose first; keep constraints and candidate intact.
        for key in ("downstream", "upstream", "evidence", "facts"):
            value = optional[key]
            if not value or overflow <= 0:
                continue
            allowance = max(0, len(value) - overflow)
            optional[key] = _truncate_review_context(value, allowance, locale) if allowance else ""
            overflow -= len(value) - len(optional[key])
        if overflow > 0:
            return None
    return fixed + "".join(labels[key] + value + "\n" for key, value in optional.items() if value)


def _normalize_review(value):
    if not isinstance(value, dict) or set(value) - {"status", "issues"}:
        return None
    status = value.get("status")
    raw_issues = value.get("issues")
    if not isinstance(status, str) or status not in {"pass", "warning", "needs_review"} or not isinstance(raw_issues, list):
        return None
    issues = []
    for item in raw_issues:
        if not isinstance(item, dict):
            return None
        reason = item.get("reason", "")
        source = item.get("source", "")
        if not isinstance(reason, str) or not isinstance(source, str):
            return None
        if reason.strip():
            issues.append({"reason": reason.strip()[:300], "source": source.strip()[:160]})
    if status == "pass" and issues:
        return None
    return {"status": status, "issues": issues}


def review_semantic_consistency(candidate, context, llm_call, locale="en"):
    """Make one structured review request and at most one format-correction request."""
    for attempt in range(2):
        prompt = _semantic_prompt(candidate, context, locale, correction=attempt == 1)
        if prompt is None:
            return {"status": "needs_review", "completed": False, "failure_code": "review_context_too_large", "issues": []}
        try:
            result = llm_call(
                prompt,
                max_tokens=1400,
                json_mode=True,
                return_metadata=True,
            )
        except Exception:
            return {"status": "needs_review", "completed": False, "failure_code": "provider_error", "issues": []}
        if not isinstance(result, dict) or result.get("error_code"):
            return {
                "status": "needs_review", "completed": False,
                "failure_code": result.get("error_code", "incomplete_response") if isinstance(result, dict) else "incomplete_response",
                "issues": [],
            }
        if result.get("finish_reason") != "stop":
            return {"status": "needs_review", "completed": False, "failure_code": "incomplete_response", "issues": []}
        content = result.get("content")
        value = parse_first_json_value(content) if isinstance(content, str) and content.strip() else None
        normalized = _normalize_review(value)
        if normalized is not None:
            return {**normalized, "completed": True, "failure_code": None}
    return {"status": "needs_review", "completed": False, "failure_code": "invalid_structured_output", "issues": []}


def review_chapter_candidate(candidate, context, valid_reference_count, llm_call, locale="en", confirmed_items=()):
    """Combine hard checks with one bounded semantic review; only a clean pass is auto-saveable."""
    deterministic = check_deterministic_consistency(
        candidate,
        valid_reference_count=valid_reference_count,
        confirmed_items=confirmed_items,
    )
    if deterministic["hard_conflicts"]:
        return {
            "status": "needs_review",
            "completed": True,
            "hard_conflicts": deterministic["hard_conflicts"],
            "issues": [],
            "failure_code": None,
        }
    semantic = review_semantic_consistency(candidate, context, llm_call, locale=locale)
    return {
        "status": semantic["status"],
        "completed": semantic["completed"],
        "hard_conflicts": [],
        "issues": semantic.get("issues", []),
        "failure_code": semantic.get("failure_code"),
    }
