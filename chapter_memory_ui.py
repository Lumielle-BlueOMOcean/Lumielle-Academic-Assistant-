"""Bilingual, low-configuration chapter-memory controls for Streamlit."""

from chapter_memory_store import read_chapter_memory, set_memory_item_confirmation
from chapter_memory_support import MemoryBuildError, _leaf_nodes, refresh_chapter_memory_snapshot, structured_memory_llm_call
from chapter_consistency import classify_pending_review_reason


_TEXT = {
    "en": {
        "title": "Chapter memory",
        "status": {
            "unwritten": "No saved chapter text yet.",
            "memory_missing": "Draft saved; memory has not been built yet.",
            "legacy_unverified": "Legacy memory is available but has not been verified.",
            "current": "Memory matches the saved chapter.",
            "stale": "The chapter has changed; rebuild its memory.",
            "invalid": "Memory could not be verified; rebuild it.",
        },
        "legacy": "Legacy summary (unverified)",
        "summary": "Chapter summary",
        "items": "Key academic information",
        "source": "Source passage",
        "confirm": "Use this information as a writing constraint",
        "confirm_help": "This marks a consistency preference for later writing; it does not independently verify academic truth.",
        "rebuild": "Rebuild this chapter's memory",
        "batch": "Build memory for existing chapters",
        "batch_button": "Rebuild missing or outdated memories",
        "batch_done": "Memory rebuild finished: {ok} updated, {failed} failed, {skipped} already current.",
        "progress": "Rebuilding chapter {current}/{total}: {title}",
        "failed": "Memory update did not finish. The saved draft and previous memory were preserved; try again later.",
        "pending": "Memory is waiting to be rebuilt for this saved chapter.",
        "failure_detail": "Safe diagnostic: {category} ({code}) at {stage}{chunk}.",
        "empty": "The saved chapter has no text to analyze.",
        "updated": "Chapter memory updated.",
        "reused": "This saved chapter already has current memory.",
        "preview": "Expected context for this chapter",
        "actual": "Most recent generation actually used",
        "actual_none": "No actual chapter-generation request is recorded in this session yet.",
        "prompt_hash": "Prompt hash",
        "preview_selected": "Selected memory items: {count}",
        "preview_missing": "Chapters needing memory: {count}",
        "preview_sources": "Context chapters: {chapters}",
        "preview_omitted": "Omitted for length: {chapters}",
        "preview_none": "None",
        "no_items": "No source-anchored memory items have been extracted yet.",
    },
    "zh": {
        "title": "章节记忆",
        "status": {
            "unwritten": "尚无已保存正文。",
            "memory_missing": "正文已保存，尚未建立记忆。",
            "legacy_unverified": "存在旧版记忆，但尚未验证。",
            "current": "记忆与已保存正文一致。",
            "stale": "正文已修改，需要更新记忆。",
            "invalid": "记忆无法核验，请重新建立。",
        },
        "legacy": "旧版记忆（尚未验证）",
        "summary": "本章摘要",
        "items": "关键学术信息",
        "source": "正文来源",
        "confirm": "将此信息作为后续写作约束",
        "confirm_help": "这表示后续写作时需要核对该信息，不代表学术真实性已经过外部验证。",
        "rebuild": "重新建立本章记忆",
        "batch": "为已有章节建立记忆",
        "batch_button": "重建缺失或过期的章节记忆",
        "batch_done": "记忆重建完成：更新 {ok} 章，失败 {failed} 章，已有记忆 {skipped} 章。",
        "progress": "正在重建第 {current}/{total} 章：{title}",
        "failed": "记忆更新未完成。已保留已保存正文和此前记忆，请稍后重试。",
        "pending": "本章正文已保存，记忆待重建。可使用下方按钮单独重新提取。",
        "failure_detail": "安全诊断：{stage} 阶段，类别 {category}，状态 {code}{chunk}。",
        "empty": "当前没有可分析的已保存正文。",
        "updated": "本章记忆已更新。",
        "reused": "已保存正文已有对应版本的记忆。",
        "preview": "本章预计参考内容",
        "actual": "最近一次生成实际使用的内容",
        "actual_none": "本会话尚无实际章节生成请求记录。",
        "prompt_hash": "Prompt 哈希",
        "preview_selected": "选用的记忆条目：{count}",
        "preview_missing": "需要建立或更新记忆的章节：{count}",
        "preview_sources": "本次参考章节：{chapters}",
        "preview_omitted": "因篇幅限制未加入：{chapters}",
        "preview_none": "无",
        "no_items": "尚未提取到有原文依据的记忆条目。",
    },
}

