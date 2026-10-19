"""Regression tests for confirmed v1.2.0 RC3 workflow defects."""

import json
import re
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import chapter_memory_support
import chapter_memory_store
import document_support
import format_ui_support
import json_storage_support
import literature_intelligence
import outline_support
import research_support
import structured_output_support
import writing_support
from docx import Document
from format_support import load_builtin_presets
from word_export_support import render_manuscript_docx


def _metadata_response(content, *, finish_reason="stop", error=None):
    return {
        "content": content,
        "finish_reason": finish_reason,
        "structured_mode": "native",
        "error_code": error,
    }


def _valid_literature_chunk():
    return json.dumps({"results": "A supported result", "claims": []})


def _valid_literature_profile():
    return json.dumps({"rating": 4, "category": "Empirical Study", "summary": "A supported study summary."})


class StructuredResponseContractTests(unittest.TestCase):
    def test_truncated_json_response_is_classified_without_fabricated_http_status(self):
        response = types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content='{"facts":[', reasoning_content="SECRET"),
                finish_reason="length",
            )],
            usage=types.SimpleNamespace(completion_tokens=120),
        )

        result = structured_output_support.structured_response_result(response)

        self.assertEqual(result["finish_reason"], "length")
        self.assertEqual(result["error_code"], "truncated_output")
        self.assertEqual(result["completion_tokens"], 120)
        self.assertNotIn("http_status", result)
        self.assertNotIn("SECRET", json.dumps(result))

    def test_empty_final_json_content_is_not_a_successful_structured_response(self):
        response = types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content="", reasoning_content='{"facts": []}'),
                finish_reason="stop",
            )],
        )

        result = structured_output_support.structured_response_result(response)

        self.assertEqual(result["error_code"], "empty_content")
        self.assertEqual(result["content"], "")

    def test_structured_provider_retry_is_bounded_and_skips_authentication_errors(self):
        from structured_output_support import call_structured_with_bounded_retry

        class ProviderFailure(Exception):
            def __init__(self, status):
                super().__init__("secret provider body that must not escape")
                self.status_code = status

        response = types.SimpleNamespace(choices=[types.SimpleNamespace(
            finish_reason="stop", message=types.SimpleNamespace(content='{"facts":[]}'),
        )])
        calls = []
        sleeps = []

        def transient_then_success(**_kwargs):
            calls.append(True)
            if len(calls) == 1:
                raise ProviderFailure(503)
            return response

        result = call_structured_with_bounded_retry(
            transient_then_success, {"model": "fake"}, sleep=sleeps.append,
        )
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [1])

        auth_calls = []
        auth = call_structured_with_bounded_retry(
            lambda **_kwargs: auth_calls.append(True) or (_ for _ in ()).throw(ProviderFailure(401)),
            {"model": "fake"}, sleep=sleeps.append,
        )
        self.assertEqual(auth["error_code"], "authentication_failed")
        self.assertEqual(len(auth_calls), 1)
        self.assertNotIn("secret", json.dumps(auth))

    def test_compatibility_fallback_is_not_repeated_when_its_provider_call_is_transient(self):
        from structured_output_support import call_structured_with_bounded_retry

        class UnsupportedResponseFormat(Exception):
            status_code = 400

        class ProviderBusy(Exception):
            status_code = 503

        calls = []
        sleeps = []

        def create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise UnsupportedResponseFormat("response_format is unsupported")
            if len(calls) == 2:
                raise ProviderBusy("temporary provider error")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                finish_reason="stop", message=types.SimpleNamespace(content='{"status":"ok"}'),
            )])

        result = call_structured_with_bounded_retry(
            create,
            {"response_format": {"type": "json_object"}, "model": "fake"},
            sleep=sleeps.append,
        )

        self.assertEqual(result["structured_mode"], "compatibility")
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(len(calls), 3)
        self.assertIn("response_format", calls[0])
        self.assertTrue(all("response_format" not in item for item in calls[1:]))
        self.assertEqual(sleeps, [1])

    def test_aigc_detector_requires_explicit_valid_probability_and_stop(self):
        self.assertIsNone(writing_support.parse_aigc_detection_response({"content": '{"judgment":"likely"}', "finish_reason": "stop", "error_code": None})["score"])
        self.assertIsNone(writing_support.parse_aigc_detection_response({"content": '{"ai_probability":50}', "finish_reason": "length", "error_code": "truncated_output"})["score"])
        result = writing_support.parse_aigc_detection_response({"content": '```json\n{"ai_probability":72,"judgment":"mixed"}\n```', "finish_reason": "stop", "error_code": None})
        self.assertEqual(result["score"], 72)


