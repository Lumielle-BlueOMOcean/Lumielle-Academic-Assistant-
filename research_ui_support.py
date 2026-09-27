"""Localized Streamlit controls for the shared Research Fact Layer."""

from __future__ import annotations

import json
import uuid

import pandas as pd

from document_support import is_document_parse_error
from research_support import (
    PRESET_SECTIONS,
    accept_smart_inbox_facts,
    add_custom_section,
    create_default_research_base,
    migrate_research_base,
    normalize_research_source,
    parse_classification_suggestion,
    parse_research_source,
    remove_custom_section,
)
from writing_support import LLMOutputError


_TEXT = {
    "en": {
        "sections": "Research sections", "inbox": "Smart Research Inbox",
        "choose": "Section", "empty": "No facts yet. Add a note or process a source.",
        "grounding": "Allow this section's relevant facts in chapter writing",
        "grounding_help": "When enabled, relevant parsed facts may enter selected chapter prompts; the entire section is not sent to every chapter. This section still informs research planning and the logic outline when disabled. More injected material is not always better; unrelated facts can add repetition, conflict, and context noise.",
        "file": "Upload a research source", "paste": "Paste or type research text", "title": "Source title",
        "parse_file": "Parse uploaded source", "parse_text": "Parse pasted text", "title_module": "Fact title",
        "content": "Fact content", "tags": "Tags (comma-separated)", "save": "Save section changes",
        "add": "Add fact manually", "new_title": "New fact title", "new_content": "New fact content",
        "delete": "Delete fact", "binding": "Chapter binding", "chapters": "Bound chapters",
        "binding_modes": {"auto": "Auto relevance", "global": "Global constraint/fact", "chapters": "Specific chapters"},
        "custom_name": "New custom section name", "custom_grounding": "Allow relevant facts from this section in chapter writing",
        "create_custom": "Create custom section", "delete_custom": "Delete custom section",
        "smart_advisory": "Classification is an AI suggestion only. Review and change the section, tags, role, and grounding setting before accepting.",
        "classify": "Parse and suggest a filing", "suggested": "Suggested section", "role": "Suggested role (editable)",
        "accept": "Accept filing and add facts", "none": "No suggestions available; choose a destination section.",
        "no_text": "Provide a file or paste text first.", "parse_failed": "No facts were added. Existing section content was preserved.",
        "parse_ok": "Complete source parsed into {count} facts across {chunks} ordered chunks.",
        "section_added": "Custom section created.", "section_removed": "Custom section and its facts deleted.",
        "manual_added": "Fact added.", "selected_grounding": "Allow this fact in chapter writing",
        "smart_file": "Upload unclassified research material", "smart_paste": "Paste unclassified research material",
        "accept_notice": "Filing accepted. You can still edit the facts in the destination section.",
        "error": "Source text could not be extracted.", "parse_button": "Parse source",
    },
    "zh": {
        "sections": "科研资料分区", "inbox": "智能归档 / 未分类科研资料",
        "choose": "选择分区", "empty": "暂无事实。可手动添加，或解析文件/粘贴文本。",
        "grounding": "允许本板块的相关事实参与正文 Grounding",
        "grounding_help": "开启后，本板块解析出的相关事实可在需要的章节进入正文生成上下文，并不代表整板块会加入每一章。即使关闭，本板块仍参与科研基座理解、逻辑大纲等规划环节。并非注入越多正文质量越高；无关资料可能增加重复、冲突和上下文噪声。",
        "file": "上传科研资料", "paste": "直接输入或粘贴科研文本", "title": "资料标题",
        "parse_file": "解析上传资料", "parse_text": "解析粘贴文本", "title_module": "事实标题",
        "content": "事实内容", "tags": "标签（逗号分隔）", "save": "保存本板块更改",
        "add": "手动添加事实", "new_title": "新事实标题", "new_content": "新事实内容",
        "delete": "删除事实", "binding": "章节绑定", "chapters": "绑定章节",
        "binding_modes": {"auto": "自动相关性选择", "global": "全局约束 / 事实", "chapters": "指定章节"},
        "custom_name": "自定义分区名称", "custom_grounding": "允许本分区相关事实参与正文 Grounding",
        "create_custom": "创建自定义分区", "delete_custom": "删除自定义分区",
        "smart_advisory": "AI 分类仅供建议。接受前请检查并可修改分区、标签、角色和正文 Grounding 设置。",
        "classify": "解析并建议归档位置", "suggested": "建议分区", "role": "建议用途（可编辑）",
        "accept": "接受归档并添加事实", "none": "暂无有效建议，请手动选择目标分区。",
        "no_text": "请先上传文件或粘贴文本。", "parse_failed": "未添加事实。原有分区内容保持不变。",
        "parse_ok": "完整资料已按顺序分为 {chunks} 块并解析出 {count} 条事实。",
        "section_added": "已创建自定义分区。", "section_removed": "自定义分区及其中事实已删除。",
        "manual_added": "已添加事实。", "selected_grounding": "允许此事实参与正文 Grounding",
        "smart_file": "上传未分类科研资料", "smart_paste": "粘贴未分类科研资料",
        "accept_notice": "已接受归档；仍可在目标分区编辑事实。",
        "error": "无法提取资料文本。", "parse_button": "解析资料",
    },
}


