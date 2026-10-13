"""Small deterministic checks and one bounded semantic review for chapter drafts."""

from __future__ import annotations

import re

from structured_output_support import parse_first_json_value


_CITATION = re.compile(r"\[(\d+)\]")
_LINK = re.compile(r"!?\[[^\]\n]+\]\([^\)\n]*\)")


def _without_links(text):
    return _LINK.sub("", str(text or ""))


def check_deterministic_consistency(candidate, valid_reference_count=0, confirmed_items=()):
    """Return only high-confidence, machine-checkable findings."""
    conflicts = []
    for match in _CITATION.finditer(_without_links(candidate)):
        number = int(match.group(1))
        if number < 1 or number > int(valid_reference_count or 0):
            conflicts.append({"code": "invalid_citation", "citation": match.group(0), "reason": "Citation has no reference in this chapter."})

    # Compare numbers only when a confirmed statement and candidate share a concrete subject token.
    for item in confirmed_items or ():
        if not isinstance(item, dict) or not item.get("confirmed_for_writing"):
            continue
        statement = str(item.get("statement") or "")
        quote = str(item.get("source_quote") or "")
        subject_tokens = {
            token.casefold()
            for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u3400-\u9fff]{2,}", statement)
            if token.casefold() not in {"the", "and", "with", "from", "that", "this"}
        }
        candidate_folded = str(candidate or "").casefold()
        shared = [token for token in subject_tokens if token in candidate_folded]
        source_numbers = set(re.findall(r"(?<!\w)\d+(?:\.\d+)?%?", quote))
        if shared and source_numbers:
            candidate_numbers = set(re.findall(r"(?<!\w)\d+(?:\.\d+)?%?", candidate_folded))
            if candidate_numbers and not source_numbers.intersection(candidate_numbers):
                conflicts.append({
                    "code": "confirmed_number_conflict",
                    "memory_id": item.get("id"),
                    "reason": "A number tied to a confirmed subject differs from its source passage.",
                })
    return {"status": "needs_review" if conflicts else "pass", "hard_conflicts": conflicts}


def _semantic_prompt(candidate, context, locale, correction=False):
    context = context if isinstance(context, dict) else {}
    if locale == "zh":
        correction_text = "上次输出不是有效审阅 JSON。请仅按下列结构修正。\n" if correction else ""
        return (
            "你是论文章节一致性审阅助手。只检查候选正文与给定的已写章节信息是否存在明显冲突，不判断学术结论的客观真伪。"
            "重点看重要论断、术语、数值主体/时间/口径、假设被写成事实，以及是否越过未写章节边界。没有明确风险时返回 pass。"
            "只输出 JSON：{\"status\":\"pass\",\"issues\":[]}；status 只能为 pass、warning、needs_review。issues 每项包含简短 reason 与 source。"
            "这是风险提示，不是学术真实性认证；不得补充事实。\n"
            + correction_text
            + "[已写上游]\n" + str(context.get("upstream", ""))
            + "\n[已写下游与章节边界]\n" + str(context.get("downstream", ""))
            + "\n[候选正文]\n" + str(candidate or "")
        )
    correction_text = "The previous output was not valid review JSON. Correct it to the required shape only.\n" if correction else ""
    return (
        "Review a candidate paper chapter only for clear conflicts with the supplied written-chapter context. Do not judge objective academic truth. "
        "Look for contradictions in important claims, terminology drift, changed subjects/timeframes/measurement bases for numbers, assumptions presented as facts, and content that crosses an unwritten chapter boundary. Return pass when no clear risk is present. "
        "Output JSON only: {\"status\":\"pass\",\"issues\":[]}. status is pass, warning, or needs_review. Each issue has a short reason and a source chapter or memory reference. "
        "This is a risk signal, not truth certification. Do not add facts.\n"
        + correction_text
        + "[WRITTEN UPSTREAM]\n" + str(context.get("upstream", ""))
        + "\n[WRITTEN DOWNSTREAM AND BOUNDARY]\n" + str(context.get("downstream", ""))
        + "\n[CANDIDATE CHAPTER]\n" + str(candidate or "")
    )


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
        try:
            result = llm_call(
                _semantic_prompt(candidate, context, locale, correction=attempt == 1),
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
