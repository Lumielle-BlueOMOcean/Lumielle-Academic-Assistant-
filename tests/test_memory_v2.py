import hashlib
import json
import shutil
import tempfile
import ast
import types
import unittest
from pathlib import Path

from chapter_memory_store import (
    initialize_memory_store,
    read_chapter_memory,
    save_chapter_memory,
    set_memory_item_confirmation,
    _validated_items,
)
from chapter_memory_support import (
    MemoryBuildError,
    build_chapter_memory_v2,
    build_context_sandwich_v2,
    fit_context_sandwich_to_prompt,
    save_draft_and_maybe_refresh_memory,
)
from chapter_consistency import (
    check_deterministic_consistency,
    review_chapter_candidate,
    review_semantic_consistency,
    _normalize_review,
)


def structured_result(value, finish_reason="stop"):
    return {
        "content": json.dumps(value, ensure_ascii=False),
        "finish_reason": finish_reason,
        "structured_mode": "native",
        "http_status": 200,
        "error_code": None,
    }


def extraction_for_chunk(prompt):
    source = prompt.split("[CHAPTER TEXT]\n", 1)[1]
    sentinels = ("BEGIN_SENTINEL", "MIDDLE_SENTINEL", "FINAL_CHAPTER_FACT_SENTINEL")
    found = [value for value in sentinels if value in source]
    if not found:
        return {"chunk_status": "no_relevant_content", "summary": "", "items": []}
    return {
        "chunk_status": "content",
        "summary": "Source contains " + ", ".join(found),
        "items": [
            {
                "memory_type": "finding",
                "statement": f"The chapter records {value}.",
                "source_quote": value,
            }
            for value in found
        ],
    }


class ChapterMemoryStoreV2Tests(unittest.TestCase):
    def _memory(self, text, *, item_id=None):
        quote = "The study reports 12 participants."
        start = text.index(quote)
        return {
            "narrative_summary": "The study reports a bounded sample.",
            "completion_status": "complete",
            "items": [
                {
                    "id": item_id,
                    "memory_type": "data_or_number",
                    "statement": "The study reports 12 participants.",
                    "source_quote": quote,
                    "source_start": start,
                    "source_end": start + len(quote),
                    "confirmed_for_writing": False,
                }
            ],
        }

    def test_database_is_created_in_the_supplied_data_directory(self):
        with tempfile.TemporaryDirectory() as data_dir:
            path = initialize_memory_store(data_dir)
            self.assertEqual(Path(path), Path(data_dir) / "chapter_memory_v2.sqlite3")
            self.assertTrue(Path(path).is_file())

    def test_body_hash_controls_current_and_stale_status(self):
        text = "The study reports 12 participants."
        with tempfile.TemporaryDirectory() as data_dir:
            initialize_memory_store(data_dir)
            save_chapter_memory(data_dir, "c1", text, self._memory(text))
            current = read_chapter_memory(data_dir, "c1", text)
            stale = read_chapter_memory(data_dir, "c1", text + " Revised.")
        self.assertEqual(current["status"], "current")
        self.assertEqual(current["draft_hash"], hashlib.sha256(text.encode("utf-8")).hexdigest())
        self.assertEqual(stale["status"], "stale")

    def test_missing_and_legacy_memory_are_not_treated_as_current(self):
        with tempfile.TemporaryDirectory() as data_dir:
            initialize_memory_store(data_dir)
            missing = read_chapter_memory(data_dir, "c1", "A saved body.")
            legacy = read_chapter_memory(data_dir, "c2", "A saved body.", legacy_summary="Old free-text memory")
            unwritten = read_chapter_memory(data_dir, "c3", "  ")
        self.assertEqual(missing["status"], "memory_missing")
        self.assertEqual(legacy["status"], "legacy_unverified")
        self.assertEqual(unwritten["status"], "unwritten")

    def test_source_quote_and_offsets_are_verified_before_persistence(self):
        text = "The study reports 12 participants."
        with tempfile.TemporaryDirectory() as data_dir:
            with self.assertRaises(ValueError):
                save_chapter_memory(data_dir, "c1", text, {
                    "narrative_summary": "Summary",
                    "completion_status": "complete",
                    "items": [{
                        "memory_type": "data_or_number",
                        "statement": "Unanchored claim",
                        "source_quote": "not in source",
                        "source_start": 0,
                        "source_end": 14,
                    }],
                })
            state = read_chapter_memory(data_dir, "c1", text)
        self.assertEqual(state["status"], "memory_missing")

    def test_unhashable_memory_type_is_rejected_as_validation_error(self):
        text = "The study reports 12 participants."
        raw_memory = self._memory(text)
        raw_memory["items"][0]["memory_type"] = {"unexpected": "object"}
        with self.assertRaisesRegex(ValueError, "Unsupported chapter memory type"):
            _validated_items(text, raw_memory, "a" * 64)

    def test_memory_snapshot_replacement_is_atomic_on_sqlite_failure(self):
        text = "The study reports 12 participants."
        with tempfile.TemporaryDirectory() as data_dir:
            path = initialize_memory_store(data_dir)
            save_chapter_memory(data_dir, "c1", text, self._memory(text))
            import sqlite3
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "CREATE TRIGGER reject_memory_item BEFORE INSERT ON memory_items "
                    "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
                )
            with self.assertRaises(Exception):
                save_chapter_memory(data_dir, "c1", text, self._memory(text))
            state = read_chapter_memory(data_dir, "c1", text)
        self.assertEqual(state["status"], "current")
        self.assertEqual(state["narrative_summary"], "The study reports a bounded sample.")

    def test_corrupted_memory_item_is_reported_invalid_without_crashing(self):
        import sqlite3

        text = "The study reports 12 participants."
        with tempfile.TemporaryDirectory() as data_dir:
            path = initialize_memory_store(data_dir)
            save_chapter_memory(data_dir, "c1", text, self._memory(text))
            with sqlite3.connect(path) as connection:
                connection.execute("UPDATE memory_items SET memory_type = 'unknown' WHERE chapter_id = 'c1'")
            result = read_chapter_memory(data_dir, "c1", text)
        self.assertEqual(result["status"], "invalid")

    def test_confirming_an_item_is_persisted_without_claiming_truth(self):
        text = "The study reports 12 participants."
        with tempfile.TemporaryDirectory() as data_dir:
            initialize_memory_store(data_dir)
            save_chapter_memory(data_dir, "c1", text, self._memory(text))
            item = read_chapter_memory(data_dir, "c1", text)["items"][0]
            set_memory_item_confirmation(data_dir, item["id"], True)
            confirmed = read_chapter_memory(data_dir, "c1", text)["items"][0]
        self.assertTrue(confirmed["confirmed_for_writing"])
        self.assertEqual(confirmed["verification_status"], "draft_provisional")


