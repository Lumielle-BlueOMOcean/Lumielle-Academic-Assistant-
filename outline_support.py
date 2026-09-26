"""Complete-manuscript reverse engineering without partial outline writes."""

import json
import os
import tempfile
import uuid

from document_support import DEFAULT_CHUNK_CHARS, chunk_document_text
from writing_support import LLMOutputError, require_valid_llm_output


MAX_CONSOLIDATION_PROMPT_CHARS = 24000
MAX_SOURCE_OUTPUT_TOKENS = 4500
MAX_MERGE_OUTPUT_TOKENS = 6000
MAX_TREE_DEPTH = 3


class OutlineParseError(ValueError):
    """Raised when a model response is not a usable outline JSON array."""


def extract_docx_manuscript_text(document):
    """Extract non-empty paragraphs and table-cell paragraphs in DOCX body order."""
    extracted = []

    def collect_blocks(container):
        for block in container.iter_inner_content():
            text = getattr(block, "text", None)
            if text is not None:
                if text.strip():
                    extracted.append(text)
                continue
            seen_cells = set()
            for row in block.rows:
                for cell in row.cells:
                    cell_id = id(cell._tc)
                    if cell_id in seen_cells:
                        continue
                    seen_cells.add(cell_id)
                    collect_blocks(cell)

    collect_blocks(document)
    return "\n".join(extracted)


def extract_outline_array(response):
    """Decode the first complete JSON outline array without greedy regex parsing."""
    if not isinstance(response, str):
        raise OutlineParseError("Outline response is not text.")
    decoder = json.JSONDecoder()
    for index, character in enumerate(response):
        if character not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(response, index)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            if isinstance(value.get("outline"), list):
                value = value["outline"]
            else:
                value = [value]
        if isinstance(value, list) and value and _contains_titled_node(value):
            return value
    raise OutlineParseError("No non-empty outline JSON array was found.")


def _title_of(node):
    if not isinstance(node, dict):
        return ""
    value = node.get("title")
    return str(value).strip() if value is not None else ""


def _contains_titled_node(nodes):
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if _title_of(node):
            return True
        children = node.get("children")
        if isinstance(children, list) and _contains_titled_node(children):
            return True
    return False


def _validate_tree_depth(nodes, depth=1):
    for node in nodes:
        if not isinstance(node, dict) or not _title_of(node):
            continue
        if depth > MAX_TREE_DEPTH:
            raise OutlineParseError("The outline exceeds the supported hierarchy depth.")
        children = node.get("children")
        if isinstance(children, list):
            _validate_tree_depth(children, depth + 1)


def _safe_text(value):
    return "" if value is None else str(value)


def _safe_word_count(value):
    if isinstance(value, bool):
        return int(value)
    try:
        count = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, count)


def _safe_boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes", "是", "是的"}
    return False


def normalize_reverse_outline_tree(raw_tree):
    """Rebuild the compatible Logic Chain schema and replace all model IDs."""
    if isinstance(raw_tree, dict):
        raw_tree = [raw_tree]
    if not isinstance(raw_tree, list):
        raise OutlineParseError("The outline root must be a non-empty array.")
    _validate_tree_depth(raw_tree)

    used_ids = set()

    def normalize_nodes(nodes, depth):
        normalized = []
        for raw_node in nodes:
            if not isinstance(raw_node, dict):
                continue
            title = _title_of(raw_node)
            if not title:
                continue
            if depth > MAX_TREE_DEPTH:
                raise OutlineParseError("The outline exceeds the supported hierarchy depth.")
            raw_children = raw_node.get("children")
            children = normalize_nodes(raw_children, depth + 1) if isinstance(raw_children, list) else []
            node_id = uuid.uuid4().hex
            while node_id in used_ids:
                node_id = uuid.uuid4().hex
            used_ids.add(node_id)
            word_count = sum(child["word_count"] for child in children) if children else _safe_word_count(raw_node.get("word_count", 0))
            normalized.append({
                "id": node_id,
                "title": title,
                "desc": _safe_text(raw_node.get("desc", "")),
                "word_count": word_count,
                "chart_instruction": _safe_text(raw_node.get("chart_instruction", "")),
                "image_suggestion": _safe_text(raw_node.get("image_suggestion", "")),
                "is_own_experiment": _safe_boolean(raw_node.get("is_own_experiment", False)),
                "references": [],
                "children": children,
            })
        return normalized

    result = normalize_nodes(raw_tree, 1)
    if not result:
        raise OutlineParseError("The outline has no titled nodes.")
    return result


