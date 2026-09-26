"""Deterministic document chunking and atomic Research Foundation parsing."""

import re

from writing_support import LLMOutputError, require_valid_llm_output


DEFAULT_CHUNK_CHARS = 5500
MAX_OUTPUT_TOKENS = 7000


def chunk_document_text(text, max_chars=DEFAULT_CHUNK_CHARS):
    """Split text into contiguous chunks without dropping or duplicating characters."""
    if not isinstance(max_chars, int) or max_chars < 1:
        raise ValueError("max_chars must be a positive integer")
    if not isinstance(text, str) or not text:
        return []

    chunks = []
    start = 0
    while start < len(text):
        hard_end = min(start + max_chars, len(text))
        if hard_end == len(text):
            end = hard_end
        else:
            lower_bound = start + max(1, max_chars // 2)
            window = text[lower_bound:hard_end]
            boundary_patterns = (
                r"\n[ \t]*\n",
                r"\r?\n",
                r"(?<=[.!?。！？])\s+",
                r"\s+",
            )
            end = hard_end
            for pattern in boundary_patterns:
                matches = list(re.finditer(pattern, window))
                if matches:
                    end = lower_bound + matches[-1].end()
                    break
        chunks.append(text[start:end])
        start = end

    return chunks


def is_document_parse_error(text):
    """Identify localized extraction errors without rejecting ordinary document text."""
    if not isinstance(text, str):
        return False
    stripped = text.lstrip()
    folded = stripped.casefold()
    return folded.startswith("parse failed:") or stripped.startswith("解析失败")


def _build_chunk_prompt(chunk, purpose, part_number, total_parts, locale, retry=False):
    if locale == "zh":
        retry_instruction = (
            "你上次的结果不是有效的非空模块 JSON 数组。请修正后只输出合法 JSON 数组。\n"
            if retry else ""
        )
        return (
            "你是科研基座结构化解析器。只解析本次提供的文档分块，并将其整理为供研究者引用和编辑的模块。\n"
            f"文档用途：{purpose}\n"
            f"这是同一份源文档的第 {part_number}/{total_parts} 部分（DOCUMENT PART {part_number}/{total_parts}）。\n"
            "只提取本分块明确包含的信息；不得因为其他分块可能包含相关内容而省略本块内容，不得推断本块未提供的章节或事实。\n"
            "保留实质信息与表格数据。文本模块格式为 {\"title\":\"标题\",\"type\":\"text\",\"content\":\"内容\"}；"
            "表格模块格式为 {\"title\":\"标题\",\"type\":\"table\",\"columns\":[...],\"data\":[[...]]}；识别到的表格用 table，其余内容用 text。\n"
            "只输出一个非空 JSON 数组；禁止 Markdown 围栏、解释、截断、省略号或添加本分块没有的内容。\n"
            + retry_instruction
            + "[DOCUMENT CONTENT]\n"
            + chunk
        )

    retry_instruction = (
        "Your previous response was not a valid non-empty module JSON array. Correct it and output only valid JSON.\n"
        if retry else ""
    )
    return (
        "You are a structured parser for the research foundation. Parse only the supplied document part into modules for the researcher to reference and edit.\n"
        f"Document purpose: {purpose}\n"
        f"This is part {part_number} of {total_parts} of one source document (DOCUMENT PART {part_number}/{total_parts}).\n"
        "Extract all substantive information present in this part. Do not omit content because another part may contain related material, and do not infer sections or facts absent from this part.\n"
        "Use text modules as {\"title\":\"title\",\"type\":\"text\",\"content\":\"content\"}; use table modules as {\"title\":\"title\",\"type\":\"table\",\"columns\":[...],\"data\":[[...]]}. Represent detected tables as table modules and other content as text modules.\n"
        "Return one non-empty JSON array only. No Markdown fences, explanations, truncation, ellipses, or content absent from this part.\n"
        + retry_instruction
        + "[DOCUMENT CONTENT]\n"
        + chunk
    )


def parse_document_in_chunks(
    document_text,
    purpose,
    llm_call,
    parse_response,
    normalize_modules,
    locale="en",
    max_chars=DEFAULT_CHUNK_CHARS,
    on_parse_failure=None,
):
    """Parse all chunks in order and return modules only if every chunk succeeds."""
    if not isinstance(document_text, str) or not document_text.strip():
        chunks = []
    else:
        chunks = chunk_document_text(document_text, max_chars=max_chars)
    if not chunks:
        return {
            "status": "error",
            "modules": [],
            "message": "文档没有可解析的文字内容，因此本次没有导入任何模块。" if locale == "zh" else "The document has no text to parse, so no modules were imported.",
            "failed_chunk": 0,
            "chunks_total": 0,
            "error_type": "parse",
        }

    collected = []
    system_prompt = "你是严谨的结构化解析器，只输出 JSON。" if locale == "zh" else "You are a rigorous structured parser. Output only JSON."
    for index, chunk in enumerate(chunks, start=1):
        parsed_modules = None
        last_response = ""
        for attempt in range(2):
            prompt = _build_chunk_prompt(
                chunk,
                purpose,
                index,
                len(chunks),
                locale,
                retry=attempt == 1,
            )
            kwargs = {"system_prompt": system_prompt, "max_tokens": MAX_OUTPUT_TOKENS}
            if attempt == 1:
                kwargs["temp"] = 0.3
            try:
                last_response = require_valid_llm_output(llm_call(prompt, **kwargs))
            except LLMOutputError as exc:
                if locale == "zh":
                    message = f"文档第 {index}/{len(chunks)} 部分调用模型失败，因此本次没有导入任何部分结果：{exc}"
                else:
                    message = f"The model could not process document part {index} of {len(chunks)}, so no partial content was imported: {exc}"
                return {
                    "status": "error", "modules": [], "chunks_total": len(chunks),
                    "failed_chunk": index, "error_type": "llm", "message": message,
                }

            try:
                raw_modules = parse_response(last_response)
            except (TypeError, ValueError):
                raw_modules = None
            if isinstance(raw_modules, list) and raw_modules:
                parsed_modules = normalize_modules(raw_modules)
                if isinstance(parsed_modules, list) and parsed_modules:
                    break
                parsed_modules = None

        if parsed_modules is None:
            if on_parse_failure:
                on_parse_failure(chunk, last_response)
            message = (
                f"文档未能完整解析，因此本次没有导入不完整内容。第 {index}/{len(chunks)} 个分块重试后仍无法解析。"
                if locale == "zh"
                else f"The document could not be parsed completely, so no partial content was imported. Part {index} of {len(chunks)} failed after one retry."
            )
            return {
                "status": "error", "modules": [], "chunks_total": len(chunks),
                "failed_chunk": index, "error_type": "parse", "message": message,
            }
        collected.extend(parsed_modules)

    return {"status": "ok", "modules": collected, "chunks_total": len(chunks)}