class ChapterMemoryExtractionV2Tests(unittest.TestCase):
    def _long_chapter(self, size):
        text = "BEGIN_SENTINEL " + "a" * (size // 2)
        text += " MIDDLE_SENTINEL " + "b" * (size - len(text) - 50)
        return text + " FINAL_CHAPTER_FACT_SENTINEL"

    def test_short_chapter_uses_one_structured_extraction(self):
        calls = []
        text = "The study reports 12 participants."

        def fake_llm(prompt, **kwargs):
            calls.append((prompt, kwargs))
            return structured_result({
                "chunk_status": "content",
                "summary": "A study with 12 participants.",
                "items": [{
                    "memory_type": "data_or_number",
                    "statement": "The study reports 12 participants.",
                    "source_quote": text,
                }],
            })

        result = build_chapter_memory_v2(text, fake_llm, locale="en")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][1]["json_mode"])
        self.assertTrue(calls[0][1]["return_metadata"])
        self.assertEqual(result["items"][0]["source_quote"], text)
        self.assertEqual(text[result["items"][0]["source_start"]:result["items"][0]["source_end"]], text)

    def test_ten_thousand_character_chapter_processes_ordered_first_middle_and_tail(self):
        chapter = self._long_chapter(10_000)
        seen = []

        def fake_llm(prompt, **kwargs):
            source = prompt.split("[CHAPTER TEXT]\n", 1)[1]
            seen.append(source)
            return structured_result(extraction_for_chunk(prompt))

        result = build_chapter_memory_v2(chapter, fake_llm, max_chars=2_000)
        self.assertEqual("".join(seen), chapter)
        self.assertIn("BEGIN_SENTINEL", seen[0])
        self.assertTrue(any("MIDDLE_SENTINEL" in chunk for chunk in seen))
        self.assertIn("FINAL_CHAPTER_FACT_SENTINEL", seen[-1])
        self.assertIn("FINAL_CHAPTER_FACT_SENTINEL", result["narrative_summary"])

    def test_fifty_thousand_character_chapter_preserves_tail_memory_item(self):
        chapter = self._long_chapter(50_000)
        seen = []

        def fake_llm(prompt, **kwargs):
            seen.append(prompt.split("[CHAPTER TEXT]\n", 1)[1])
            return structured_result(extraction_for_chunk(prompt))

        result = build_chapter_memory_v2(chapter, fake_llm, max_chars=5_500)
        self.assertEqual("".join(seen), chapter)
        for sentinel in ("BEGIN_SENTINEL", "MIDDLE_SENTINEL", "FINAL_CHAPTER_FACT_SENTINEL"):
            self.assertIn(sentinel, result["narrative_summary"])
        tail = [item for item in result["items"] if item["statement"].endswith("FINAL_CHAPTER_FACT_SENTINEL.")]
        self.assertTrue(tail)
        item = tail[0]
        self.assertEqual(chapter[item["source_start"]:item["source_end"]], item["source_quote"])

    def test_finish_reason_length_retries_by_splitting_source_and_never_accepts_partial_json(self):
        calls = []

        def fake_llm(prompt, **kwargs):
            source = prompt.split("[CHAPTER TEXT]\n", 1)[1]
            calls.append(source)
            if len(source) > 20:
                return structured_result({"chunk_status": "content", "summary": "truncated"}, "length")
            quote = source[:8]
            return structured_result({
                "chunk_status": "content",
                "summary": "Extracted source segment.",
                "items": [{"memory_type": "finding", "statement": "A source detail.", "source_quote": quote}],
            })

        chapter = "0123456789" * 8
        result = build_chapter_memory_v2(chapter, fake_llm, max_chars=len(chapter))
        self.assertGreater(len(calls), 1)
        self.assertTrue(result["narrative_summary"])
        self.assertTrue(all(chapter[item["source_start"]:item["source_end"]] == item["source_quote"] for item in result["items"]))

    def test_all_empty_or_meaningless_chunks_fail_closed(self):
        def fake_llm(prompt, **kwargs):
            return structured_result({"chunk_status": "no_relevant_content", "summary": "", "items": []})

        with self.assertRaises(MemoryBuildError):
            build_chapter_memory_v2("No extractable academic content.", fake_llm)

    def test_mixed_empty_and_content_chunks_record_each_processed_status(self):
        chapter = "AAAAA BBBBB CCCCC DDDDD"
        calls = []

        def fake_llm(prompt, **kwargs):
            source = prompt.split("[CHAPTER TEXT]\n", 1)[1]
            calls.append(source)
            if "CCCCC" not in source:
                return structured_result({"chunk_status": "no_relevant_content", "summary": "", "items": []})
            return structured_result({"chunk_status": "content", "summary": "The later chunk contains a finding.", "items": []})

        result = build_chapter_memory_v2(chapter, fake_llm, max_chars=12)
        self.assertEqual("".join(calls), chapter)
        self.assertEqual([item["status"] for item in result["chunk_statuses"]], ["no_relevant_content", "content"])
        self.assertEqual([item["chunk_index"] for item in result["chunk_statuses"]], [1, 2])
        self.assertEqual(result["chunks_processed"], 2)

    def test_unverified_source_quotes_are_discarded_and_cannot_be_persisted_as_items(self):
        text = "The study reports 12 participants."

        def fake_llm(prompt, **kwargs):
            return structured_result({
                "chunk_status": "content",
                "summary": "A study summary.",
                "items": [{
                    "memory_type": "finding",
                    "statement": "An unsupported statement.",
                    "source_quote": "A fabricated quotation.",
                }],
            })

        result = build_chapter_memory_v2(text, fake_llm)
        self.assertEqual(result["items"], [])

    def test_repeated_source_quote_uses_a_real_python_character_offset(self):
        quote = "The result remains preliminary."
        text = f"{quote} Earlier note. {quote}"

        def fake_llm(prompt, **kwargs):
            return structured_result({
                "chunk_status": "content",
                "summary": "The result is preliminary.",
                "items": [{"memory_type": "finding", "statement": "The result remains preliminary.", "source_quote": quote}],
            })

        result = build_chapter_memory_v2(text, fake_llm)
        item = result["items"][0]
        self.assertEqual(item["source_start"], text.index(quote))
        self.assertEqual(text[item["source_start"]:item["source_end"]], quote)

    def test_transient_provider_failures_use_bounded_retry_and_then_succeed(self):
        from unittest.mock import patch

        calls = []
        text = "The method uses a structured survey."

        def fake_llm(prompt, **kwargs):
            calls.append(kwargs)
            if len(calls) < 3:
                return {"content": "", "finish_reason": None, "error_code": "server_error"}
            return structured_result({"chunk_status": "content", "summary": "A survey method.", "items": []})

        with patch("chapter_memory_support.time.sleep") as sleep:
            memory = build_chapter_memory_v2(text, fake_llm)
        self.assertEqual(len(calls), 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
        self.assertEqual(memory["completion_status"], "complete")

    def test_non_transient_provider_failure_is_not_retried(self):
        calls = []

        def fake_llm(prompt, **kwargs):
            calls.append(kwargs)
            return {"content": "", "finish_reason": None, "error_code": "authentication_failed"}

        with self.assertRaises(MemoryBuildError):
            build_chapter_memory_v2("A chapter with useful content.", fake_llm)
        self.assertEqual(len(calls), 1)

    def test_content_filter_and_unknown_finish_reason_fail_without_schema_retry(self):
        for finish_reason in ("content_filter", "unknown", None):
            with self.subTest(finish_reason=finish_reason):
                calls = []

                def fake_llm(prompt, **kwargs):
                    calls.append(prompt)
                    return {"content": "{}", "finish_reason": finish_reason, "error_code": None}

                with self.assertRaises(MemoryBuildError):
                    build_chapter_memory_v2("A source chapter.", fake_llm)
                self.assertEqual(len(calls), 1)

    def test_schema_correction_retries_once_and_accepts_corrected_structure(self):
        calls = []
        text = "The study uses a survey."

        def fake_llm(prompt, **kwargs):
            calls.append(prompt)
            if len(calls) == 1:
                return structured_result({"unexpected": "shape"})
            return structured_result({"chunk_status": "content", "summary": "Survey design.", "items": []})

        result = build_chapter_memory_v2(text, fake_llm)
        self.assertEqual(len(calls), 2)
        self.assertIn("previous response", calls[1].lower())
        self.assertTrue(result["narrative_summary"])


class ContextSandwichV2Tests(unittest.TestCase):
    def _save_memory(self, data_dir, chapter_id, body, statement, quote, memory_type="definition", confirmed=False):
        start = body.index(quote)
        save_chapter_memory(data_dir, chapter_id, body, {
            "narrative_summary": f"Summary of {chapter_id}.",
            "completion_status": "complete",
            "items": [{
                "memory_type": memory_type,
                "statement": statement,
                "source_quote": quote,
                "source_start": start,
                "source_end": start + len(quote),
                "confirmed_for_writing": confirmed,
            }],
        })

    def test_written_chapters_without_current_memory_are_not_marked_unwritten(self):
        with tempfile.TemporaryDirectory() as data_dir:
            initialize_memory_store(data_dir)
            outline = [
                {"id": "first", "title": "Background"},
                {"id": "current", "title": "Methods", "desc": "sample design"},
            ]
            context = build_context_sandwich_v2(
                "current", outline, {"first": "A saved background chapter with a source quote."},
                data_dir, locale="en",
            )
        self.assertIn("first", [row["chapter_id"] for row in context["context_manifest"]["missing_or_stale_chapters"]])
        self.assertIn("unverified_draft_excerpt", context["upstream"])
        self.assertNotIn("unwritten", context["upstream"].lower())

    def test_relevant_upstream_and_written_downstream_memories_are_retrieved(self):
        with tempfile.TemporaryDirectory() as data_dir:
            initialize_memory_store(data_dir)
            first = "The framework defines adaptive capacity as a measured construct."
            later = "The result uses the same adaptive capacity construct in the final model."
            self._save_memory(data_dir, "first", first, "Adaptive capacity is the measured construct.", "adaptive capacity")
            self._save_memory(data_dir, "later", later, "The final model uses adaptive capacity.", "adaptive capacity")
            outline = [
                {"id": "first", "title": "Framework", "desc": "adaptive capacity"},
                {"id": "current", "title": "Methods", "desc": "adaptive capacity measurement"},
                {"id": "later", "title": "Final model", "desc": "adaptive capacity"},
                {"id": "unwritten", "title": "Discussion", "desc": "implications"},
            ]
            context = build_context_sandwich_v2(
                "current", outline, {"first": first, "later": later}, data_dir, locale="en",
            )
        self.assertIn("Adaptive capacity is the measured construct.", context["upstream"])
        self.assertIn("The final model uses adaptive capacity.", context["downstream"])
        self.assertIn("unwritten", context["downstream"].casefold())
        self.assertEqual(len(context["context_manifest"]["selected_memory_ids"]), 2)

    def test_legacy_summary_is_not_used_as_confirmed_memory(self):
        with tempfile.TemporaryDirectory() as data_dir:
            initialize_memory_store(data_dir)
            context = build_context_sandwich_v2(
                "current", [{"id": "old", "title": "Old"}, {"id": "current", "title": "Now"}],
                {"old": "A saved chapter body."}, data_dir,
                legacy_summaries={"old": "Legacy free text should not become trusted."}, locale="en",
            )
        self.assertEqual(context["memory_statuses"]["old"], "legacy_unverified")
        self.assertNotIn("Legacy free text should not become trusted.", context["upstream"])

    def test_global_outline_retains_parent_hierarchy_and_prompt_manifest_lists_sources(self):
        with tempfile.TemporaryDirectory() as data_dir:
            initialize_memory_store(data_dir)
            body = "The study defines a stable construct."
            self._save_memory(data_dir, "chapter-1", body, "The construct is stable.", "stable construct", confirmed=True)
            outline = [
                {"id": "parent", "title": "Part One", "desc": "First research stage", "children": [
                    {"id": "chapter-1", "title": "Definitions", "desc": "stable construct"},
                    {"id": "current", "title": "Methods", "desc": "stable construct"},
                ]},
            ]
            context = build_context_sandwich_v2(
                "current", outline, {"chapter-1": body}, data_dir, locale="en",
            )
            fitted = fit_context_sandwich_to_prompt(
                context,
                lambda value: f"{value['global_outline']}\n{value['upstream']}\n{value['downstream']}",
            )
        self.assertIn("Part One", fitted["global_outline"])
        self.assertIn("Definitions", fitted["global_outline"])
        self.assertEqual(fitted["context_manifest"]["selected_chapters"][0]["title"], "Definitions")
        self.assertEqual(len(fitted["confirmed_memory_items"]), 1)

    def test_chinese_keyword_matching_selects_source_grounded_upstream_memory(self):
        with tempfile.TemporaryDirectory() as data_dir:
            initialize_memory_store(data_dir)
            body = "本研究将组织韧性定义为核心构念。"
            self._save_memory(data_dir, "chapter-1", body, "组织韧性是研究核心构念。", "组织韧性")
            context = build_context_sandwich_v2(
                "current",
                [{"id": "chapter-1", "title": "理论", "desc": "组织韧性"}, {"id": "current", "title": "测量", "desc": "组织韧性变量"}],
                {"chapter-1": body}, data_dir, locale="zh",
            )
        self.assertIn("组织韧性是研究核心构念。", context["upstream"])

    def test_distant_unrelated_memory_is_omitted_with_a_reason(self):
        with tempfile.TemporaryDirectory() as data_dir:
            initialize_memory_store(data_dir)
            body = "The chapter describes coastal flood policy."
            self._save_memory(data_dir, "far", body, "Coastal policy changed in 2020.", "coastal flood")
            outline = [
                {"id": "far", "title": "Coastal policy", "desc": "flood resilience"},
                {"id": "middle-1", "title": "Literature review"},
                {"id": "middle-2", "title": "Model design"},
                {"id": "current", "title": "Interview coding", "desc": "participant themes"},
            ]
            context = build_context_sandwich_v2(
                "current", outline, {"far": body}, data_dir, locale="en",
            )
        item_id = context["context_manifest"]["omitted_memory_ids"][0]
        self.assertEqual(context["context_manifest"]["omission_reasons"][str(item_id)], "not_relevant_to_current_chapter")
        self.assertNotIn("Coastal policy changed", context["upstream"])

    def test_prompt_fitting_records_exact_hash_and_never_drops_confirmed_constraint(self):
        context = {
            "current_title": "Methods",
            "upstream": "optional narrative " * 200,
            "downstream": "",
            "_memory_entries": [
                {"memory_id": "required-1", "side": "upstream", "text": "LOCKED_TERM must remain." , "confirmed": True},
                {"memory_id": "optional-2", "side": "upstream", "text": "Optional history " * 100, "confirmed": False},
            ],
            "context_manifest": {"selected_memory_ids": ["required-1", "optional-2"], "omitted_memory_ids": [], "omission_reasons": {}},
        }

        def prompt_builder(value):
            return f"CURRENT {value['current_title']}\n{value['upstream']}\n{value['downstream']}"

        fitted = fit_context_sandwich_to_prompt(context, prompt_builder, max_estimated_tokens=70)
        manifest = fitted["context_manifest"]
        self.assertIn("LOCKED_TERM", prompt_builder(fitted))
        self.assertEqual(manifest["prompt_hash"], hashlib.sha256(prompt_builder(fitted).encode("utf-8")).hexdigest())
        self.assertIn("optional-2", manifest["omitted_memory_ids"])
        self.assertLessEqual(manifest["estimated_prompt_size"], 70)


class DraftLifecycleV2Tests(unittest.TestCase):
    def test_failed_body_save_does_not_call_memory_model_or_change_store(self):
        with tempfile.TemporaryDirectory() as data_dir:
            initialize_memory_store(data_dir)
            calls = []
            result = save_draft_and_maybe_refresh_memory(
                data_dir, {}, "c1", "A saved chapter.",
                lambda drafts: False,
                lambda *args, **kwargs: calls.append((args, kwargs)),
                refresh_memory=True,
            )
        self.assertFalse(result["saved"])
        self.assertEqual(calls, [])

    def test_same_body_reuses_current_memory_and_does_not_recall_model(self):
        body = "The study reports 12 participants."
        memory = {
            "narrative_summary": "A bounded study.", "completion_status": "complete",
            "items": [{
                "memory_type": "data_or_number", "statement": body,
                "source_quote": body, "source_start": 0, "source_end": len(body),
            }],
        }

        with tempfile.TemporaryDirectory() as data_dir:
            initialize_memory_store(data_dir)
            save_chapter_memory(data_dir, "c1", body, memory)
            calls = []
            result = save_draft_and_maybe_refresh_memory(
                data_dir, {"c1": body}, "c1", body, lambda _drafts: True,
                lambda *args, **kwargs: calls.append((args, kwargs)), refresh_memory=True,
            )
        self.assertTrue(result["saved"])
        self.assertTrue(result["memory_reused"])
        self.assertEqual(calls, [])

    def test_saved_body_survives_failed_memory_refresh_and_old_snapshot_becomes_stale(self):
        old = "The study reports 12 participants."
        new = old + " The conclusion remains preliminary."
        memory = {
            "narrative_summary": "Old current snapshot.", "completion_status": "complete",
            "items": [{"memory_type": "data_or_number", "statement": old, "source_quote": old, "source_start": 0, "source_end": len(old)}],
        }

        def failed_llm(*args, **kwargs):
            raise RuntimeError("safe synthetic failure")

        with tempfile.TemporaryDirectory() as data_dir:
            initialize_memory_store(data_dir)
            save_chapter_memory(data_dir, "c1", old, memory)
            result = save_draft_and_maybe_refresh_memory(
                data_dir, {"c1": old}, "c1", new, lambda _drafts: True,
                failed_llm, refresh_memory=True,
            )
            state = read_chapter_memory(data_dir, "c1", new)
        self.assertTrue(result["saved"])
        self.assertFalse(result["memory_updated"])
        self.assertEqual(state["status"], "stale")
        self.assertEqual(state["narrative_summary"], "Old current snapshot.")

    def test_later_chunk_failure_never_persists_a_partial_memory_snapshot(self):
        old = "The study reports 12 participants."
        new = "0123456789abcdefghijKLMNOPQRSTUV"
        old_memory = {
            "narrative_summary": "Previous complete snapshot.",
            "completion_status": "complete",
            "items": [{
                "memory_type": "data_or_number",
                "statement": old,
                "source_quote": old,
                "source_start": 0,
                "source_end": len(old),
            }],
        }
        calls = []

        def fake_llm(prompt, **kwargs):
            calls.append(prompt)
            if len(calls) == 1:
                source = prompt.split("[CHAPTER TEXT]\n", 1)[1]
                return structured_result({"chunk_status": "content", "summary": "Partial segment.", "items": []})
            return {"content": "", "finish_reason": None, "error_code": "authentication_failed"}

        with tempfile.TemporaryDirectory() as data_dir:
            initialize_memory_store(data_dir)
            save_chapter_memory(data_dir, "c1", old, old_memory)
            result = save_draft_and_maybe_refresh_memory(
                data_dir, {"c1": old}, "c1", new, lambda _drafts: True,
                fake_llm, refresh_memory=True, max_chars=12,
            )
            state = read_chapter_memory(data_dir, "c1", new)
        self.assertTrue(result["saved"])
        self.assertEqual(result["memory_error"], "memory_update_failed")
        self.assertEqual(state["status"], "stale")
        self.assertEqual(state["narrative_summary"], "Previous complete snapshot.")
        self.assertEqual(len(calls), 2)

    def test_bilingual_app_draft_save_path_persists_body_and_current_memory_once(self):
        from chapter_memory_support import structured_memory_llm_call

        root = Path(__file__).resolve().parents[1]
        body = "The study uses a survey with 426 respondents."
        for app_name, locale in (("app.py", "en"), ("app_zh.py", "zh")):
            with self.subTest(app=app_name), tempfile.TemporaryDirectory() as data_dir:
                calls = []
                files = {"drafts.json": {}, "drafts_summary.json": {}}

                def load_json(name, default=None):
                    return files.get(name, default)

                def save_json(name, value):
                    files[name] = value
                    return True

                def dispatch(prompt, **kwargs):
                    calls.append((prompt, kwargs))
                    return structured_result({
                        "chunk_status": "content",
                        "summary": "The chapter describes a survey sample.",
                        "items": [{
                            "memory_type": "data_or_number",
                            "statement": "The study uses a survey with 426 respondents.",
                            "source_quote": body,
                        }],
                    })

                source = (root / app_name).read_text(encoding="utf-8")
                tree = ast.parse(source)
                node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "persist_draft_text")
                module_code = compile(ast.Module(body=[node], type_ignores=[]), app_name, "exec")
                function_code = next(item for item in module_code.co_consts if isinstance(item, types.CodeType))
                namespace = {
                    "load_json_file": load_json,
                    "save_json_file": save_json,
                    "DATA_DIR": data_dir,
                    "save_draft_and_maybe_refresh_memory": save_draft_and_maybe_refresh_memory,
                    "structured_memory_llm_call": structured_memory_llm_call,
                    "dispatch_llm_call": dispatch,
                }
                persist = types.FunctionType(function_code, namespace)
                persist.__kwdefaults__ = {"refresh_memory": False, "drafts": None}
                result = persist("chapter-1", body, locale, refresh_memory=True)
                self.assertTrue(result["saved"])
                self.assertTrue(result["memory_updated"])
                self.assertEqual(files["drafts.json"]["chapter-1"], body)
                self.assertEqual(read_chapter_memory(data_dir, "chapter-1", body)["status"], "current")
                reused = persist("chapter-1", body, locale, refresh_memory=True)
                self.assertTrue(reused["memory_reused"])
                self.assertEqual(len(calls), 1)