def _short_prompt(source_text, locale, retry=False):
    if locale == "zh":
        correction = "上次结果不是有效且层级不超过三级的大纲 JSON 数组；请修正并只输出 JSON。\n" if retry else ""
        instructions = (
            "请从下方完整源文稿中逆向提取逻辑大纲，保留章节顺序、合并同一章节，不得添加源文稿不存在的章节。"
            "最多三级层级。叶子节点的 word_count 是对应源文稿篇幅的粗略估计；不确定时填 0。references 必须为空。\n"
            '每个节点字段为 {"id":"可忽略","title":"标题","desc":"主题","word_count":0,'
            '"chart_instruction":"","image_suggestion":"","is_own_experiment":false,"references":[],"children":[]}。\n'
            "只输出非空 JSON 数组，不要 Markdown、解释或省略。\n"
        )
    else:
        correction = "The previous result was not a valid outline JSON array with at most three levels. Correct it and output JSON only.\n" if retry else ""
        instructions = (
            "Reverse-engineer the logic outline from the complete source manuscript below. Preserve section order, merge repeated sections, and do not invent sections absent from the source. "
            "Use at most three hierarchy levels. A leaf word_count is a rough estimate of existing source length; use 0 when uncertain. references must be empty.\n"
            'Each node uses {"id":"ignored","title":"title","desc":"topic","word_count":0,'
            '"chart_instruction":"","image_suggestion":"","is_own_experiment":false,"references":[],"children":[]} .\n'
            "Return one non-empty JSON array only, with no Markdown, explanation, or omissions.\n"
        )
    return (
        "[FINAL OUTLINE FROM COMPLETE MANUSCRIPT]\n"
        + instructions + correction
        + "[COMPLETE SOURCE MANUSCRIPT]\n" + source_text
    )


def _source_prompt(chunk, part_number, total_parts, locale, retry=False):
    marker = f"SOURCE DOCUMENT PART {part_number}/{total_parts}"
    if locale == "zh":
        correction = "上次结果不是有效的大纲 JSON 数组，请修正后只输出 JSON。\n" if retry else ""
        instructions = (
            f"这是源文档第 {part_number}/{total_parts} 部分（{marker}）。只提取本部分实际出现的结构，保留原文顺序，不得虚构缺失章节。"
            "章节可能从上一部分延续，也可能延续到下一部分；最多三级层级。不要生成引用、图表建议或图片建议，不需要 ID 或精确字数。\n"
            "只输出标题、主题描述、层级和子节点组成的非空 JSON 数组。\n"
        )
    else:
        correction = "The previous response was not a valid outline JSON array. Correct it and output JSON only.\n" if retry else ""
        instructions = (
            f"This is source document part {part_number}/{total_parts} ({marker}). Extract only structure present in this part, preserve original order, and do not invent missing sections. "
            "A section may continue from the previous part or into the next part. Use at most three hierarchy levels. Do not generate citations, chart suggestions, or image suggestions; IDs and precise word counts are unnecessary.\n"
            "Return a non-empty JSON array containing titles, topic descriptions, hierarchy hints, and children only.\n"
        )
    return (
        "[STAGE 1: SOURCE STRUCTURE EXTRACTION]\n"
        + marker + "\n" + instructions + correction
        + "[SOURCE PART CONTENT]\n" + chunk
    )