class AtomicJsonPersistenceTests(unittest.TestCase):
    def test_failed_serialization_preserves_existing_json_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "drafts.json"
            path.write_text('{"chapter":"KEEP"}', encoding="utf-8")
            with patch.object(json_storage_support.json, "dump", side_effect=TypeError("synthetic serialization failure")):
                with self.assertRaises(TypeError):
                    json_storage_support.atomic_write_json(path, {"chapter": object()})
            self.assertEqual(path.read_text(encoding="utf-8"), '{"chapter":"KEEP"}')


class FormatSpecResponseTests(unittest.TestCase):
    def test_invalid_json_gets_one_schema_correction_and_never_becomes_pending(self):
        valid_spec = load_builtin_presets("format_presets")["General Academic"]
        calls = []

        def llm_call(prompt, **kwargs):
            calls.append((prompt, kwargs))
            value = "not JSON" if len(calls) == 1 else json.dumps(valid_spec, ensure_ascii=False)
            return {"content": value, "finish_reason": "stop", "error_code": None}

        result = format_ui_support.generate_format_spec_from_instructions("Use academic defaults", llm_call, "en")

        self.assertEqual(result["name"], "General Academic")
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(options["json_mode"] and options["return_metadata"] for _, options in calls))
        self.assertIn("did not pass FormatSpec validation", calls[1][0])


class ResearchDocumentChunkContractTests(unittest.TestCase):
    def test_metadata_response_without_stop_is_not_accepted_as_complete_json(self):
        source = "One research fact."

        result = document_support.parse_document_in_chunks(
            source,
            "research background",
            lambda _prompt, **_kwargs: {
                "content": json.dumps({"facts": [{"title": "Fact", "type": "text", "content": "usable"}]}),
                "finish_reason": None,
                "error_code": None,
            },
            parse_response=lambda response: json.loads(response).get("facts"),
            normalize_modules=lambda modules: modules,
            locale="en",
            max_chars=100,
            sleep=lambda _seconds: None,
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["modules"], [])
        self.assertEqual(result["failure_code"], "incomplete_response")

    def test_length_truncation_splits_source_and_processes_both_ordered_halves(self):
        source = "Opening sentence is complete. " + ("MID_SENTENCE_TOKEN " * 4) + "Closing sentence is complete."
        seen = []

        def llm_call(prompt, **kwargs):
            chunk = prompt.split("[DOCUMENT CONTENT]\n", 1)[1]
            seen.append((chunk, kwargs))
            if len(seen) == 1:
                return {"content": '{"facts":[', "finish_reason": "length", "error_code": "truncated_output"}
            return {
                "content": json.dumps({"facts": [{"title": f"part-{len(seen)}", "type": "text", "content": "usable fact"}]}),
                "finish_reason": "stop",
                "error_code": None,
            }

        result = document_support.parse_document_in_chunks(
            source,
            "research background",
            llm_call,
            parse_response=lambda response: json.loads(response).get("facts"),
            normalize_modules=lambda modules: modules,
            locale="en",
            max_chars=len(source) + 10,
            sleep=lambda _seconds: None,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(seen), 3)
        self.assertEqual("".join(chunk for chunk, _ in seen[1:]), source)
        self.assertTrue(all(options.get("json_mode") for _, options in seen))
        self.assertTrue(all(options.get("return_metadata") for _, options in seen))
        self.assertEqual([item["source_chunk"] for item in result["modules"]], [1, 1])

    def test_failure_after_an_intermediate_chunk_returns_no_partial_modules(self):
        source = "A" * 80 + "\n\n" + "B" * 80
        calls = []

        def llm_call(prompt, **kwargs):
            calls.append((prompt, kwargs))
            if len(calls) == 1:
                return {"content": '{"facts":[', "finish_reason": "stop", "error_code": None}
            return {"content": "", "finish_reason": "length", "error_code": "truncated_output"}

        result = document_support.parse_document_in_chunks(
            source,
            "research background",
            llm_call,
            parse_response=lambda response: json.loads(response).get("facts"),
            normalize_modules=lambda modules: modules,
            locale="en",
            max_chars=80,
            sleep=lambda _seconds: None,
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["modules"], [])
        self.assertEqual(result["failure_code"], "truncated_output")