_REVIEW_TEXT = {
    "en": {
        "title": "Review generated chapter candidates",
        "pass_warning": "This candidate needs your review before it can replace the saved chapter.",
        "review_unavailable": "The consistency review did not complete. Inspect the candidate before saving.",
        "reason_labels": {
            "deterministic_reference_error": "Deterministic citation error",
            "deterministic_fact_conflict": "Confirmed-fact conflict",
            "review_warning": "Review completed with a warning; manual review is required",
            "review_invalid_json": "Review returned invalid JSON",
            "review_empty_output": "Review returned empty output",
            "review_truncated": "Review output was truncated",
            "review_timeout": "Review timed out",
            "review_rate_limited": "Review was rate limited",
            "review_connection_error": "Review connection failed",
            "review_server_error": "Review service error",
            "review_provider_unavailable": "Review provider was temporarily unavailable",
            "review_context_budget": "Review context exceeded the safe budget",
            "review_content_filtered": "Review was interrupted by content filtering",
            "review_provider_error": "Review provider failed",
            "review_incomplete": "Review did not complete",
            "review_completed": "Review completed; manual confirmation is required",
        },
        "rereview": "Re-review this candidate only",
        "rereview_done": "Review completed. This candidate is still awaiting your explicit save decision.",
        "rereview_incomplete": "Review did not complete; the candidate and previous draft are unchanged.",
        "issues": "Possible issue",
        "source": "Related context",
        "save": "Save this candidate",
        "keep": "Keep the previous draft",
        "saved": "Candidate saved.",
        "memory_failed": "The draft was saved, but its memory was not updated. You can rebuild it from Chapter memory.",
        "save_failed": "The candidate could not be saved; the previous draft remains unchanged.",
        "save_consistency_failed": "The candidate was not saved, and its citation snapshot could not be restored. Check this chapter's references before export; the editor was not refreshed.",
        "kept": "The previous draft was kept.",
        "candidate": "Candidate text",
    },
    "zh": {
        "title": "审阅生成候选正文",
        "pass_warning": "候选正文需要你审阅后才能替换已保存章节。",
        "review_unavailable": "一致性检查未完成，请检查候选正文后再决定是否保存。",
        "reason_labels": {
            "deterministic_reference_error": "确定性引用错误",
            "deterministic_fact_conflict": "与已确认事实存在冲突",
            "review_warning": "审阅已完成但有警告，需要人工核对",
            "review_invalid_json": "审阅返回的 JSON 格式无效",
            "review_empty_output": "审阅未返回内容",
            "review_truncated": "审阅输出被截断",
            "review_timeout": "审阅请求超时",
            "review_rate_limited": "审阅请求受到频率限制",
            "review_connection_error": "审阅连接失败",
            "review_server_error": "审阅服务异常",
            "review_provider_unavailable": "审阅服务暂时不可用",
            "review_context_budget": "审阅上下文超过安全长度",
            "review_content_filtered": "审阅被内容过滤中断",
            "review_provider_error": "审阅服务调用失败",
            "review_incomplete": "审阅未完成",
            "review_completed": "审阅已完成，仍需你确认候选正文",
        },
        "rereview": "仅重新审阅本章候选",
        "rereview_done": "审阅已完成。候选正文仍等待你明确决定是否保存。",
        "rereview_incomplete": "审阅仍未完成；候选正文和此前正文均未改变。",
        "issues": "可能需要核对",
        "source": "相关内容来源",
        "save": "保存这份候选正文",
        "keep": "保留此前正文",
        "saved": "候选正文已保存。",
        "memory_failed": "正文已保存，但记忆未能更新；可在“章节记忆”中重试。",
        "save_failed": "候选正文保存失败，原有正文保持不变。",
        "save_consistency_failed": "候选正文未保存，且引用快照无法恢复。请先核对本章引用再导出；编辑器未刷新。",
        "kept": "已保留此前正文。",
        "candidate": "候选正文",
    },
}


def _locale_text(locale):
    return _TEXT["zh" if locale == "zh" else "en"]


