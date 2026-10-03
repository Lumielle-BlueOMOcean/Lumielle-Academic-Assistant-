"""Localized shared literature import and legacy re-analysis UI."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import streamlit as st

from document_support import is_document_parse_error
from literature_intelligence import (
    analyze_literature_document,
    apply_analysis_to_literature_record,
    new_literature_record,
    save_literature_source,
)


_LABELS = {
    "en": {
        "heading": "Complete-document Literature Intelligence",
        "upload": "Import PDF / DOCX / TXT references (multiple files supported)",
        "import": "Analyze and import uploaded references",
        "paste_title": "Pasted reference title", "paste_text": "Paste or type literature text",
        "paste_source": "Source / URL (optional)", "paste_button": "Analyze and import pasted reference",
        "error": "Text extraction failed", "unavailable": "The source was saved, but complete AI analysis was unavailable; no partial profile or evidence was stored.",
        "success": "Complete document analyzed across {chunks} ordered chunk(s); {evidence} evidence item(s) saved.",
        "added": "Imported {count} reference(s).", "library": "Literature Library",
        "evidence": "Stored evidence chunks", "reanalyze": "Re-analyze complete source for profile and evidence",
        "reanalyze_failed": "Re-analysis failed. Existing profile and evidence were preserved.",
        "reanalyze_success": "Full document profile and evidence updated.", "open_error": "Original source is unavailable.",
    },
    "zh": {
        "heading": "全文文献智能分析",
        "upload": "导入 PDF / DOCX / TXT 文献（支持多选）",
        "import": "分析并导入上传文献",
        "paste_title": "粘贴文献标题", "paste_text": "直接输入或粘贴文献文本",
        "paste_source": "来源 / URL（可选）", "paste_button": "分析并导入粘贴文献",
        "error": "文本提取失败", "unavailable": "原始资料已保存，但完整 AI 分析未完成；未保存部分档案或证据。",
        "success": "完整文献已按顺序处理 {chunks} 个分块，保存 {evidence} 条证据。",
        "added": "已导入 {count} 篇文献。", "library": "文献库",
        "evidence": "已保存的证据片段", "reanalyze": "基于完整原文重新分析档案与证据",
        "reanalyze_failed": "重新分析失败，原有档案与证据保持不变。",
        "reanalyze_success": "已根据完整原文更新文献档案与证据。", "open_error": "原始文献不可用。",
    },
}


_FAILURE_MESSAGES = {
    "en": {
        "provider_call": "The source was saved, but the AI service call failed. Test the current model connection and try again; no partial profile or evidence was stored.",
        "invalid_structured_output": "The source was saved, but the AI returned malformed structured data. Retry; no partial profile or evidence was stored.",
        "empty_structured_output": "The source was saved, but the AI returned empty analysis data. Retry; no partial profile or evidence was stored.",
        "schema_validation_failure": "The source was saved, but the AI data did not match the literature-analysis schema. Retry; no partial profile or evidence was stored.",
        "document_synthesis": "Chunk analysis completed, but full-document synthesis failed. The source was kept; no partial profile or evidence was stored.",
        "synthesis_reduction_failure": "The ordered chunk analyses could not be compacted safely for full-document synthesis. The source was kept; no partial profile or evidence was stored.",
    },
    "zh": {
        "provider_call": "原始资料已保存，但 AI 服务调用失败。请先测试当前模型连接后重试；未保存部分档案或证据。",
        "invalid_structured_output": "原始资料已保存，但 AI 返回的数据格式异常。请重试；未保存部分档案或证据。",
        "empty_structured_output": "原始资料已保存，但 AI 返回了空的分析数据。请重试；未保存部分档案或证据。",
        "schema_validation_failure": "原始资料已保存，但 AI 返回内容不符合文献分析格式。请重试；未保存部分档案或证据。",
        "document_synthesis": "文献分块已处理，但整篇档案合并失败。原始资料已保留，未保存部分档案或证据。",
        "synthesis_reduction_failure": "无法在安全限制内归并全部文献分块。原始资料已保留，未保存部分档案或证据。",
    },
}

_REANALYSIS_FAILURE_MESSAGES = {
    "en": {
        "provider_call": "The AI service call failed during re-analysis. Test the current model connection and retry; existing profile and evidence were preserved.",
        "invalid_structured_output": "The AI returned malformed structured data during re-analysis. Existing profile and evidence were preserved.",
        "empty_structured_output": "The AI returned empty analysis data during re-analysis. Existing profile and evidence were preserved.",
        "schema_validation_failure": "The AI data did not match the literature-analysis schema. Existing profile and evidence were preserved.",
        "document_synthesis": "Full-document synthesis failed during re-analysis. Existing profile and evidence were preserved.",
        "synthesis_reduction_failure": "The ordered chunk analyses could not be compacted safely. Existing profile and evidence were preserved.",
    },
    "zh": {
        "provider_call": "重新分析时 AI 服务调用失败。请先测试当前模型连接后重试；原有档案与证据保持不变。",
        "invalid_structured_output": "重新分析时 AI 返回的数据格式异常；原有档案与证据保持不变。",
        "empty_structured_output": "重新分析时 AI 返回了空的分析数据；原有档案与证据保持不变。",
        "schema_validation_failure": "重新分析时 AI 返回内容不符合文献分析格式；原有档案与证据保持不变。",
        "document_synthesis": "文献分块已处理，但整篇档案合并失败；原有档案与证据保持不变。",
        "synthesis_reduction_failure": "无法在安全限制内归并全部文献分块；原有档案与证据保持不变。",
    },
}


_FAILURE_GROUPS = {
    "schema_validation_failure": "schema",
    "invalid_structured_output": "invalid_json",
    "empty_structured_output": "empty",
    "truncated_output": "truncated",
    "synthesis_reduction_failure": "reduction",
    "content_filtered": "filtered",
    "rate_limited": "temporary",
    "timeout": "temporary",
    "connection_error": "temporary",
    "server_error": "temporary",
    "insufficient_system_resource": "temporary",
    "aborted": "temporary",
    "authentication_failed": "configuration",
    "insufficient_balance": "configuration",
    "model_not_available": "configuration",
    "invalid_request": "configuration",
    "provider_error": "provider",
    "incomplete_response": "provider",
    "source_extraction_failure": "source",
    "empty_source": "source",
}


def _result_failure_code(result):
    if not isinstance(result, dict):
        return "provider_call"
    return str(result.get("failure_code") or result.get("failure_stage") or "provider_call")


def _failure_detail(result, locale="en"):
    language = "zh" if str(locale).lower().startswith("zh") else "en"
    code = _result_failure_code(result)
    diagnostic = result.get("diagnostic", {}) if isinstance(result, dict) else {}
    diagnostic = diagnostic if isinstance(diagnostic, dict) else {}
    status = diagnostic.get("http_status")
    valid_status = isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599
    status_text = f"（HTTP {status}）" if language == "zh" and valid_status else f" (HTTP {status})" if valid_status else ""
    chunk_index = diagnostic.get("chunk_index")
    chunks_total = diagnostic.get("chunks_total")
    valid_chunk_position = (
        isinstance(chunk_index, int) and not isinstance(chunk_index, bool)
        and isinstance(chunks_total, int) and not isinstance(chunks_total, bool)
    )
    chunk_text = (
        f"（第 {chunk_index}/{chunks_total} 分块）" if language == "zh" and valid_chunk_position
        else f" (chunk {chunk_index}/{chunks_total})" if valid_chunk_position
        else ""
    )
    locator = diagnostic.get("source_locator")
    safe_locator = locator if isinstance(locator, str) and re.fullmatch(r"\d+(?:\.\d+)*", locator) else ""
    locator_text = (
        f"（源分块 {safe_locator}）" if language == "zh" and safe_locator
        else f" (source part {safe_locator})" if safe_locator
        else ""
    )
    failure_stage = diagnostic.get("stage")
    if language == "zh":
        truncated_detail = (
            "各部分已完成分析，但最终档案合并连续两次超过模型输出长度限制"
            if failure_stage == "synthesis"
            else "文献已自动细分处理，但其中一个最小分块的结构化输出仍超过模型长度限制"
        )
        reduction_detail = "无法在有界长度内归并全部分块摘要"
        details = {
            "schema_validation_failure": "结构化数据不兼容" + chunk_text,
            "invalid_structured_output": "结构化 JSON 格式无效" + chunk_text,
            "empty_structured_output": "模型返回了空分析内容" + chunk_text,
            "truncated_output": truncated_detail + chunk_text + locator_text,
            "synthesis_reduction_failure": reduction_detail,
            "content_filtered": "模型内容安全过滤阻止了本次分析" + chunk_text,
            "rate_limited": "AI 服务暂时繁忙或请求受限，系统已自动重试" + status_text,
            "timeout": "AI 服务响应超时，系统已自动重试" + status_text,
            "connection_error": "暂时无法连接 AI 服务，系统已自动重试" + status_text,
            "server_error": "AI 服务暂时不可用，系统已自动重试" + status_text,
            "insufficient_system_resource": "AI 服务资源暂时不足，系统已自动重试" + status_text,
            "aborted": "AI 服务中断了本次请求，系统已自动重试" + status_text,
            "authentication_failed": "AI 认证失败，请检查模型配置后重试" + status_text,
            "insufficient_balance": "AI 服务账户余额不足，请检查模型账户后重试" + status_text,
            "model_not_available": "当前模型不可用，请检查模型配置" + status_text,
            "invalid_request": "AI 请求配置无效，请检查模型配置" + status_text,
            "provider_error": "AI 服务返回了无法识别的错误" + status_text,
            "incomplete_response": "AI 服务返回不完整，请稍后重试" + status_text,
            "provider_call": "AI 服务调用失败，请检查模型配置后重试" + status_text,
            "source_extraction_failure": "原文提取失败",
            "empty_source": "没有可分析的原文",
        }
        detail = details.get(code, "AI 分析失败" + chunk_text + status_text)
        if code != "truncated_output":
            detail += locator_text
        split_depth = diagnostic.get("split_depth")
        if isinstance(split_depth, int) and not isinstance(split_depth, bool) and code == "truncated_output":
            detail += f"（细分层级 {split_depth}）"
        reduction_level = diagnostic.get("reduction_level")
        if isinstance(reduction_level, int) and not isinstance(reduction_level, bool):
            detail += f"（归并层级 {reduction_level}）"
        return detail
    truncated_detail = (
        "All source parts were analyzed, but final profile synthesis exceeded the output limit twice"
        if failure_stage == "synthesis"
        else "The source was split automatically, but a minimum-size part still exceeded the model output limit"
    )
    details = {
        "schema_validation_failure": "Incompatible structured data" + chunk_text,
        "invalid_structured_output": "Malformed structured JSON" + chunk_text,
        "empty_structured_output": "The model returned empty analysis" + chunk_text,
        "truncated_output": truncated_detail + chunk_text + locator_text,
        "synthesis_reduction_failure": "The chunk analyses could not be compacted within bounded synthesis limits",
        "content_filtered": "The model's content filter blocked this analysis" + chunk_text,
        "rate_limited": "The AI service is busy or rate-limited; automatic retries were attempted" + status_text,
        "timeout": "The AI service timed out; automatic retries were attempted" + status_text,
        "connection_error": "The AI service could not be reached; automatic retries were attempted" + status_text,
        "server_error": "The AI service is temporarily unavailable; automatic retries were attempted" + status_text,
        "insufficient_system_resource": "The AI service temporarily lacked resources; automatic retries were attempted" + status_text,
        "aborted": "The AI service interrupted the request; automatic retries were attempted" + status_text,
        "authentication_failed": "AI authentication failed; check the model configuration" + status_text,
        "insufficient_balance": "The AI provider account has insufficient balance" + status_text,
        "model_not_available": "The selected model is unavailable; check the model configuration" + status_text,
        "invalid_request": "The AI request configuration is invalid; check the model settings" + status_text,
        "provider_error": "The AI service returned an unclassified error" + status_text,
        "incomplete_response": "The AI service returned an incomplete response" + status_text,
        "provider_call": "The AI service call failed; check the model configuration" + status_text,
        "source_extraction_failure": "Source text extraction failed",
        "empty_source": "No source text was available to analyze",
    }
    detail = details.get(code, "AI analysis failed" + chunk_text + status_text)
    if code != "truncated_output":
        detail += locator_text
    split_depth = diagnostic.get("split_depth")
    if isinstance(split_depth, int) and not isinstance(split_depth, bool) and code == "truncated_output":
        detail += f" (split depth {split_depth})"
    reduction_level = diagnostic.get("reduction_level")
    if isinstance(reduction_level, int) and not isinstance(reduction_level, bool):
        detail += f" (reduction level {reduction_level})"
    return detail


def failure_message(result, locale="en", *, reanalysis=False):
    """Map safe failure codes and diagnostics to localized user-facing copy."""
    language = "zh" if str(locale).lower().startswith("zh") else "en"
    # Preserve established copy for older callers that only supply a stage.
    if isinstance(result, dict) and not result.get("failure_code"):
        messages = _REANALYSIS_FAILURE_MESSAGES if reanalysis else _FAILURE_MESSAGES
        legacy = messages[language].get(result.get("failure_stage"))
        if legacy:
            return legacy
    detail = _failure_detail(result, language)
    if reanalysis:
        return (
            f"{detail}；原有档案与证据保持不变。" if language == "zh"
            else f"{detail}; existing profile and evidence were preserved."
        )
    if language == "zh":
        return f"原始资料已保存，但{detail}；未保存部分档案或证据。"
    return f"The source was saved, but {detail}; no partial profile or evidence was stored."


def format_batch_outcome(item, locale="en"):
    """Render one filename-bound status without exposing model response contents."""
    language = "zh" if str(locale).lower().startswith("zh") else "en"
    filename = str(item.get("filename", "file")) if isinstance(item, dict) else "file"
    result = item.get("result", {}) if isinstance(item, dict) else {}
    if isinstance(item, dict) and item.get("success"):
        return f"{filename} — 分析成功" if language == "zh" else f"{filename} — analyzed successfully"
    detail = _failure_detail(result, language)
    if isinstance(item, dict) and item.get("source_saved"):
        return f"{filename} — 仅保存原文：{detail}" if language == "zh" else f"{filename} — original saved only: {detail}"
    return f"{filename} — 分析失败：{detail}" if language == "zh" else f"{filename} — analysis failed: {detail}"


def summarize_literature_batch(items, locale="en"):
    """Summarize analyzed records separately from saved-original-only outcomes."""
    language = "zh" if str(locale).lower().startswith("zh") else "en"
    outcomes = items if isinstance(items, (list, tuple)) else []
    analyzed = sum(bool(item.get("success")) for item in outcomes if isinstance(item, dict))
    source_only = sum(
        not bool(item.get("success")) and bool(item.get("source_saved"))
        for item in outcomes if isinstance(item, dict)
    )
    if language == "zh":
        summary = f"本批次共 {len(outcomes)} 篇：成功分析 {analyzed}，仅保存原文 {source_only}。"
        names = {
            "schema": "结构化数据不兼容", "invalid_json": "JSON 格式无效", "empty": "空分析结果",
            "truncated": "输出达到长度限制", "reduction": "分块摘要归并失败", "temporary": "服务暂时不可用/请求受限",
            "configuration": "模型配置或账户问题", "filtered": "内容安全过滤", "provider": "其他服务错误",
            "source": "原文提取失败",
        }
        counts = {}
        for item in outcomes:
            if not isinstance(item, dict) or item.get("success"):
                continue
            code = _result_failure_code(item.get("result", {}))
            group = _FAILURE_GROUPS.get(code, "source" if code == "source_extraction_failure" else "provider")
            counts[group] = counts.get(group, 0) + 1
        if counts:
            details = "、".join(f"{names[group]} {count}" for group, count in counts.items())
            summary += f"\n失败原因：{details}。"
        return summary
    summary = f"Batch: {len(outcomes)} files; analyzed {analyzed}; original only {source_only}."
    names = {
        "schema": "incompatible structured data", "invalid_json": "invalid JSON", "empty": "empty analysis",
        "truncated": "length-limited output", "reduction": "chunk reduction failure", "temporary": "temporary service/rate-limit issue",
        "configuration": "model configuration/account issue", "filtered": "content filter", "provider": "other provider error",
        "source": "source extraction failure",
    }
    counts = {}
    for item in outcomes:
        if not isinstance(item, dict) or item.get("success"):
            continue
        code = _result_failure_code(item.get("result", {}))
        group = _FAILURE_GROUPS.get(code, "source" if code == "source_extraction_failure" else "provider")
        counts[group] = counts.get(group, 0) + 1
    if counts:
        summary += "\nFailure reasons: " + "; ".join(f"{names[group]} {count}" for group, count in counts.items()) + "."
    return summary


def _safe_batch_notice(items):
    """Keep only filename, outcome, and whitelisted safe diagnostics across rerun."""
    diagnostic_fields = (
        "stage", "chunk_index", "chunks_total", "failure_code", "finish_reason",
        "http_status", "response_chars", "structured_mode", "retry_count", "top_level_shape",
        "source_locator", "source_chars", "split_depth", "reduction_level",
    )
    safe = []
    for item in items if isinstance(items, (list, tuple)) else ():
        if not isinstance(item, dict):
            continue
        result = item.get("result", {}) if isinstance(item.get("result"), dict) else {}
        raw_diagnostic = result.get("diagnostic", {}) if isinstance(result.get("diagnostic"), dict) else {}
        diagnostic = {}
        for key in diagnostic_fields:
            if key not in raw_diagnostic:
                continue
            value = raw_diagnostic[key]
            if key == "source_locator":
                if isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)*", value):
                    diagnostic[key] = value
            elif key == "top_level_shape":
                if isinstance(value, dict):
                    diagnostic[key] = {
                        str(name)[:60]: str(kind)[:24]
                        for name, kind in list(value.items())[:24]
                        if isinstance(name, str) and isinstance(kind, str)
                    }
            elif key in {"source_chars", "split_depth", "reduction_level", "chunk_index", "chunks_total", "http_status", "response_chars", "retry_count"}:
                if isinstance(value, int) and not isinstance(value, bool):
                    diagnostic[key] = value
            elif key == "stage":
                if isinstance(value, str) and value in {"input", "chunk", "synthesis", "synthesis_reduction"}:
                    diagnostic[key] = value
            elif key == "finish_reason":
                if isinstance(value, str) and value in {"stop", "length", "content_filter", "insufficient_system_resource", "aborted", "unknown"}:
                    diagnostic[key] = value
            elif key == "structured_mode":
                if isinstance(value, str) and value in {"native", "compatibility", "legacy", "unknown"}:
                    diagnostic[key] = value
            elif key == "failure_code":
                if isinstance(value, str) and re.fullmatch(r"[a-z_]{1,80}", value):
                    diagnostic[key] = value
        safe.append({
            "filename": str(item.get("filename", "file"))[:240],
            "success": bool(item.get("success")),
            "source_saved": bool(item.get("source_saved")),
            "result": {
                "failure_code": str(result.get("failure_code") or result.get("failure_stage") or "provider_call")[:80],
                "failure_stage": str(result.get("failure_stage") or "")[:80],
                "diagnostic": diagnostic,
            },
        })
    return safe


def literature_summary_for_display(literature, locale="en"):
    """Show only a completed AI summary; never substitute the imported source prefix."""
    language = "zh" if str(locale).lower().startswith("zh") else "en"
    record = literature if isinstance(literature, dict) else {}
    analysis = record.get("analysis") if isinstance(record.get("analysis"), dict) else {}
    summary = analysis.get("summary")
    if record.get("analysis_status") == "ok":
        if isinstance(summary, str) and summary.strip():
            prefix = "AI 文献摘要：" if language == "zh" else "AI literature summary:"
            return f"{prefix} {summary.strip()}"
        return "AI 文献摘要：完整分析中未提供摘要。" if language == "zh" else "AI literature summary: no summary was provided in the completed analysis."
    return "AI 文献摘要：尚未生成（文献分析未完成）。" if language == "zh" else "AI literature summary: not generated because analysis is incomplete."


def literature_importance_control(literature, locale="en"):
    language = "zh" if str(locale).lower().startswith("zh") else "en"
    marked = bool(literature.get("important", False)) if isinstance(literature, dict) else False
    if language == "zh":
        label = "📌 已标记重点" if marked else "📌 标记重点"
        help_text = "人工重点标记，仅用于快速识别重要文献，不改变 AI 相关性评级，也不会自动绑定到章节。"
    else:
        label = "📌 Important" if marked else "📌 Mark important"
        help_text = "Manual importance marker only; it does not change the AI relevance rating or bind the reference to a chapter."
    return label, help_text


def toggle_literature_importance(literature):
    if not isinstance(literature, dict):
        return False
    literature["important"] = not bool(literature.get("important", False))
    return literature["important"]


_CATEGORY_LABELS_ZH = {
    "Empirical Study": "实证研究",
    "Theoretical / Conceptual Study": "理论 / 概念研究",
    "Literature Review": "文献综述",
    "Case Study": "案例研究",
    "Policy / Official Document": "政策 / 官方文件",
    "Standard / Guideline": "标准 / 指南",
    "Data / Research Report": "数据 / 研究报告",
    "Thesis / Dissertation": "学位论文",
    "Methodological Study": "方法研究",
    "Other": "其他",
    "Unclassified": "未分类",
}


def display_literature_category(category, locale="en"):
    language = "zh" if str(locale).lower().startswith("zh") else "en"
    value = category if isinstance(category, str) and category in _CATEGORY_LABELS_ZH else "Unclassified"
    return _CATEGORY_LABELS_ZH[value] if language == "zh" else value


def _import_one(title, text, source, link, raw_bytes, extension, literatures, evidence_store, raw_dir, llm_call, locale, research_topic="", research_context=""):
    literature_id = str(uuid.uuid4())
    filename = title if os.path.splitext(title)[1] else f"{title}{extension}"
    file_path = ""
    try:
        file_path = save_literature_source(raw_dir, literature_id, filename, raw_bytes)
    except Exception:
        file_path = ""
    record = new_literature_record(literature_id, title, text, source=source, link=link, file_path=file_path)
    result = analyze_literature_document(
        text, title, llm_call, locale=locale,
        research_topic=research_topic, research_context=research_context,
    )
    updated, new_store, success = apply_analysis_to_literature_record(record, evidence_store, result)
    literatures.append(updated)
    return new_store, success, result


def render_literature_ingestion(st, literatures, evidence_store, raw_dir, extract_upload_text, llm_call, save_records, save_evidence, locale="en", *, research_topic="", research_context=""):
    labels = _LABELS[locale]
    st.subheader(labels["heading"])
    previous_notice = st.session_state.pop("literature_batch_notice", None)
    if isinstance(previous_notice, list):
        for item in previous_notice:
            rendered = format_batch_outcome(item, locale)
            (st.success if item.get("success") else st.warning)(rendered)
        st.info(summarize_literature_batch(previous_notice, locale))
    uploads = st.file_uploader(labels["upload"], type=["pdf", "docx", "txt"], accept_multiple_files=True, key="literature_full_uploads")
    if uploads and st.button(labels["import"], key="literature_full_import"):
        outcomes = []
        progress = st.progress(0)
        for index, upload in enumerate(uploads):
            text = extract_upload_text(upload)
            if is_document_parse_error(text):
                outcome = {
                    "filename": upload.name, "success": False, "source_saved": False,
                    "result": {"failure_code": "source_extraction_failure", "failure_stage": "source_extraction_failure"},
                }
                st.warning(f"{upload.name} — {labels['error']}.")
            else:
                with st.spinner(upload.name):
                    evidence_store, success, result = _import_one(
                        upload.name, text, "Local Upload", "", upload.getvalue(),
                        Path(upload.name).suffix or ".txt", literatures, evidence_store, raw_dir, llm_call, locale,
                        research_topic, research_context,
                    )
                outcome = {
                    "filename": upload.name, "success": success,
                    "source_saved": bool(literatures and literatures[-1].get("file_path")),
                    "result": result,
                }
                rendered = format_batch_outcome(outcome, locale)
                if success:
                    st.success(rendered)
                else:
                    st.warning(rendered)
            outcomes.append(outcome)
            progress.progress((index + 1) / len(uploads))
        save_records(literatures)
        save_evidence(evidence_store)
        st.session_state["literature_batch_notice"] = _safe_batch_notice(outcomes)
        st.rerun()

    with st.expander(labels["paste_text"], expanded=False):
        title = st.text_input(labels["paste_title"], key="literature_paste_title")
        source = st.text_input(labels["paste_source"], key="literature_paste_source")
        pasted = st.text_area(labels["paste_text"], height=180, key="literature_paste_body")
        if st.button(labels["paste_button"], key="literature_paste_import"):
            if not title.strip() or not pasted.strip():
                st.warning(labels["paste_text"])
            else:
                evidence_store, success, result = _import_one(
                    title.strip(), pasted, source or "Pasted Text", source,
                    pasted.encode("utf-8"), ".txt", literatures, evidence_store, raw_dir, llm_call, locale,
                    research_topic, research_context,
                )
                save_records(literatures)
                save_evidence(evidence_store)
                if success:
                    st.success(format_batch_outcome({"filename": title.strip(), "success": True}, locale))
                else:
                    st.warning(format_batch_outcome({
                        "filename": title.strip(), "success": False,
                        "source_saved": bool(literatures and literatures[-1].get("file_path")),
                        "result": result,
                    }, locale))
                st.rerun()


def render_legacy_reanalysis(st, literature, literatures, evidence_store, raw_dir, extract_document_text, llm_call, save_records, save_evidence, locale="en", *, research_topic="", research_context=""):
    labels = _LABELS[locale]
    evidence = evidence_store.get(str(literature.get("id", "")), []) if isinstance(evidence_store, dict) else []
    if evidence:
        st.caption(f"{labels['evidence']}: {len(evidence)}")
        st.json(evidence[:8])
    file_path = str(literature.get("file_path", "") or "")
    if not file_path or not os.path.isfile(file_path):
        return
    if st.button(labels["reanalyze"], key=f"literature_reanalyze_{literature.get('id')}"):
        try:
            raw = Path(file_path).read_bytes()
            text = extract_document_text(raw, file_path)
        except OSError:
            text = "Parse failed: source file could not be read."
        if is_document_parse_error(text):
            st.warning(labels["reanalyze_failed"])
            return
        result = analyze_literature_document(
            text, literature.get("title", ""), llm_call, locale=locale,
            research_topic=research_topic, research_context=research_context,
        )
        updated, new_store, success = apply_analysis_to_literature_record(literature, evidence_store, result)
        if not success:
            st.warning(failure_message(result, locale, reanalysis=True))
            return
        literature.update(updated)
        save_records(literatures)
        save_evidence(new_store)
        st.success(labels["reanalyze_success"])
        st.rerun()
