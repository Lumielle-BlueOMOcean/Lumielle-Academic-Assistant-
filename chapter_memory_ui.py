"""Bilingual, low-configuration chapter-memory controls for Streamlit."""

from chapter_memory_store import read_chapter_memory, set_memory_item_confirmation
from chapter_memory_support import MemoryBuildError, _leaf_nodes, refresh_chapter_memory_snapshot, structured_memory_llm_call


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
        except MemoryBuildError:
            st.warning(text["failed"])
        except Exception:
            st.warning(text["failed"])
        else:
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
                        ok += 1
                    progress.progress(index / total if total else 1.0)
                summary = text["batch_done"].format(ok=ok, failed=failed, skipped=skipped)
                status.warning(summary) if failed else status.success(summary)


def render_pending_candidate_reviews(st, on_save, locale="en"):
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
            if review.get("completed"):
                st.warning(text["pass_warning"])
            else:
                st.warning(text["review_unavailable"])
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