def render_context_manifest_summary(st, locale="en"):
    """Show the current preview separately from the last successful generation."""
    text = _locale_text(locale)
    preview = st.session_state.get("chapter_context_preview_manifest") or {}
    actual = st.session_state.get("last_generation_context_manifest") or {}

    def render_manifest(label, manifest, *, actual_generation=False):
        st.markdown(f"#### 👁️ {label}")
        if actual_generation and not manifest:
            st.caption(text["actual_none"])
            return
        st.caption(text["preview_selected"].format(count=len(manifest.get("selected_memory_ids", []))))
        st.caption(text["preview_missing"].format(count=len(manifest.get("missing_or_stale_chapters", []))))
        selected_titles = [row.get("title") or row.get("chapter_id", "") for row in manifest.get("selected_chapters", [])]
        missing_titles = [row.get("title") or row.get("chapter_id", "") for row in manifest.get("missing_or_stale_chapters", [])]
        omitted_titles = [row.get("title") or row.get("chapter_id", "") for row in manifest.get("omitted_chapters", [])]
        separator = "、" if locale == "zh" else ", "
        st.caption(text["preview_sources"].format(chapters=separator.join(selected_titles) if selected_titles else text["preview_none"]))
        st.caption(text["preview_omitted"].format(chapters=separator.join(omitted_titles) if omitted_titles else text["preview_none"]))
        if missing_titles:
            prefix = "缺少或需要更新记忆的章节：" if locale == "zh" else "Chapters missing current memory: "
            st.caption(prefix + separator.join(missing_titles))
        if actual_generation and manifest.get("prompt_hash"):
            st.caption(f"{text['prompt_hash']}: {manifest['prompt_hash']}")

    render_manifest(text["preview"], preview)
    render_manifest(text["actual"], actual, actual_generation=True)


def render_chapter_memory_panel(
    st,
    *,
    data_dir,
    chapter_id,
    draft_text,
    legacy_summary,
    llm_call,
    locale="en",
    outline=None,
    drafts=None,
    legacy_summaries=None,
):
    """Render current status, provenance, confirmations, and explicit rebuild actions."""
    text = _locale_text(locale)
    state = read_chapter_memory(data_dir, str(chapter_id), draft_text, legacy_summary=legacy_summary)
    st.markdown(f"### 🧠 {text['title']}")
    st.caption(text["status"].get(state["status"], text["status"]["invalid"]))
    memory_failure = st.session_state.get(f"chapter_memory_failure_{chapter_id}")
    if isinstance(memory_failure, dict):
        st.warning(text["pending"])
        chunk_index = memory_failure.get("chunk_index")
        chunks_total = memory_failure.get("chunks_total")
        chunk = f" ({chunk_index}/{chunks_total})" if isinstance(chunk_index, int) and isinstance(chunks_total, int) else ""
        st.caption(text["failure_detail"].format(
            code=str(memory_failure.get("code", "memory_update_failed"))[:80],
            category=str(memory_failure.get("category", "other"))[:80],
            stage=str(memory_failure.get("stage", "extraction"))[:80],
            chunk=chunk,
        ))
    if state["status"] == "legacy_unverified" and state.get("legacy_summary"):
        st.caption(f"{text['legacy']}: {state['legacy_summary']}")
    if state["status"] == "current":
        with st.expander(text["summary"], expanded=True):
            st.write(state["narrative_summary"])
        with st.expander(text["items"], expanded=False):
            if not state["items"]:
                st.caption(text["no_items"])
            for item in state["items"]:
                st.markdown(f"**{item['statement']}**")
                st.caption(f"{text['source']}: “{item['source_quote']}”")
                key = f"chapter_memory_confirm_{item['id']}"
                if key not in st.session_state:
                    st.session_state[key] = bool(item["confirmed_for_writing"])
                selected = st.checkbox(
                    text["confirm"],
                    key=key,
                    help=text["confirm_help"],
                )
                if bool(selected) != bool(item["confirmed_for_writing"]):
                    set_memory_item_confirmation(data_dir, item["id"], selected)

    if st.button(text["rebuild"], key=f"chapter_memory_rebuild_{chapter_id}", disabled=not bool(str(draft_text or "").strip())):
        try:
            with st.spinner(text["progress"].format(current=1, total=1, title="")):
                result = refresh_chapter_memory_snapshot(
                    data_dir, str(chapter_id), draft_text,
                    structured_memory_llm_call(llm_call, locale), locale=locale,
                )
        except MemoryBuildError as exc:
            st.session_state[f"chapter_memory_failure_{chapter_id}"] = exc.safe_details()
            st.warning(text["failed"])
            st.caption(text["failure_detail"].format(
                code=exc.error_code, stage=exc.stage,
                category=exc.safe_details().get("category", "other"),
                chunk=f" ({exc.chunk_index}/{exc.chunks_total})" if exc.chunk_index and exc.chunks_total else "",
            ))
        except Exception:
            st.session_state[f"chapter_memory_failure_{chapter_id}"] = {"code": "memory_update_failed", "stage": "other"}
            st.warning(text["failed"])
        else:
            st.session_state.pop(f"chapter_memory_failure_{chapter_id}", None)
            st.success(text["reused"] if result["reused"] else text["updated"])

    render_context_manifest_summary(st, locale=locale)

    if outline and isinstance(drafts, dict):
        with st.expander(text["batch"], expanded=False):
            if st.button(text["batch_button"], key=f"chapter_memory_batch_rebuild_{chapter_id}"):
                leaves = _leaf_nodes(outline)
                candidates = []
                skipped = 0
                for node in leaves:
                    nid = str(node.get("id"))
                    body = drafts.get(node.get("id"), drafts.get(nid, ""))
                    if body and str(body).strip():
                        snapshot = read_chapter_memory(
                            data_dir, nid, body,
                            legacy_summary=(legacy_summaries or {}).get(nid),
                        )
                        if snapshot["status"] == "current":
                            skipped += 1
                        else:
                            candidates.append((nid, node.get("title", ""), body))
                ok = failed = 0
                progress = st.progress(0.0)
                status = st.empty()
                total = len(candidates)
                for index, (nid, title, body) in enumerate(candidates, start=1):
                    status.info(text["progress"].format(current=index, total=total, title=title))
                    try:
                        refresh_chapter_memory_snapshot(
                            data_dir, nid, body,
                            structured_memory_llm_call(llm_call, locale), locale=locale,
                        )
                    except Exception:
                        failed += 1
                    else:
                        st.session_state.pop(f"chapter_memory_failure_{nid}", None)
                        ok += 1
                    progress.progress(index / total if total else 1.0)
                summary = text["batch_done"].format(ok=ok, failed=failed, skipped=skipped)
                status.warning(summary) if failed else status.success(summary)