def _name(section, locale):
    return section.get("name_zh" if locale == "zh" else "name_en") or section.get("name") or section.get("id", "")


def _extract_source(upload, pasted_text, pasted_title, extract_upload_text, locale):
    if upload is not None:
        text = extract_upload_text(upload)
        if is_document_parse_error(text):
            return None, text
        source = normalize_research_source(text, upload.name)
        source["source_type"] = "file"
        return source, None
    if str(pasted_text or "").strip():
        return normalize_research_source(pasted_text, pasted_title or "Pasted research text"), None
    return None, None


def _parse_and_append(st, base, section_id, source, llm_call, locale):
    section = base["sections"][section_id]
    result = parse_research_source(
        source["text"], section_id, llm_call, locale=locale,
        source_id=source["source_id"],
        allow_writing_grounding=section.get("allow_writing_grounding", False),
    )
    if result["status"] != "ok":
        st.error(result.get("message") or _TEXT[locale]["parse_failed"])
        return False
    for fact in result["facts"]:
        fact["source_title"] = source["title"]
    section["modules"].extend(result["facts"])
    return result


def _render_section(st, base, section_id, llm_call, save_base, extract_upload_text, locale, chapter_nodes):
    text = _TEXT[locale]
    section = base["sections"][section_id]
    st.subheader(_name(section, locale))
    st.caption(text["grounding_help"])
    section["allow_writing_grounding"] = st.checkbox(
        text["grounding"], value=bool(section.get("allow_writing_grounding", False)), key=f"research_grounding_{section_id}"
    )

    upload = st.file_uploader(text["file"], type=["pdf", "docx", "txt"], key=f"research_upload_{section_id}")
    pasted_title = st.text_input(text["title"], key=f"research_title_{section_id}")
    pasted_text = st.text_area(text["paste"], height=120, key=f"research_paste_{section_id}")
    action = st.button(text["parse_button"], key=f"research_parse_{section_id}")
    if action:
        source, error = _extract_source(upload, pasted_text, pasted_title, extract_upload_text, locale)
        if error:
            st.error(error)
        elif not source:
            st.warning(text["no_text"])
        else:
            with st.spinner(text["parse_file"] if upload is not None else text["parse_text"]):
                parsed = _parse_and_append(st, base, section_id, source, llm_call, locale)
            if parsed:
                save_base(base)
                st.success(text["parse_ok"].format(count=len(parsed["facts"]), chunks=parsed["chunks_total"]))
                st.rerun()

    st.markdown("---")
    with st.expander(text["add"], expanded=False):
        title = st.text_input(text["new_title"], key=f"research_manual_title_{section_id}")
        content = st.text_area(text["new_content"], key=f"research_manual_content_{section_id}")
        if st.button(text["add"], key=f"research_manual_add_{section_id}"):
            if not title.strip() or not content.strip():
                st.warning(text["no_text"])
            else:
                section["modules"].append({
                    "id": str(uuid.uuid4()), "section": section_id, "title": title.strip(),
                    "type": "fact", "content": content, "tags": [], "source_id": "manual",
                    "source_chunk": 1, "allow_writing_grounding": section["allow_writing_grounding"],
                    "binding_mode": "auto", "chapter_ids": [],
                })
                save_base(base)
                st.success(text["manual_added"])
                st.rerun()

    modules = section.get("modules", [])
    if not modules:
        st.info(text["empty"])
    chapter_ids = [node.get("id") for node, _depth in chapter_nodes if node.get("id")]
    chapter_names = {node.get("id"): node.get("title", node.get("id")) for node, _depth in chapter_nodes if node.get("id")}
    for index, fact in enumerate(modules):
        with st.container(border=True):
            title = st.text_input(text["title_module"], value=fact.get("title", ""), key=f"research_fact_title_{section_id}_{index}")
            fact["title"] = title
            if fact.get("type") == "table":
                columns = fact.get("columns", []) or ["Column 1"]
                rows = fact.get("data", []) or [[""]]
                frame = pd.DataFrame(rows, columns=columns if len(columns) == len(rows[0]) else None)
                edited = st.data_editor(frame, num_rows="dynamic", use_container_width=True, key=f"research_fact_table_{section_id}_{index}")
                fact["columns"] = [str(value) for value in edited.columns]
                fact["data"] = edited.astype(str).values.tolist()
            else:
                fact["content"] = st.text_area(text["content"], value=fact.get("content", ""), key=f"research_fact_content_{section_id}_{index}")
            fact["tags"] = [tag.strip() for tag in st.text_input(text["tags"], value=", ".join(fact.get("tags", [])), key=f"research_fact_tags_{section_id}_{index}").split(",") if tag.strip()]
            fact["allow_writing_grounding"] = st.checkbox(text["selected_grounding"], value=bool(fact.get("allow_writing_grounding", section["allow_writing_grounding"])), key=f"research_fact_allow_{section_id}_{index}")
            mode = fact.get("binding_mode", "auto")
            mode_keys = ["auto", "global", "chapters"]
            selected_mode = st.selectbox(text["binding"], mode_keys, index=mode_keys.index(mode) if mode in mode_keys else 0, format_func=lambda value: text["binding_modes"][value], key=f"research_fact_binding_{section_id}_{index}")
            fact["binding_mode"] = selected_mode
            if selected_mode == "chapters":
                fact["chapter_ids"] = st.multiselect(text["chapters"], chapter_ids, default=[value for value in fact.get("chapter_ids", []) if value in chapter_ids], format_func=lambda value: chapter_names.get(value, value), key=f"research_fact_chapters_{section_id}_{index}")
            st.caption(f"Source: {fact.get('source_title', fact.get('source_id', 'manual'))} · chunk {fact.get('source_chunk', 1)}")
    if modules and st.button(text["save"], key=f"research_save_{section_id}"):
        save_base(base)
        st.success(text["save"])