class ChapterConsistencyV2Tests(unittest.TestCase):
    def test_non_string_review_status_is_rejected_without_type_error(self):
        for status in ([], {}, 1, None):
            with self.subTest(status=status):
                self.assertIsNone(_normalize_review({"status": status, "issues": []}))

    def test_invalid_citation_reference_is_a_hard_deterministic_conflict(self):
        result = check_deterministic_consistency("The result is confirmed [3].", valid_reference_count=1)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["hard_conflicts"][0]["code"], "invalid_citation")

    def test_valid_citation_and_no_locked_conflict_pass_deterministic_check(self):
        result = check_deterministic_consistency("The result is supported [1].", valid_reference_count=1)
        self.assertEqual(result["status"], "pass")

    def test_semantic_review_requires_valid_stop_metadata_and_structured_result(self):
        candidate = "The estimate is preliminary."
        context = {"upstream": "The estimate is preliminary.", "downstream": ""}
        result = review_semantic_consistency(
            candidate, context,
            lambda prompt, **kwargs: structured_result({"status": "pass", "issues": []}),
        )
        self.assertEqual(result["status"], "pass")

    def test_semantic_review_api_failure_is_not_reported_as_pass(self):
        result = review_semantic_consistency(
            "Candidate", {}, lambda prompt, **kwargs: {"content": "", "finish_reason": None, "error_code": "timeout"},
        )
        self.assertEqual(result["status"], "needs_review")
        self.assertFalse(result["completed"])