def _merge_prompt(fragments, locale, round_number, final=False, retry=False):
    source_fragments = [
        {"source_parts": [fragment["start"], fragment["end"]], "outline": fragment["nodes"]}
        for fragment in fragments
    ]
    payload = json.dumps(source_fragments, ensure_ascii=False, separators=(",", ":"))
    if locale == "zh":
        retry_text = "上次结果不是有效大纲 JSON；请修正后仅输出 JSON。\n" if retry else ""
        if final:
            instruction = (
                "按源分块顺序合并局部结构。合并跨分块重复章节，修复父子关系；不得新增局部结构中不存在的章节。"
                "最多三级层级。输出与逻辑链兼容的最终节点字段：id、title、desc、word_count、chart_instruction、image_suggestion、is_own_experiment、references、children；references 必须为空，ID 可忽略。\n"
            )
        else:
            instruction = (
                "按源分块顺序合并局部结构。合并跨分块重复章节，修复父子关系；不得新增局部结构中不存在的章节。"
                "最多三级层级。只输出合并后的标题、主题描述、层级和子节点 JSON 数组。\n"
            )
        header = f"[STAGE 2: ORDERED CONSOLIDATION ROUND {round_number}]\n"
    else:
        retry_text = "The previous response was not valid outline JSON. Correct it and output JSON only.\n" if retry else ""
        if final:
            instruction = (
                "Consolidate these partial outlines in source-part order. Merge repeated sections across chunk boundaries and restore parent-child structure; do not add sections absent from the partial outlines. "
                "Use at most three hierarchy levels. Return the final Logic Chain node fields: id, title, desc, word_count, chart_instruction, image_suggestion, is_own_experiment, references, children. references must be empty; IDs may be ignored.\n"
            )
        else:
            instruction = (
                "Consolidate these partial outlines in source-part order. Merge repeated sections across chunk boundaries and restore parent-child structure; do not add sections absent from the partial outlines. "
                "Use at most three hierarchy levels. Return only the merged titles, topic descriptions, hierarchy, and children as a JSON array.\n"
            )
        header = f"[STAGE 2: ORDERED CONSOLIDATION ROUND {round_number}]\n"
    return header + instruction + retry_text + "[ORDERED SOURCE FRAGMENTS]\n" + payload


def _call_for_fragments(prompt_factory, llm_call, locale, max_tokens):
    last_error = None
    error_type = "parse"
    for attempt in range(2):
        try:
            response = require_valid_llm_output(llm_call(
                prompt_factory(attempt == 1),
                system_prompt="You are a rigorous outline-structure extractor. Output JSON only." if locale != "zh" else "你是严谨的大纲结构提取器，只输出 JSON。",
                max_tokens=max_tokens,
                temp=0.3 if attempt else 0.2,
            ))
            return extract_outline_array(response), None
        except LLMOutputError as exc:
            last_error = exc
            error_type = "llm"
        except (OutlineParseError, TypeError, ValueError) as exc:
            last_error = exc
            error_type = "parse"
    return None, {"error_type": error_type, "error": last_error}


def _call_for_final_tree(prompt_factory, llm_call, locale, max_tokens):
    last_error = None
    error_type = "parse"
    for attempt in range(2):
        try:
            response = require_valid_llm_output(llm_call(
                prompt_factory(attempt == 1),
                system_prompt="You are a rigorous academic outline consolidator. Output JSON only." if locale != "zh" else "你是严谨的学术大纲整合器，只输出 JSON。",
                max_tokens=max_tokens,
                temp=0.3 if attempt else 0.2,
            ))
            tree = normalize_reverse_outline_tree(extract_outline_array(response))
            return tree, None
        except LLMOutputError as exc:
            last_error = exc
            error_type = "llm"
        except (OutlineParseError, TypeError, ValueError) as exc:
            last_error = exc
            error_type = "parse"
    return None, {"error_type": error_type, "error": last_error}


def _pack_ordered_fragments(fragments, locale, round_number, max_prompt_chars):
    groups = []
    current = []
    for fragment in fragments:
        candidate = current + [fragment]
        prompt = _merge_prompt(candidate, locale, round_number, final=True, retry=True)
        if len(prompt) <= max_prompt_chars:
            current = candidate
            continue
        if not current:
            raise ValueError("A single partial outline exceeds the consolidation prompt budget.")
        groups.append(current)
        current = [fragment]
        if len(_merge_prompt(current, locale, round_number, final=True, retry=True)) > max_prompt_chars:
            raise ValueError("A single partial outline exceeds the consolidation prompt budget.")
    if current:
        groups.append(current)

    if len(fragments) > 1 and len(groups) == len(fragments):
        first_pair = fragments[:2]
        pair_prompt = _merge_prompt(first_pair, locale, round_number, final=True, retry=True)
        if len(pair_prompt) > max_prompt_chars:
            raise ValueError("Adjacent partial outlines cannot be merged within the consolidation prompt budget.")
        groups = [first_pair] + [[fragment] for fragment in fragments[2:]]
    return groups