def _render_inbox(st, base, llm_call, save_base, extract_upload_text, locale):
    text = _TEXT[locale]
    st.subheader(text["inbox"])
    st.info(text["smart_advisory"])
    upload = st.file_uploader(text["smart_file"], type=["pdf", "docx", "txt"], key="research_inbox_upload")
    title = st.text_input(text["title"], key="research_inbox_title")
    pasted = st.text_area(text["smart_paste"], height=140, key="research_inbox_text")
    if st.button(text["classify"], key="research_inbox_parse"):
        source, error = _extract_source(upload, pasted, title, extract_upload_text, locale)
        if error:
            st.error(error)
        elif not source:
            st.warning(text["no_text"])
        else:
            parsed = parse_research_source(source["text"], "legacy_unclassified", llm_call, locale=locale, source_id=source["source_id"], allow_writing_grounding=False)
            if parsed["status"] != "ok":
                st.error(parsed.get("message") or text["parse_failed"])
            else:
                fact_preview = [{"title": item["title"], "content": item["content"], "tags": item["tags"]} for item in parsed["facts"]]
                section_names = {key: _name(section, locale) for key, section in base["sections"].items() if key != "legacy_unclassified"}
                prompt = (
                    "请根据已解析事实，建议最适合的科研资料分区、关键词标签和用途。分类仅供用户审核，不得移动资料。"
                    if locale == "zh" else
                    "Given these parsed facts, suggest the best research section, concise tags, and role. This is advisory only; do not move or rewrite the facts."
                )
                prompt += "\nValid section IDs: " + ", ".join(section_names) + "\nFacts: " + json.dumps(fact_preview, ensure_ascii=False)
                try:
                    response = llm_call(prompt, max_tokens=500)
                    suggestion = parse_classification_suggestion(response, section_names)
                except LLMOutputError:
                    suggestion = {"section": "", "tags": [], "role": ""}
                st.session_state["research_inbox_pending"] = {"source": source, "facts": parsed["facts"], "suggestion": suggestion}
                st.rerun()

    pending = st.session_state.get("research_inbox_pending")
    if not isinstance(pending, dict):
        return
    suggestion = pending.get("suggestion", {})
    sections = {key: section for key, section in base["sections"].items() if key != "legacy_unclassified"}
    section_ids = list(sections)
    suggested_id = suggestion.get("section")
    default_index = section_ids.index(suggested_id) if suggested_id in section_ids else 0
    st.markdown(f"**{text['suggested']}:** { _name(sections[suggested_id], locale) if suggested_id in sections else text['none'] }")
    selected = st.selectbox(text["choose"], section_ids, index=default_index, format_func=lambda value: _name(sections[value], locale), key="research_inbox_destination")
    tags = st.text_input(text["tags"], value=", ".join(suggestion.get("tags", [])), key="research_inbox_tags")
    role = st.text_input(text["role"], value=suggestion.get("role", ""), key="research_inbox_role")
    allow_grounding = st.checkbox(text["custom_grounding"], value=bool(sections[selected].get("allow_writing_grounding", False)), key="research_inbox_grounding")
    st.caption(f"{pending.get('source', {}).get('title', '')} · {len(pending.get('facts', []))} parsed facts · {role}")
    if st.button(text["accept"], key="research_inbox_accept"):
        target = sections[selected]
        tag_values = [tag.strip() for tag in tags.split(",") if tag.strip()]
        target["modules"].extend(accept_smart_inbox_facts(
            pending, selected, tag_values, role, allow_grounding,
        ))
        save_base(base)
        st.session_state.pop("research_inbox_pending", None)
        st.success(text["accept_notice"])
        st.rerun()