class GenerationReviewWiringV2Tests(unittest.TestCase):
    def _load_generation_function(self, app_name, locale):
        root = Path(__file__).resolve().parents[1]
        source = (root / app_name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "generate_chapter_with_correction")
        code = compile(ast.Module(body=[function], type_ignores=[]), app_name, "exec")
        function_code = next(item for item in code.co_consts if isinstance(item, types.CodeType))
        calls = []

        def dispatch(prompt, **kwargs):
            calls.append((prompt, kwargs))
            if kwargs.get("json_mode"):
                return structured_result({"status": "pass", "issues": []})
            return "Generated chapter [1]."

        namespace = {
            "st": types.SimpleNamespace(session_state={}),
            "build_chapter_prompt": lambda _sandwich, _prompts, _note=None: "Exact generated prompt",
            "dispatch_llm_call": dispatch,
            "require_valid_llm_output": lambda value: value,
            "count_english_words": lambda _text: 3,
            "count_chinese_chars": lambda _text: 12,
            "save_manifest_for_prompt": __import__("chapter_memory_support").save_manifest_for_prompt,
            "review_chapter_candidate": review_chapter_candidate,
            "LLMOutputError": RuntimeError,
            "locale": locale,
        }
        loaded = types.FunctionType(function_code, namespace)
        loaded.__defaults__ = (3, 0.5)
        return loaded, calls, namespace

    def test_both_apps_manifest_the_actual_prompt_and_review_final_candidate(self):
        for app_name, locale in (("app.py", "en"), ("app_zh.py", "zh")):
            with self.subTest(app=app_name):
                generate, calls, namespace = self._load_generation_function(app_name, locale)
                result = generate({"current_word_count": 0, "current_refs": ["lit-1"], "context_manifest": {}}, {})
                self.assertEqual(result[0], "Generated chapter [1].")
                self.assertEqual(result[2]["status"], "pass")
                self.assertEqual(len(calls), 2)
                self.assertEqual(calls[0][0], "Exact generated prompt")
                self.assertEqual(namespace["st"].session_state["last_generation_context_manifest"]["prompt_characters"], len("Exact generated prompt"))

    def test_hard_conflict_skips_semantic_call_and_requires_review(self):
        calls = []
        result = review_chapter_candidate(
            "Claim without a valid source [9].", {}, 1,
            lambda *args, **kwargs: calls.append((args, kwargs)),
        )
        self.assertEqual(result["status"], "needs_review")
        self.assertTrue(result["hard_conflicts"])
        self.assertEqual(calls, [])

    def test_semantic_warning_is_not_an_automatic_save_pass(self):
        result = review_chapter_candidate(
            "Candidate", {"upstream": "Earlier claim"}, 0,
            lambda prompt, **kwargs: structured_result({
                "status": "warning",
                "issues": [{"reason": "Possible contradiction", "source": "Chapter 1"}],
            }),
        )
        self.assertEqual(result["status"], "warning")
        self.assertTrue(result["completed"])

    def test_semantic_failure_is_not_an_automatic_save_pass(self):
        result = review_chapter_candidate(
            "Candidate", {}, 0,
            lambda prompt, **kwargs: {"content": "", "finish_reason": None, "error_code": "timeout"},
        )
        self.assertEqual(result["status"], "needs_review")
        self.assertFalse(result["completed"])


