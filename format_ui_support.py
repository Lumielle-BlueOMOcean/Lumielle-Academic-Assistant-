"""Shared Streamlit controls for preset selection, sharing, and validated format creation."""

from __future__ import annotations

import json
from pathlib import Path

from format_support import (
    FormatSpecError,
    delete_user_preset,
    load_builtin_presets,
    load_user_presets,
    parse_format_spec,
    save_user_preset,
)
from writing_support import LLMOutputError, require_valid_llm_output


_LABELS = {
    "en": {
        "preset": "FormatSpec preset", "select": "Select a formatting preset", "invalid_store": "Saved custom presets could not be read; the existing file was left unchanged.",
        "manage": "Import / share / manage presets", "json_file": "Upload a FormatSpec JSON file", "json_paste": "Or paste FormatSpec JSON",
        "import": "Validate and import preset", "export": "Download selected preset JSON", "rename": "Rename selected custom preset",
        "rename_button": "Save renamed preset", "delete": "Delete selected custom preset", "built_in": "Built-in presets cannot be renamed or deleted.",
        "natural": "Create a FormatSpec from instructions", "natural_input": "Describe the desired layout", "generate": "Generate and validate preview",
        "pending": "Validated FormatSpec preview", "save_name": "Name this custom preset", "confirm": "Save validated preset",
        "bad_json": "Preset invalid", "imported": "Preset imported and validated.", "saved": "Preset saved.", "deleted": "Custom preset deleted.",
        "generated": "The model returned a valid FormatSpec. Review it before saving.", "generation_failed": "FormatSpec generation failed validation; the selected preset remains active.",
        "import_source": "Use the upload or paste field; invalid JSON never changes the active preset.",
    },
    "zh": {
        "preset": "FormatSpec 排版预设", "select": "选择排版预设", "invalid_store": "自定义预设文件无法读取；原文件保持不变。",
        "manage": "导入 / 分享 / 管理预设", "json_file": "上传 FormatSpec JSON 文件", "json_paste": "或直接粘贴 FormatSpec JSON",
        "import": "校验并导入预设", "export": "下载当前预设 JSON", "rename": "重命名当前自定义预设",
        "rename_button": "保存重命名预设", "delete": "删除当前自定义预设", "built_in": "内置预设不可重命名或删除。",
        "natural": "根据文字要求创建 FormatSpec", "natural_input": "描述希望使用的排版规则", "generate": "生成并校验预览",
        "pending": "已校验的 FormatSpec 预览", "save_name": "自定义预设名称", "confirm": "保存已校验预设",
        "bad_json": "预设无效", "imported": "预设已校验并导入。", "saved": "预设已保存。", "deleted": "自定义预设已删除。",
        "generated": "模型已生成有效 FormatSpec，请检查后再保存。", "generation_failed": "FormatSpec 生成或校验失败，当前有效预设保持不变。",
        "import_source": "可上传或粘贴 JSON；无效 JSON 不会改变当前有效预设。",
    },
}


def _generation_prompt(instructions, locale):
    schema = {
        "schema_version": 1, "name": "Custom Academic", "page": {
            "size": "A4", "margins_cm": {"top": 2.54, "bottom": 2.54, "left": 3.17, "right": 3.17},
            "page_number": {"enabled": True, "position": "center", "start": 1},
        },
        "body": {"font_latin": "Times New Roman", "font_cjk": "宋体", "font_size_pt": 12, "alignment": "justify", "line_spacing": 1.5, "first_line_indent_chars": 2, "space_before_pt": 0, "space_after_pt": 6},
        "headings": {
            "mode": "controlled",
            "h1": {"font_latin": "Arial", "font_cjk": "黑体", "font_size_pt": 16, "alignment": "center", "bold": True, "space_before_pt": 18, "space_after_pt": 12, "keep_with_next": True, "page_break_before": False},
            "h2": {"font_latin": "Arial", "font_cjk": "黑体", "font_size_pt": 14, "alignment": "left", "bold": True, "space_before_pt": 14, "space_after_pt": 8, "keep_with_next": True, "page_break_before": False},
            "h3": {"font_latin": "Arial", "font_cjk": "黑体", "font_size_pt": 12, "alignment": "left", "bold": True, "space_before_pt": 10, "space_after_pt": 6, "keep_with_next": True, "page_break_before": False},
        },
        "table": {"style": "three_line", "font_size_pt": 10.5, "caption_prefix": "", "caption_enabled": True},
        "figure": {"caption_prefix": "", "width_percent": 85, "caption_mode": "numbered"},
    }
    task = (
        '根据用户要求生成 FormatSpec JSON。page_number.start 为 1–32767 的整数；headings.mode 为 "controlled" 或 "legacy"；各级标题可设置 keep_with_next、page_break_before；table.style 只能为 "grid" 或 "three_line"；figure.width_percent 为 10–100 的整数，figure.caption_mode 为 "numbered" 或 "legacy"。只输出给定 schema 允许的 JSON，不添加代码、路径或未定义字段。'
        if locale == "zh" else
        'Generate a FormatSpec JSON from the user\'s requirements. page_number.start is an integer from 1 to 32767; headings.mode is "controlled" or "legacy"; each heading supports keep_with_next and page_break_before; table.style is "grid" or "three_line"; figure.width_percent is an integer from 10 to 100; figure.caption_mode is "numbered" or "legacy". Return only JSON using the supplied schema; do not add code, paths, or unsupported fields.'
    )
    return f"{task}\nSchema example:\n{json.dumps(schema, ensure_ascii=False)}\nUser requirements:\n{instructions}"