def render_pending_candidate_reviews(st, on_save, locale="en", on_re_review=None):
    """Present non-passing generated candidates for an explicit keep-or-save choice."""
    language = "zh" if locale == "zh" else "en"
    text = _REVIEW_TEXT[language]
    pending = st.session_state.get("pending_chapter_candidates", {})
    if not isinstance(pending, dict) or not pending:
        return
    st.markdown(f"### 🔎 {text['title']}")
    for chapter_id, candidate in list(pending.items()):
        title = candidate.get("title", str(chapter_id))
        with st.expander(title, expanded=True):
            review = candidate.get("consistency", {})
            if not review.get("completed") and on_re_review is not None:
                if st.button(text["rereview"], key=f"pending_candidate_rereview_{chapter_id}"):
                    try:
                        updated_review = on_re_review(chapter_id, candidate)
                    except Exception:
                        updated_review = {"status": "needs_review", "completed": False, "hard_conflicts": [], "issues": [], "failure_code": "provider_error"}
                    candidate["consistency"] = updated_review
                    review = updated_review
                    if updated_review.get("completed"):
                        st.success(text["rereview_done"])
                    else:
                        st.warning(text["rereview_incomplete"])
            reason_code = classify_pending_review_reason(review)
            reason_label = text["reason_labels"].get(reason_code, text["reason_labels"]["review_incomplete"])
            if review.get("completed"):
                st.warning(text["pass_warning"])
            else:
                st.warning(text["review_unavailable"])
            http_status = review.get("http_status")
            if isinstance(http_status, int) and not isinstance(http_status, bool) and 100 <= http_status <= 599:
                reason_label += f" (HTTP {http_status})"
            st.caption(reason_label)
            for issue in review.get("hard_conflicts", []) + review.get("issues", []):
                st.markdown(f"**{text['issues']}:** {issue.get('reason', issue.get('code', ''))}")
                if issue.get("source"):
                    st.caption(f"{text['source']}: {issue['source']}")
            st.text_area(text["candidate"], value=candidate.get("text", ""), height=240, disabled=True, key=f"pending_candidate_text_{chapter_id}")
            save_col, keep_col = st.columns(2)
            with save_col:
                if st.button(text["save"], key=f"pending_candidate_save_{chapter_id}", type="primary"):
                    try:
                        result = on_save(chapter_id, candidate)
                    except Exception:
                        result = {"saved": False, "memory_updated": False}
                    if not result.get("saved"):
                        st.error(text["save_consistency_failed"] if result.get("consistency_warning") else text["save_failed"])
                    else:
                        pending.pop(chapter_id, None)
                        st.success(text["saved"])
                        if result.get("memory_error"):
                            st.warning(text["memory_failed"])
                        st.rerun()
            with keep_col:
                if st.button(text["keep"], key=f"pending_candidate_keep_{chapter_id}"):
                    pending.pop(chapter_id, None)
                    st.info(text["kept"])