class ChapterMemoryUIV2Tests(unittest.TestCase):
    def test_bilingual_writing_pages_show_memory_state_and_explicit_rebuild_controls(self):
        from streamlit.testing.v1 import AppTest

        root = Path(__file__).resolve().parents[1]
        cases = (
            ("app.py", "5. Chapter Writing", "Draft saved; memory has not been built yet.", "Rebuild this chapter's memory", "Build memory for existing chapters"),
            ("app_zh.py", "5. 正文写作", "正文已保存，尚未建立记忆。", "重新建立本章记忆", "为已有章节建立记忆"),
        )
        for app_name, writing_page, status, rebuild, batch in cases:
            with self.subTest(app=app_name), tempfile.TemporaryDirectory() as temp_dir:
                app_root = Path(temp_dir)
                for source in root.glob("*.py"):
                    shutil.copy2(source, app_root / source.name)
                shutil.copy2(root / app_name, app_root / "app.py")
                data_dir = app_root / "data"
                data_dir.mkdir()
                (data_dir / "logic_tree.json").write_text(json.dumps([
                    {"id": "part-1", "title": "Part One", "children": [
                        {"id": "chapter-1", "title": "Background", "desc": "Study context"},
                        {"id": "chapter-2", "title": "Methods", "desc": "Study methods"},
                    ]},
                ]), encoding="utf-8")
                (data_dir / "drafts.json").write_text(json.dumps({"chapter-1": "A saved synthetic chapter draft."}), encoding="utf-8")
                (data_dir / "drafts_summary.json").write_text("{}", encoding="utf-8")
                app = AppTest.from_file(str(app_root / "app.py"), default_timeout=30).run()
                self.assertFalse(app.exception)
                app.radio[0].set_value(writing_page).run()
                self.assertFalse(app.exception)
                rendered = "\n".join(item.value for item in (*app.caption, *app.markdown) if hasattr(item, "value"))
                rendered += "\n".join(item.label for item in app.button)
                rendered += "\n".join(item.label for item in app.expander)
                self.assertIn(status, rendered)
                self.assertIn(rebuild, rendered)
                self.assertIn(batch, rendered)

    def test_bilingual_pending_candidate_requires_explicit_save_or_keep(self):
        from streamlit.testing.v1 import AppTest

        root = Path(__file__).resolve().parents[1]
        for locale, save_label, keep_label, unavailable in (
            ("en", "Save this candidate", "Keep the previous draft", "The consistency review did not complete."),
            ("zh", "保存这份候选正文", "保留此前正文", "一致性检查未完成"),
        ):
            with self.subTest(locale=locale), tempfile.TemporaryDirectory() as temp_dir:
                script = Path(temp_dir) / "review_ui.py"
                script.write_text(
                    "import sys\n"
                    f"sys.path.insert(0, {str(root)!r})\n"
                    "import streamlit as st\n"
                    "from chapter_memory_ui import render_pending_candidate_reviews\n"
                    "st.session_state.setdefault('pending_chapter_candidates', {'c1': {'title': 'Synthetic chapter', 'text': 'Candidate body.', 'consistency': {'completed': False, 'status': 'needs_review', 'failure_code': 'provider_error', 'issues': []}}})\n"
                    "def save_candidate(chapter_id, candidate):\n"
                    "    st.session_state['saved_candidate'] = chapter_id\n"
                    "    return {'saved': True, 'memory_error': None}\n"
                    f"render_pending_candidate_reviews(st, save_candidate, locale={locale!r})\n",
                    encoding="utf-8",
                )
                app = AppTest.from_file(str(script), default_timeout=30).run()
                self.assertFalse(app.exception)
                labels = [button.label for button in app.button]
                self.assertIn(save_label, labels)
                self.assertIn(keep_label, labels)
                self.assertTrue(any(unavailable in item.value for item in app.warning))
                next(button for button in app.button if button.label == save_label).click().run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state["saved_candidate"], "c1")


if __name__ == "__main__":
    unittest.main()