def render_research_foundation(st, load_base, save_base, extract_upload_text, llm_call, locale, chapter_nodes):
    """Render the same section/fact pipeline in English and Chinese."""
    text = _TEXT[locale]
    st.header("Research Foundation" if locale == "en" else "科研基座")
    base = migrate_research_base(load_base() or create_default_research_base())
    definitions = {item["id"]: item for item in PRESET_SECTIONS}
    keys = list(base["sections"])
    display = lambda key: _name(base["sections"][key], locale)
    tabs = st.tabs([text["sections"], text["inbox"]])
    with tabs[0]:
        custom_name = st.text_input(text["custom_name"], key="research_custom_name")
        custom_allow = st.checkbox(text["custom_grounding"], key="research_custom_allow")
        if st.button(text["create_custom"], key="research_custom_create"):
            try:
                base, _section_id = add_custom_section(base, custom_name, custom_allow)
                save_base(base)
                st.success(text["section_added"])
                st.rerun()
            except ValueError as exc:
                st.warning(str(exc))
        custom_ids = [key for key, section in base["sections"].items() if section.get("kind") == "custom"]
        if custom_ids:
            custom_to_delete = st.selectbox(text["delete_custom"], custom_ids, format_func=display, key="research_custom_delete_sel")
            if st.button(text["delete_custom"], key="research_custom_delete"):
                base, removed = remove_custom_section(base, custom_to_delete)
                if removed:
                    save_base(base)
                    st.success(text["section_removed"])
                    st.rerun()
        selected = st.selectbox(text["choose"], keys, format_func=display, key="research_section_select")
        _render_section(st, base, selected, llm_call, save_base, extract_upload_text, locale, chapter_nodes)
    with tabs[1]:
        _render_inbox(st, base, llm_call, save_base, extract_upload_text, locale)