def render_format_spec_controls(st, *, locale, preset_directory, custom_store_path, llm_call, saved_format_prompt=""):
    labels = _LABELS[locale]
    builtin = load_builtin_presets(preset_directory)
    try:
        custom = load_user_presets(custom_store_path)
    except FormatSpecError as exc:
        custom = {}
        st.error(f"{labels['invalid_store']} {exc}")
    presets = {**builtin, **custom}
    if not presets:
        raise FormatSpecError("No valid built-in FormatSpec presets are installed.")

    default_name = "Legacy Compatible" if "Legacy Compatible" in presets else next(iter(presets))
    selected_name = st.selectbox(labels["select"], list(presets), index=list(presets).index(default_name), key=f"format_spec_choice_{locale}")
    selected_spec = presets[selected_name]
    st.caption(f"{selected_name} · schema v{selected_spec['schema_version']}")

    with st.expander(labels["manage"], expanded=False):
        st.caption(labels["import_source"])
        upload = st.file_uploader(labels["json_file"], type=["json"], key=f"format_json_upload_{locale}")
        pasted = st.text_area(labels["json_paste"], height=140, key=f"format_json_paste_{locale}")
        if st.button(labels["import"], key=f"format_json_import_{locale}"):
            try:
                content = upload.getvalue().decode("utf-8-sig") if upload is not None else pasted
                spec = parse_format_spec(content)
                save_user_preset(custom_store_path, spec["name"], spec, builtin_names=builtin)
                st.success(labels["imported"])
                st.rerun()
            except (UnicodeDecodeError, FormatSpecError, OSError) as exc:
                st.error(f"{labels['bad_json']}: {exc}")
        st.download_button(
            labels["export"], data=json.dumps(selected_spec, ensure_ascii=False, indent=2),
            file_name=f"{selected_name.replace(' ', '_')}.json", mime="application/json", key=f"format_json_export_{locale}",
        )
        if selected_name in custom:
            new_name = st.text_input(labels["rename"], value=selected_name, key=f"format_rename_{locale}")
            if st.button(labels["rename_button"], key=f"format_rename_save_{locale}"):
                try:
                    save_user_preset(custom_store_path, new_name, selected_spec, builtin_names=builtin)
                    if new_name.strip() != selected_name:
                        delete_user_preset(custom_store_path, selected_name, builtin_names=builtin)
                    st.success(labels["saved"])
                    st.rerun()
                except (FormatSpecError, OSError) as exc:
                    st.error(f"{labels['bad_json']}: {exc}")
            if st.button(labels["delete"], key=f"format_delete_{locale}"):
                try:
                    delete_user_preset(custom_store_path, selected_name, builtin_names=builtin)
                    st.success(labels["deleted"])
                    st.rerun()
                except (FormatSpecError, OSError) as exc:
                    st.error(f"{labels['bad_json']}: {exc}")
        else:
            st.caption(labels["built_in"])

    with st.expander(labels["natural"], expanded=False):
        instructions = st.text_area(labels["natural_input"], value=saved_format_prompt, height=120, key=f"format_natural_prompt_{locale}")
        if st.button(labels["generate"], key=f"format_natural_generate_{locale}"):
            try:
                response = require_valid_llm_output(llm_call(_generation_prompt(instructions, locale), max_tokens=1800, json_mode=True))
                pending = parse_format_spec(response)
                st.session_state[f"format_pending_{locale}"] = pending
                st.success(labels["generated"])
            except (LLMOutputError, FormatSpecError, TypeError, ValueError) as exc:
                st.error(f"{labels['generation_failed']} {exc}")
        pending = st.session_state.get(f"format_pending_{locale}")
        if isinstance(pending, dict):
            st.caption(labels["pending"])
            st.json(pending)
            name = st.text_input(labels["save_name"], value=pending.get("name", "Custom Academic"), key=f"format_pending_name_{locale}")
            if st.button(labels["confirm"], key=f"format_pending_save_{locale}"):
                try:
                    save_user_preset(custom_store_path, name, pending, builtin_names=builtin)
                    st.session_state.pop(f"format_pending_{locale}", None)
                    st.success(labels["saved"])
                    st.rerun()
                except (FormatSpecError, OSError) as exc:
                    st.error(f"{labels['bad_json']}: {exc}")
    return selected_spec
