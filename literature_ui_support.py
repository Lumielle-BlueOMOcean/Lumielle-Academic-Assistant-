"""Localized shared literature import and legacy re-analysis UI."""

from __future__ import annotations

import os
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


def _import_one(title, text, source, link, raw_bytes, extension, literatures, evidence_store, raw_dir, llm_call, locale):
    literature_id = str(uuid.uuid4())
    filename = title if os.path.splitext(title)[1] else f"{title}{extension}"
    file_path = ""
    try:
        file_path = save_literature_source(raw_dir, literature_id, filename, raw_bytes)
    except Exception:
        file_path = ""
    record = new_literature_record(literature_id, title, text, source=source, link=link, file_path=file_path)
    result = analyze_literature_document(text, title, llm_call, locale=locale)
    updated, new_store, success = apply_analysis_to_literature_record(record, evidence_store, result)
    literatures.append(updated)
    return new_store, success, result


def render_literature_ingestion(st, literatures, evidence_store, raw_dir, extract_upload_text, llm_call, save_records, save_evidence, locale="en"):
    labels = _LABELS[locale]
    st.subheader(labels["heading"])
    uploads = st.file_uploader(labels["upload"], type=["pdf", "docx", "txt"], accept_multiple_files=True, key="literature_full_uploads")
    if uploads and st.button(labels["import"], key="literature_full_import"):
        added = 0
        progress = st.progress(0)
        for index, upload in enumerate(uploads):
            text = extract_upload_text(upload)
            if is_document_parse_error(text):
                st.warning(f"{labels['error']}: {upload.name}. {text}")
            else:
                with st.spinner(upload.name):
                    evidence_store, success, result = _import_one(
                        upload.name, text, "Local Upload", "", upload.getvalue(),
                        Path(upload.name).suffix or ".txt", literatures, evidence_store, raw_dir, llm_call, locale,
                    )
                if success:
                    st.success(labels["success"].format(chunks=result["chunks_total"], evidence=len(result["evidence"])))
                else:
                    st.warning(labels["unavailable"])
                added += 1
            progress.progress((index + 1) / len(uploads))
        save_records(literatures)
        save_evidence(evidence_store)
        st.success(labels["added"].format(count=added))
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
                )
                save_records(literatures)
                save_evidence(evidence_store)
                if success:
                    st.success(labels["success"].format(chunks=result["chunks_total"], evidence=len(result["evidence"])))
                else:
                    st.warning(labels["unavailable"])
                st.rerun()


def render_legacy_reanalysis(st, literature, literatures, evidence_store, raw_dir, extract_document_text, llm_call, save_records, save_evidence, locale="en"):
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
        result = analyze_literature_document(text, literature.get("title", ""), llm_call, locale=locale)
        updated, new_store, success = apply_analysis_to_literature_record(literature, evidence_store, result)
        if not success:
            st.warning(labels["reanalyze_failed"])
            return
        literature.update(updated)
        save_records(literatures)
        save_evidence(new_store)
        st.success(labels["reanalyze_success"])
        st.rerun()
