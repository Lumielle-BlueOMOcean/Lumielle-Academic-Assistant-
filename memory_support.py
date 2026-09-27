"""Shared, fail-closed chapter-memory construction for both application locales."""

from document_support import DEFAULT_CHUNK_CHARS, chunk_document_text
from writing_support import LLMOutputError, require_valid_llm_output


MEMORY_MAX_OUTPUT_TOKENS = 700


def _chunk_prompt(chunk, part_number, total_parts, locale, retry=False):
    if locale == "zh":
        retry_text = "上一次响应无效。请仅根据本分块重新提取，不要输出错误信息。\n" if retry else ""
        return (
            "你负责为后续章节建立可靠的章节记忆。只提取本次分块中明确出现的信息，不得补全、猜测或引用其他分块中未出现的事实。\n"
            f"这是完整章节的第 {part_number}/{total_parts} 个有序分块。保留关键事实、数字、定义、方法、研究/实验结果、结论、限制条件，以及可能影响后续章节一致性的信息；若本分块本身存在冲突，应分别保留。\n"
            "请用简洁中文列出本分块的信息，供最终合并；不得把本分块没有的信息写入记忆。\n"
            + retry_text
            + f"[CHAPTER CHUNK {part_number}/{total_parts}]\n[CHAPTER TEXT]\n"
            + chunk
        )

    retry_text = "The previous response was invalid. Extract again from this chunk only; do not return an error message.\n" if retry else ""
    return (
        "Build reliable chapter memory for later chapters. Extract only information explicitly present in this chunk; do not complete, guess, or import facts from other chunks.\n"
        f"This is ordered chunk {part_number} of {total_parts} from the complete chapter. Preserve key facts, numbers, definitions, methods, research or experimental results, conclusions, limitations, and information that may affect consistency in later chapters. Preserve distinct conflicting statements if this chunk contains them.\n"
        "Write concise notes for final synthesis. Do not add information absent from this chunk.\n"
        + retry_text
        + f"[CHAPTER CHUNK {part_number}/{total_parts}]\n[CHAPTER TEXT]\n"
        + chunk
    )


def _synthesis_prompt(extractions, locale, retry=False):
    ordered_extractions = "\n\n".join(
        f"[CHUNK MEMORY {index}/{len(extractions)}]\n{extraction}"
        for index, extraction in enumerate(extractions, start=1)
    )
    if locale == "zh":
        retry_text = "上一次合并响应无效。请仅输出完整、非空的章节记忆。\n" if retry else ""
        return (
            "[MEMORY SYNTHESIS]\n"
            "请将以下按原文顺序提取的章节记忆合并为简洁的中文章节记忆，供后续章节使用。合并重复信息；保留源内容中的冲突，不要自行消除；不得编造。优先保留后续章节需要核对的事实、数字、定义、方法、结果、结论与限制。内容应紧凑，但不得丢掉关键一致性信息。\n"
            + retry_text
            + "[ORDERED CHUNK EXTRACTIONS]\n"
            + ordered_extractions
        )

    retry_text = "The previous synthesis response was invalid. Return one complete, non-empty chapter memory only.\n" if retry else ""
    return (
        "[MEMORY SYNTHESIS]\n"
        "Synthesize the following ordered chapter extractions into a compact memory for later chapters. Merge duplicates, preserve source conflicts instead of resolving them, and do not fabricate. Prioritize facts, numbers, definitions, methods, results, conclusions, and limitations needed by later chapters. Keep it concise without dropping key consistency information.\n"
        + retry_text
        + "[ORDERED CHUNK EXTRACTIONS]\n"
        + ordered_extractions
    )


def _checked_call(llm_call, prompt):
    return require_valid_llm_output(
        llm_call(prompt, max_tokens=MEMORY_MAX_OUTPUT_TOKENS)
    ).strip()


def _call_with_one_retry(llm_call, prompt_builder):
    last_error = None
    for attempt in range(2):
        try:
            result = _checked_call(llm_call, prompt_builder(attempt == 1))
            if result:
                return result
            last_error = LLMOutputError("The model returned an empty response.")
        except LLMOutputError as exc:
            last_error = exc
    raise last_error or LLMOutputError("The model returned no usable chapter memory.")


def build_chapter_memory(chapter_text, llm_call, locale="en", max_chars=DEFAULT_CHUNK_CHARS):
    """Build a complete compact memory from ordered, non-overlapping chapter chunks."""
    if not isinstance(chapter_text, str) or not chapter_text.strip():
        return ""

    chunks = chunk_document_text(chapter_text, max_chars=max_chars)
    if not chunks:
        return ""

    extractions = []
    for index, chunk in enumerate(chunks, start=1):
        extractions.append(
            _call_with_one_retry(
                llm_call,
                lambda retry, chunk=chunk, index=index: _chunk_prompt(
                    chunk, index, len(chunks), locale, retry=retry
                ),
            )
        )

    return _call_with_one_retry(
        llm_call,
        lambda retry: _synthesis_prompt(extractions, locale, retry=retry),
    )


def update_chapter_memory(memories, chapter_id, chapter_text, llm_call, locale="en", max_chars=DEFAULT_CHUNK_CHARS):
    """Return a replacement memory map only after the entire new memory succeeds."""
    summary = build_chapter_memory(
        chapter_text,
        llm_call,
        locale=locale,
        max_chars=max_chars,
    )
    updated = dict(memories) if isinstance(memories, dict) else {}
    updated[chapter_id] = summary
    return updated, summary