class LiteratureEvidenceFallbackTests(unittest.TestCase):
    def test_synthesis_truncation_gets_one_larger_output_budget_retry(self):
        budgets = []

        def llm_call(prompt, **kwargs):
            if "[SOURCE CHUNK" in prompt:
                return _metadata_response(_valid_literature_chunk())
            budgets.append(kwargs["max_tokens"])
            if len(budgets) == 1:
                return _metadata_response('{"rating":', finish_reason="length", error="truncated_output")
            return _metadata_response(_valid_literature_profile())

        result = literature_intelligence.analyze_literature_document(
            "A synthetic study reports its research method and results.",
            "Synthetic study", llm_call, locale="en", max_chars=2000,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(budgets, [2600, 4000])

    def test_empty_structured_synthesis_gets_one_business_correction_retry(self):
        synthesis_calls = []

        def llm_call(prompt, **kwargs):
            if "[SOURCE CHUNK" in prompt:
                return _metadata_response(_valid_literature_chunk())
            synthesis_calls.append(prompt)
            if len(synthesis_calls) == 1:
                return _metadata_response("", error="empty_content")
            return _metadata_response(_valid_literature_profile())

        result = literature_intelligence.analyze_literature_document(
            "A synthetic study reports its research method and results.",
            "Synthetic study", llm_call, locale="en", max_chars=2000,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(synthesis_calls), 2)
        self.assertIn("previous output was invalid", synthesis_calls[1].lower())

    def test_explicitly_bound_nonmatching_evidence_is_used_after_relevant_items(self):
        evidence = {
            "bound": [
                {"literature_id": "bound", "chunk_index": 1, "claim": "sampling methods", "evidence_text": "MATCH"},
            ],
            "bound-no-match": [
                {"literature_id": "bound-no-match", "chunk_index": 1, "claim": "different topic", "evidence_text": "BOUND_FALLBACK"},
            ],
            "unbound": [{"literature_id": "unbound", "chunk_index": 1, "claim": "sampling methods", "evidence_text": "DO_NOT_USE"}],
        }

        selected = literature_intelligence.select_literature_evidence_for_chapter(
            evidence, ["bound", "bound-no-match"], "sampling methods", limit=2
        )

        self.assertEqual([item["evidence_text"] for item in selected], ["MATCH", "BOUND_FALLBACK"])
        self.assertEqual(selected[1]["selection_reason"], "bound_source_fallback")

    def test_evidence_numbering_uses_same_valid_reference_order_as_chapter_catalog(self):
        evidence = {
            "deleted-id": [{"literature_id": "deleted-id", "claim": "claim", "evidence_text": "STALE"}],
            "valid-id": [{"literature_id": "valid-id", "claim": "methods", "evidence_text": "VALID_EVIDENCE"}],
        }

        rendered = literature_intelligence.format_literature_evidence_for_chapter(
            evidence,
            ["deleted-id", "valid-id"],
            "methods",
            locale="en",
            literatures=[{"id": "valid-id", "title": "Valid source"}],
        )

        self.assertIn("[1] Valid source", rendered)
        self.assertNotIn("[2] Valid source", rendered)
        self.assertNotIn("STALE", rendered)


class WordParagraphIntegrityTests(unittest.TestCase):
    def test_deleted_literature_identity_is_detected_from_saved_local_citations(self):
        cited = writing_support.chapters_citing_literature(
            [{"id": "leaf", "title": "Methods", "children": []}],
            {"leaf": "The method follows the source [1]."},
            {"leaf": ["removed-literature"]},
            "removed-literature",
        )
        unrelated = writing_support.chapters_citing_literature(
            [{"id": "leaf", "title": "Methods", "children": []}],
            {"leaf": "The method follows the source [2]."},
            {"leaf": ["removed-literature"]},
            "removed-literature",
        )

        self.assertEqual(cited, ["Methods"])
        self.assertEqual(unrelated, [])

    def test_export_blocks_a_saved_citation_whose_literature_record_is_missing(self):
        spec = load_builtin_presets("format_presets")["General Academic"]
        with self.assertRaisesRegex(ValueError, "literature record that no longer exists"):
            render_manuscript_docx(
                tree=[{"id": "leaf", "title": "Methods", "children": []}],
                drafts={"leaf": "The study cites the missing source [1]."},
                draft_reference_maps={"leaf": ["removed-literature"]},
                literatures=[], drafts_charts={}, drafts_images={}, format_spec=spec,
            )

    def test_export_blocks_numeric_citations_without_a_valid_saved_reference_snapshot(self):
        spec = load_builtin_presets("format_presets")["General Academic"]
        for citation_map, body in (({}, "Unmapped source [1]."), ({"leaf": ["valid-id"]}, "Out-of-range source [2].")):
            with self.subTest(citation_map=citation_map, body=body), self.assertRaisesRegex(ValueError, "reference snapshot"):
                render_manuscript_docx(
                    tree=[{"id": "leaf", "title": "Methods", "children": []}],
                    drafts={"leaf": body}, draft_reference_maps=citation_map,
                    literatures=[{"id": "valid-id", "title": "Valid source"}],
                    drafts_charts={}, drafts_images={}, format_spec=spec,
                )

    def test_export_keeps_single_newline_paragraphs_and_numbered_headings_and_omits_parent_drafts(self):
        spec = load_builtin_presets("format_presets")["General Academic"]
        output = render_manuscript_docx(
            tree=[{
                "id": "parent", "title": "Chapter", "children": [
                    {"id": "leaf", "title": "Methods", "children": []},
                ],
            }],
            drafts={
                "parent": "PARENT_STALE_DRAFT",
                "leaf": "1. Introduction\nFirst paragraph.\nSecond paragraph.",
            },
            draft_reference_maps={},
            literatures=[],
            drafts_charts={},
            drafts_images={},
            format_spec=spec,
        )
        document = Document(output)
        paragraph_texts = [paragraph.text for paragraph in document.paragraphs]

        self.assertIn("1. Introduction", paragraph_texts)
        self.assertIn("First paragraph.", paragraph_texts)
        self.assertIn("Second paragraph.", paragraph_texts)
        self.assertNotIn("PARENT_STALE_DRAFT", "\n".join(paragraph_texts))
        self.assertFalse(any("First paragraph.\nSecond paragraph." == value for value in paragraph_texts))


class ResearchPlanningRoleTests(unittest.TestCase):
    def test_mixed_valid_and_invalid_fact_modules_do_not_silently_import_a_partial_chunk(self):
        response = json.dumps({"facts": [
            {"title": "valid", "type": "text", "content": "SUPPORTED_FACT"},
            {"title": "incomplete table", "type": "table", "columns": ["A"], "data": []},
        ]})

        result = research_support.parse_research_source(
            "A source with a valid paragraph and an incomplete table.",
            "methods",
            lambda *_args, **_kwargs: response,
            locale="en",
            max_chars=1000,
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["facts"], [])
        self.assertEqual(result["failure_stage"], "schema_validation_failure")

    def test_reviewed_fact_role_is_retained_in_planning_context(self):
        base = research_support.create_default_research_base()
        base["sections"]["methods"]["modules"] = [{
            "id": "fact", "type": "text", "title": "Sampling procedure",
            "content": "Participants were selected from the registry.",
            "role": "Use to define the sampling frame in Chapter 3.",
            "tags": [], "allow_writing_grounding": True,
        }]

        digest = research_support.build_research_planning_context(base)

        self.assertIn("Use to define the sampling frame in Chapter 3.", digest)

    def test_table_cells_and_hard_constraints_do_not_consume_fact_relevance_slots(self):
        base = research_support.create_default_research_base()
        base["sections"]["constraints"]["modules"] = [
            {"id": f"constraint-{index}", "type": "text", "title": f"Constraint {index}",
             "content": "Use only the approved sample.", "tags": ["hard_constraint"], "allow_writing_grounding": True}
            for index in range(1, 10)
        ]
        base["sections"]["methods"]["modules"] = [{
            "id": "table-fact", "type": "table", "title": "Sampling frame", "columns": ["Method", "Scope"],
            "data": [["stratified sampling", "graduate students"]], "tags": [],
            "binding_mode": "auto", "allow_writing_grounding": True,
        }]

        selected = research_support.select_research_grounding_for_chapter(base, "Stratified sampling", limit=2)

        self.assertIn("table-fact", [fact["id"] for fact in selected])


class ForwardOutlineContractTests(unittest.TestCase):
    def test_outline_duplicate_ids_invalid_children_and_unknown_references_fail_closed(self):
        valid = [{"id": "a", "title": "Methods", "word_count": 100, "references": [1], "children": []}]
        self.assertIsNone(outline_support.validate_forward_outline_tree(valid, 1))
        duplicate = [dict(valid[0]), dict(valid[0])]
        self.assertEqual(outline_support.validate_forward_outline_tree(duplicate, 1), "invalid_or_duplicate_id")
        bad_children = [{**valid[0], "children": {}}]
        self.assertEqual(outline_support.validate_forward_outline_tree(bad_children, 1), "invalid_children")
        bad_refs = [{**valid[0], "references": [2]}]
        self.assertEqual(outline_support.validate_forward_outline_tree(bad_refs, 1), "invalid_references")

    def test_outline_replacement_requires_saved_drafts_to_remain_editable_leaves(self):
        tree = [{"id": "container", "title": "Chapter", "children": [{"id": "leaf", "title": "Methods", "children": []}]}]
        self.assertTrue(outline_support.forward_outline_preserves_draft_ids(tree, {"leaf": "saved draft"}))
        self.assertFalse(outline_support.forward_outline_preserves_draft_ids(tree, {"container": "stale draft"}))


class MemoryLengthBoundaryTests(unittest.TestCase):
    def test_truncated_memory_retry_splits_near_sentence_boundary_without_losing_source(self):
        source = "Opening sentence is complete. " + ("unbrokenlongtoken " * 5) + "Closing sentence is complete."
        seen_chunks = []

        def llm_call(prompt, **kwargs):
            chunk = prompt.split("[CHAPTER TEXT]\n", 1)[1]
            seen_chunks.append(chunk)
            if len(seen_chunks) == 1:
                return {"content": '{"chunk_status":', "finish_reason": "length", "error_code": "truncated_output"}
            return {
                "content": json.dumps({"chunk_status": "no_relevant_content", "summary": "", "items": []}),
                "finish_reason": "stop", "error_code": None,
            }

        chunks = chapter_memory_support._extract_source_segment(source, 0, 1, 1, "en", llm_call)

        self.assertEqual("".join(seen_chunks[1:]), source)
        self.assertTrue(seen_chunks[1].rstrip().endswith("."))
        self.assertEqual(len(chunks), 2)

    def test_unchanged_confirmed_fact_inherits_confirmation_after_rebuild(self):
        old_text = "The baseline was 42 units."
        new_text = old_text + " A later paragraph adds a new detail."
        quote = "The baseline was 42 units."
        memory = {
            "narrative_summary": "The chapter fixes a baseline measurement.",
            "completion_status": "complete",
            "items": [{
                "memory_type": "data_or_number", "statement": "The baseline was 42 units.",
                "source_quote": quote, "source_start": 0, "source_end": len(quote),
                "confirmed_for_writing": False,
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            chapter_memory_store.initialize_memory_store(directory)
            chapter_memory_store.save_chapter_memory(directory, "ch-1", old_text, memory)
            old = chapter_memory_store.read_chapter_memory(directory, "ch-1", old_text)
            self.assertTrue(chapter_memory_store.set_memory_item_confirmation(directory, old["items"][0]["id"], True))

            def llm_call(_prompt, **_kwargs):
                return {
                    "content": json.dumps({
                        "chunk_status": "content", "summary": "The chapter fixes a baseline measurement.",
                        "items": [{"memory_type": "data_or_number", "statement": "The baseline was 42 units.", "source_quote": quote}],
                    }),
                    "finish_reason": "stop", "error_code": None,
                }

            chapter_memory_support.refresh_chapter_memory_snapshot(directory, "ch-1", new_text, llm_call)
            current = chapter_memory_store.read_chapter_memory(directory, "ch-1", new_text)
            self.assertEqual(current["status"], "current")
            self.assertTrue(current["items"][0]["confirmed_for_writing"])


if __name__ == "__main__":
    unittest.main()