def _error_result(locale, error_type, chunks_total, stage):
    message = (
        "文稿未能完整完成逆向解析，因此已保留原有逻辑大纲。"
        if locale == "zh"
        else "The manuscript could not be reverse-engineered completely, so the existing logic outline was kept."
    )
    return {
        "status": "error",
        "error_type": error_type,
        "stage": stage,
        "chunks_total": chunks_total,
        "message": message,
    }


def reverse_engineer_manuscript(
    extracted_text,
    llm_call,
    locale="en",
    max_chars=DEFAULT_CHUNK_CHARS,
    max_consolidation_prompt_chars=MAX_CONSOLIDATION_PROMPT_CHARS,
):
    """Return a complete normalized outline, or an error with no persistable tree."""
    if not isinstance(extracted_text, str) or not extracted_text.strip():
        return _error_result(locale, "empty", 0, "source")
    chunks = chunk_document_text(extracted_text, max_chars=max_chars)
    if not chunks:
        return _error_result(locale, "empty", 0, "source")

    if len(chunks) == 1:
        parsed_tree, error = _call_for_final_tree(
            lambda retry: _short_prompt(extracted_text, locale, retry=retry),
            llm_call,
            locale,
            MAX_MERGE_OUTPUT_TOKENS,
        )
        if error:
            return _error_result(locale, error["error_type"], 1, "final_outline")
        return {"status": "ok", "tree": parsed_tree, "chunks_total": 1, "stage1_calls": 0, "merge_rounds": 0}

    fragments = []
    for part_number, chunk in enumerate(chunks, start=1):
        parsed, error = _call_for_fragments(
            lambda retry, content=chunk, number=part_number: _source_prompt(
                content, number, len(chunks), locale, retry=retry
            ),
            llm_call,
            locale,
            MAX_SOURCE_OUTPUT_TOKENS,
        )
        if error:
            return _error_result(locale, error["error_type"], len(chunks), f"source_part_{part_number}")
        fragments.append({"start": part_number, "end": part_number, "nodes": parsed})

    round_number = 0
    while len(fragments) > 1:
        round_number += 1
        try:
            groups = _pack_ordered_fragments(
                fragments, locale, round_number, max_consolidation_prompt_chars
            )
        except ValueError:
            return _error_result(locale, "budget", len(chunks), "consolidation")
        if len(groups) >= len(fragments):
            return _error_result(locale, "budget", len(chunks), "consolidation")

        next_fragments = []
        final_round = len(groups) == 1
        for group in groups:
            if len(group) == 1:
                next_fragments.append(group[0])
                continue
            source_start = group[0]["start"]
            source_end = group[-1]["end"]
            if final_round:
                merged, error = _call_for_final_tree(
                    lambda retry, batch=group: _merge_prompt(batch, locale, round_number, final=True, retry=retry),
                    llm_call,
                    locale,
                    MAX_MERGE_OUTPUT_TOKENS,
                )
                if error:
                    return _error_result(locale, error["error_type"], len(chunks), "consolidation")
                return {
                    "status": "ok", "tree": merged, "chunks_total": len(chunks),
                    "stage1_calls": len(chunks), "merge_rounds": round_number,
                }
            merged, error = _call_for_fragments(
                lambda retry, batch=group: _merge_prompt(batch, locale, round_number, final=False, retry=retry),
                llm_call,
                locale,
                MAX_MERGE_OUTPUT_TOKENS,
            )
            if error:
                return _error_result(locale, error["error_type"], len(chunks), "consolidation")
            next_fragments.append({"start": source_start, "end": source_end, "nodes": merged})
        fragments = next_fragments

    return _error_result(locale, "parse", len(chunks), "consolidation")


def persist_reverse_outline_result(result, data_dir):
    """Atomically replace the saved outline only with a successful final tree."""
    if not isinstance(result, dict) or result.get("status") != "ok":
        return False
    tree = result.get("tree")
    if not isinstance(tree, list) or not tree:
        return False
    destination = os.path.join(os.fspath(data_dir), "logic_tree.json")
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=os.path.dirname(destination),
            prefix=".logic_tree.json.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = temporary_file.name
            json.dump(tree, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
        return True
    except Exception:
        return False
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
