"""Deterministic document chunking and atomic Research Foundation parsing."""

import io
import os
import re
import time

import PyPDF2
import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from writing_support import LLMOutputError, require_valid_llm_output
from structured_output_support import classify_provider_exception


DEFAULT_CHUNK_CHARS = 5500
MAX_OUTPUT_TOKENS = 7000


def extract_document_text(raw_bytes, filename):
    """Extract all available text from supported PDF/DOCX/TXT sources without OCR or prefix loss."""
    extension = os.path.splitext(str(filename or ""))[1].casefold()
    try:
        if extension == ".pdf":
            reader = PyPDF2.PdfReader(io.BytesIO(raw_bytes))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            if not text.strip():
                return "Parse failed: PDF contains no extractable text (possibly scanned/image-based PDF). Convert to Word or plain text and retry."
            return text
        if extension == ".docx":
            document = docx.Document(io.BytesIO(raw_bytes))
            fragments = []
            for element in document.element.body.iterchildren():
                if element.tag.endswith("}p"):
                    fragments.append(Paragraph(element, document).text)
                elif element.tag.endswith("}tbl"):
                    table = Table(element, document)
                    fragments.extend(" | ".join(cell.text for cell in row.cells) for row in table.rows)
            text = "\n".join(fragment for fragment in fragments if fragment.strip())
            if not text.strip():
                return "Parse failed: DOCX contains no extractable text."
            return text
        if extension == ".txt":
            text = raw_bytes.decode("utf-8-sig")
            return text if text.strip() else "Parse failed: TXT contains no text."
        return f"Parse failed: unsupported document type {extension or '(unknown)'}."
    except UnicodeDecodeError:
        return "Parse failed: TXT is not valid UTF-8."
    except Exception as exc:
        return f"Parse failed: {exc}"


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
            "你上次的结果不是有效的非空科研事实 JSON 对象。请修正后只输出合法 JSON 对象。\n"
            if retry else ""
        )
        return (
            "你是科研基座结构化解析器。只解析本次提供的文档分块，并将其整理为供研究者引用和编辑的模块。\n"
            f"文档用途：{purpose}\n"
            f"这是同一份源文档的第 {part_number}/{total_parts} 部分（DOCUMENT PART {part_number}/{total_parts}）。\n"
            "只提取本分块明确包含的信息；不得因为其他分块可能包含相关内容而省略本块内容，不得推断本块未提供的章节或事实。\n"
            "保留实质信息与表格数据。文本模块格式为 {\"title\":\"标题\",\"type\":\"text\",\"content\":\"内容\"}；"
            "表格模块格式为 {\"title\":\"标题\",\"type\":\"table\",\"columns\":[...],\"data\":[[...]]}；识别到的表格用 table，其余内容用 text。\n"
            "只输出一个非空 JSON 对象，顶层字段为 facts，值为模块数组，例如 {\"facts\":[{\"title\":\"标题\",\"type\":\"text\",\"content\":\"内容\",\"tags\":[]}]}。\n"
            "禁止 Markdown 围栏、解释、截断、省略号或添加本分块没有的内容。\n"
            + retry_instruction
            + "[DOCUMENT CONTENT]\n"
            + chunk
        )

    retry_instruction = (
        "Your previous response was not a valid non-empty research-facts JSON object. Correct it and output only a valid JSON object.\n"
        if retry else ""
    )
    return (
        "You are a structured parser for the research foundation. Parse only the supplied document part into modules for the researcher to reference and edit.\n"
        f"Document purpose: {purpose}\n"
        f"This is part {part_number} of {total_parts} of one source document (DOCUMENT PART {part_number}/{total_parts}).\n"
        "Extract all substantive information present in this part. Do not omit content because another part may contain related material, and do not infer sections or facts absent from this part.\n"
        "Use text modules as {\"title\":\"title\",\"type\":\"text\",\"content\":\"content\"}; use table modules as {\"title\":\"title\",\"type\":\"table\",\"columns\":[...],\"data\":[[...]]}. Represent detected tables as table modules and other content as text modules.\n"
        "Return one non-empty JSON object only, with a top-level facts array, for example {\"facts\":[{\"title\":\"title\",\"type\":\"text\",\"content\":\"content\",\"tags\":[]}]}.\n"
        "No Markdown fences, explanations, truncation, ellipses, or content absent from this part.\n"
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
    sleep=None,
    max_length_splits=2,
):
    """Parse all chunks in order and return modules only if every chunk succeeds."""
    sleep = sleep or time.sleep
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
    system_prompt = "你是严谨的科研事实结构化解析器，只输出 JSON 对象。" if locale == "zh" else "You are a rigorous research-facts parser. Output only a JSON object."
    def split_for_length(chunk):
        if len(chunk) < 2:
            return None
        midpoint = len(chunk) // 2
        sentence_candidates = [
            match.end() for match in re.finditer(r"(?<=[.!?。！？])\s+", chunk)
            if 0 < match.end() < len(chunk)
        ]
        if sentence_candidates:
            split_at = min(sentence_candidates, key=lambda value: abs(value - midpoint))
        else:
            lower, upper = max(1, int(len(chunk) * 0.3)), min(len(chunk) - 1, int(len(chunk) * 0.7))
            candidates = [lower + match.end() for match in re.finditer(r"\n[ \t]*\n|\r?\n|\s+", chunk[lower:upper])]
            split_at = min(candidates, key=lambda value: abs(value - midpoint)) if candidates else midpoint
        if not 0 < split_at < len(chunk):
            return None
        return chunk[:split_at], chunk[split_at:]

    def parse_one(chunk, index, total, *, depth=0):
        parsed_modules = None
        last_response = ""
        failure_stage = "invalid_structured_output"
        for attempt in range(2):
            prompt = _build_chunk_prompt(
                chunk,
                purpose,
                index,
                total,
                locale,
                retry=attempt == 1,
            )
            kwargs = {
                "system_prompt": system_prompt,
                "max_tokens": MAX_OUTPUT_TOKENS,
                "json_mode": True,
                "return_metadata": True,
            }
            if attempt == 1:
                kwargs["temp"] = 0.3
            try:
                response = llm_call(prompt, **kwargs)
            except Exception as exc:
                code = classify_provider_exception(exc)
                return None, {"failure_stage": "provider_call", "failure_code": code, "response": ""}
            if isinstance(response, dict):
                error_code = response.get("error_code")
                finish_reason = response.get("finish_reason")
                last_response = response.get("content", "") if isinstance(response.get("content"), str) else ""
                if error_code and error_code not in {"truncated_output", "empty_content", "incomplete_response"}:
                    return None, {"failure_stage": "provider_call", "failure_code": error_code, "response": ""}
                if finish_reason == "length" or error_code == "truncated_output":
                    if depth >= max_length_splits:
                        return None, {"failure_stage": "truncated_output", "failure_code": "truncated_output", "response": ""}
                    split = split_for_length(chunk)
                    if not split:
                        return None, {"failure_stage": "truncated_output", "failure_code": "truncated_output", "response": ""}
                    left, right = split
                    left_result, left_failure = parse_one(left, index, total, depth=depth + 1)
                    if left_result is None:
                        return None, left_failure
                    right_result, right_failure = parse_one(right, index, total, depth=depth + 1)
                    if right_result is None:
                        return None, right_failure
                    return left_result + right_result, None
                if finish_reason == "content_filter":
                    return None, {"failure_stage": "provider_call", "failure_code": "content_filtered", "response": ""}
                if finish_reason != "stop":
                    return None, {"failure_stage": "provider_call", "failure_code": "incomplete_response", "response": ""}
            else:
                # Compatibility for pure unit fakes and legacy helpers. Production
                # dispatch_llm_call is always invoked with return_metadata=True.
                last_response = response if isinstance(response, str) else ""
            if not last_response.strip():
                failure_stage = "empty_structured_output"
                continue
            try:
                last_response = require_valid_llm_output(last_response)
            except LLMOutputError:
                return None, {"failure_stage": "provider_call", "failure_code": "provider_error", "response": ""}

            try:
                raw_modules = parse_response(last_response)
            except (TypeError, ValueError):
                raw_modules = None
            if raw_modules is None:
                failure_stage = "invalid_structured_output"
            elif not isinstance(raw_modules, list) or not raw_modules:
                failure_stage = "empty_structured_output"
            else:
                failure_stage = "schema_validation_failure"
            if isinstance(raw_modules, list) and raw_modules:
                parsed_modules = normalize_modules(raw_modules)
                if isinstance(parsed_modules, list) and parsed_modules:
                    for module in parsed_modules:
                        if isinstance(module, dict):
                            module.setdefault("source_chunk", index)
                    return parsed_modules, None
                parsed_modules = None
        return None, {"failure_stage": failure_stage, "failure_code": failure_stage, "response": last_response}

    for index, chunk in enumerate(chunks, start=1):
        parsed_modules, failure = parse_one(chunk, index, len(chunks))
        if parsed_modules is None:
            failure_stage = failure["failure_stage"]
            failure_code = failure["failure_code"]
            if on_parse_failure:
                on_parse_failure(chunk, "")
            if locale == "zh":
                descriptions = {
                    "invalid_structured_output": "AI 返回的数据格式异常",
                    "empty_structured_output": "AI 返回了空的科研事实数据",
                    "schema_validation_failure": "AI 返回的数据不符合科研事实格式",
                    "truncated_output": "模型输出达到长度限制",
                    "provider_call": "AI 服务调用失败",
                }
                message = f"{descriptions.get(failure_stage, 'AI 解析失败')}（第 {index}/{len(chunks)} 块），未导入不完整内容。请稍后重试。"
            else:
                descriptions = {
                    "invalid_structured_output": "The AI returned malformed structured data",
                    "empty_structured_output": "The AI returned no research facts",
                    "schema_validation_failure": "The AI data did not match the research-fact schema",
                    "truncated_output": "The model output reached its length limit",
                    "provider_call": "The AI service could not complete this request",
                }
                message = f"{descriptions.get(failure_stage, 'The document parse failed')} for part {index} of {len(chunks)}. No partial content was imported. Please retry later."
            return {
                "status": "error", "modules": [], "chunks_total": len(chunks),
                "failed_chunk": index,
                "error_type": "llm" if failure_stage == "provider_call" else "parse",
                "failure_stage": failure_stage,
                "failure_code": failure_code, "message": message,
            }
        collected.extend(parsed_modules)

    return {"status": "ok", "modules": collected, "chunks_total": len(chunks)}
