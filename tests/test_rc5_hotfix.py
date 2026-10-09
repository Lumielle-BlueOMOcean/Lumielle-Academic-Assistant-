"""Regression coverage for the v1.1.1 RC5 literature chunk contract."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import literature_intelligence as literature
import literature_ui_support as literature_ui


ROOT = Path(__file__).resolve().parents[1]


def _response(value, *, finish_reason="stop"):
    content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return {
        "content": content,
        "finish_reason": finish_reason,
        "structured_mode": "native",
        "http_status": 200,
        "error_code": None,
    }


def _empty_chunk(status="no_relevant_content"):
    return {
        "chunk_status": status,
        "research_question": "",
        "theory": "",
        "method": "",
        "sample": "",
        "data": "",
        "results": "",
        "claims": [],
        "limitations": "",
        "conclusion": "",
        "definitions": "",
    }


def _profile():
    return {"rating": 4, "category": "Empirical Study", "summary": "Merged academic findings"}


class ChunkStatusContractTests(unittest.TestCase):
    def test_legacy_and_explicit_content_normalize_to_content_status(self):
        for value in (
            {"method": "Case study", "results": "Finding A", "claims": []},
            {"chunk_status": "content", "method": "Survey", "claims": []},
        ):
            with self.subTest(value=value):
                normalized = literature.normalize_chunk_extraction(value)
                self.assertIsNotNone(normalized)
                self.assertEqual(normalized.get("chunk_status"), "content")
                self.assertTrue(normalized["method"] or normalized["results"])

    def test_explicit_canonical_empty_chunk_is_accepted(self):
        normalized = literature.normalize_chunk_extraction(_empty_chunk())
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["chunk_status"], "no_relevant_content")
        self.assertEqual(normalized["claims"], [])
        self.assertTrue(all(not normalized[field] for field in literature._CHUNK_TEXT_FIELDS))

    def test_explicit_empty_chunk_requires_exact_canonical_field_set(self):
        malformed = (
            _empty_chunk() | {"analysis": {"method": "Survey"}},
            _empty_chunk() | {"analysis": {}},
            _empty_chunk() | {"unexpected_field": ""},
            {key: value for key, value in _empty_chunk().items() if key != "definitions"},
        )
        validate = literature._validate_chunk_extraction_result
        for value in malformed:
            with self.subTest(value=value):
                result = validate(value)
                self.assertIsNone(result.normalized)
                self.assertEqual(result.issue_code, "inconsistent_no_relevant_content")

    def test_content_and_legacy_aliases_keep_their_existing_extra_field_behavior(self):
        content_with_extras = _empty_chunk("content") | {
            "method": "Survey", "analysis": {"method": "ignored wrapper value"}, "unexpected_field": "ignored"
        }
        normalized = literature.normalize_chunk_extraction(content_with_extras)
        self.assertEqual(normalized["chunk_status"], "content")
        self.assertEqual(normalized["method"], "Survey")

        legacy = literature.normalize_chunk_extraction({"methodology": "Interview", "findings": "Finding A"})
        self.assertEqual(legacy["method"], "Interview")
        self.assertEqual(legacy["results"], "Finding A")

    def test_empty_without_status_and_content_status_without_content_have_issue_codes(self):
        for value, expected in (
            (_empty_chunk(status=None) | {"chunk_status": None}, "invalid_chunk_status"),
            ({key: item for key, item in _empty_chunk().items() if key != "chunk_status"}, "empty_without_explicit_status"),
            (_empty_chunk("content"), "no_meaningful_content"),
        ):
            with self.subTest(expected=expected):
                validate = getattr(literature, "_validate_chunk_extraction_result", None)
                self.assertTrue(callable(validate))
                result = validate(value)
                self.assertIsNone(result.normalized)
                self.assertEqual(result.issue_code, expected)

    def test_invalid_and_contradictory_statuses_are_rejected(self):
        invalid = _empty_chunk("unknown")
        contradictory = _empty_chunk() | {"results": "A real finding"}
        for value, expected in (
            (invalid, "invalid_chunk_status"),
            (contradictory, "inconsistent_no_relevant_content"),
        ):
            with self.subTest(expected=expected):
                validate = getattr(literature, "_validate_chunk_extraction_result", None)
                self.assertTrue(callable(validate))
                self.assertEqual(validate(value).issue_code, expected)

    def test_empty_garbage_and_unsupported_wrappers_remain_invalid(self):
        for value, expected in (
            ({}, "empty_without_explicit_status"),
            ({"foo": "PRIVATE_VALUE"}, "unsupported_wrapper_shape"),
            ({"status": "ok"}, "unsupported_wrapper_shape"),
            ({"analysis": {"method": "Survey"}}, "unsupported_wrapper_shape"),
            ({"claims": []}, "empty_without_explicit_status"),
        ):
            with self.subTest(value=value):
                validate = getattr(literature, "_validate_chunk_extraction_result", None)
                self.assertTrue(callable(validate))
                result = validate(value)
                self.assertIsNone(result.normalized)
                self.assertEqual(result.issue_code, expected)

    def test_invalid_academic_field_and_claim_shapes_have_distinct_issues(self):
        cases = (
            ({"method": {"bad": object()}}, "invalid_academic_field"),
            ({"method": "Survey", "claims": 7}, "invalid_claims_container"),
            ({"method": "Survey", "claims": [["nested"]]}, "invalid_claim_item"),
        )
        for value, expected in cases:
            with self.subTest(expected=expected):
                validate = getattr(literature, "_validate_chunk_extraction_result", None)
                self.assertTrue(callable(validate))
                result = validate(value)
                self.assertIsNone(result.normalized)
                self.assertEqual(result.issue_code, expected)

    def test_chunk_prompt_explains_status_with_complete_examples_in_both_languages(self):
        for locale in ("en", "zh"):
            with self.subTest(locale=locale):
                prompt = literature.build_chunk_extraction_prompt("Study", "SOURCE", 1, 1, locale)
                self.assertIn('"chunk_status":"content"', prompt)
                self.assertIn('"chunk_status":"no_relevant_content"', prompt)
                self.assertIn('"claims":[]', prompt)
                examples = [json.loads(line) for line in prompt.splitlines() if line.startswith('{"chunk_status"')]
                self.assertEqual(len(examples), 2)
                self.assertEqual(examples[0]["chunk_status"], "content")
                self.assertEqual(examples[0]["method"], "Survey" if locale == "en" else "问卷调查")
                self.assertEqual(examples[0]["claims"], [])
                self.assertEqual(examples[1], _empty_chunk())
                correction = literature.build_chunk_extraction_prompt("Study", "SOURCE", 1, 1, locale, True)
                self.assertTrue(any(term in correction.lower() for term in ("no_relevant_content", "学术内容")))

    def test_schema_correction_can_recover_empty_legacy_response_once(self):
        prompts = []
        chunk_calls = 0

        def llm(prompt, **_kwargs):
            nonlocal chunk_calls
            prompts.append(prompt)
            if "[DOCUMENT SYNTHESIS]" in prompt:
                return _response(_profile())
            if "[SOURCE CHUNK" in prompt:
                chunk_calls += 1
                empty_legacy = {key: value for key, value in _empty_chunk().items() if key != "chunk_status"}
                return _response(empty_legacy if chunk_calls == 1 else {"method": "Recovered method", "claims": []})
            self.fail("Unexpected LLM call")

        result = literature.analyze_literature_document("source text", "Study", llm, max_chars=100)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(sum("[SOURCE CHUNK" in prompt for prompt in prompts), 2)
        self.assertTrue(any("no_relevant_content" in prompt for prompt in prompts[1:]))


class EmptyChunkPipelineTests(unittest.TestCase):
    def test_content_empty_content_synthesizes_in_order_without_retry_or_evidence_from_empty(self):
        chunks = [
            "EARLY_SOURCE EXACT_EARLY_QUOTE", "BIBLIOGRAPHY_ONLY_SENTINEL",
            "MIDDLE_SOURCE EXACT_MIDDLE_QUOTE", "PUBLICATION_METADATA_SENTINEL", "TAIL_SOURCE EXACT_TAIL_QUOTE",
        ]
        prompts = []

        def llm(prompt, **_kwargs):
            prompts.append(prompt)
            if "[DOCUMENT SYNTHESIS]" in prompt:
                self.assertIn("EARLY_FACT", prompt)
                self.assertIn("MIDDLE_FACT", prompt)
                self.assertIn("TAIL_FACT", prompt)
                self.assertNotIn("BIBLIOGRAPHY_ONLY_SENTINEL", prompt)
                self.assertNotIn("PUBLICATION_METADATA_SENTINEL", prompt)
                return _response(_profile())
            match = re.search(r"Source locator: ([0-9.]+)", prompt)
            locator = match.group(1)
            if locator == "1":
                return _response({"method": "EARLY_FACT", "claims": [{"claim": "early", "evidence_text": "EXACT_EARLY_QUOTE"}]})
            if locator == "2":
                return _response(_empty_chunk())
            if locator == "3":
                return _response({"results": "MIDDLE_FACT", "claims": [{"claim": "middle", "evidence_text": "EXACT_MIDDLE_QUOTE"}]})
            if locator == "4":
                return _response(_empty_chunk())
            return _response({"results": "TAIL_FACT", "claims": [{"claim": "tail", "evidence_text": "EXACT_TAIL_QUOTE"}]})

        with patch.object(literature, "chunk_document_text", return_value=chunks):
            result = literature.analyze_literature_document("whole document", "Study", llm)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(sum("[SOURCE CHUNK" in prompt for prompt in prompts), 5)
        self.assertEqual([item["evidence_text"] for item in result["evidence"]], [
            "EXACT_EARLY_QUOTE", "EXACT_MIDDLE_QUOTE", "EXACT_TAIL_QUOTE",
        ])
        self.assertEqual(result["diagnostic"]["empty_source_locators"], ["2", "4"])
        self.assertEqual(result["diagnostic"]["processed_source_locators"], ["1", "2", "3", "4", "5"])

    def test_all_explicit_empty_chunks_fail_without_synthesis_or_profile(self):
        prompts = []

        def llm(prompt, **_kwargs):
            prompts.append(prompt)
            if "[DOCUMENT SYNTHESIS]" in prompt:
                self.fail("All-empty input must not synthesize a profile")
            return _response(_empty_chunk())

        with patch.object(literature, "chunk_document_text", return_value=["metadata one", "metadata two"]):
            result = literature.analyze_literature_document("complete source", "Metadata", llm)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["failure_code"], "no_academic_content")
        self.assertIsNone(result["profile"])
        self.assertEqual(result["evidence"], [])
        self.assertEqual(len(prompts), 2)
        self.assertEqual(result["diagnostic"]["empty_source_locators"], ["1", "2"])
        self.assertIn("no academic content", literature_ui.failure_message(result, "en").lower())
        self.assertIn("AI 未从该文献中提取到可分析的学术内容", literature_ui.failure_message(result, "zh"))

    def test_empty_without_status_fails_with_schema_reason_after_one_correction(self):
        calls = 0
        legacy_empty = {key: value for key, value in _empty_chunk().items() if key != "chunk_status"}

        def llm(prompt, **_kwargs):
            nonlocal calls
            self.assertIn("[SOURCE CHUNK", prompt)
            calls += 1
            return _response(legacy_empty)

        result = literature.analyze_literature_document("source", "Study", llm, max_chars=100)
        self.assertEqual(result["failure_code"], "schema_validation_failure")
        self.assertEqual(result["diagnostic"].get("schema_issue_code"), "empty_without_explicit_status")
        self.assertEqual(calls, 2)

    def test_empty_profile_json_keeps_shared_empty_output_classification(self):
        def llm(prompt, **_kwargs):
            if "[DOCUMENT SYNTHESIS]" in prompt:
                return _response({})
            return _response({"method": "Survey"})

        with patch.object(literature, "normalize_profile", wraps=literature.normalize_profile) as normalizer:
            result = literature.analyze_literature_document("source", "Study", llm, max_chars=100)

        self.assertEqual(result["failure_code"], "empty_structured_output")
        self.assertEqual(result["failure_stage"], "document_synthesis")
        normalizer.assert_not_called()

    def test_adaptive_split_can_finish_with_one_content_and_one_empty_child(self):
        parent = "CONTENT_CHILD_SENTINEL " + ("a" * 950) + " EMPTY_CHILD_SENTINEL " + ("b" * 950)
        left = parent[:len(parent) // 2]
        right = parent[len(parent) // 2:]
        self.assertEqual(left + right, parent)
        prompts = []

        def llm(prompt, **_kwargs):
            prompts.append(prompt)
            if "[DOCUMENT SYNTHESIS]" in prompt:
                self.assertIn("CONTENT_FACT", prompt)
                self.assertNotIn("EMPTY_CHILD_SENTINEL", prompt)
                return _response(_profile())
            locator = re.search(r"Source locator: ([0-9.]+)", prompt).group(1)
            if locator == "1":
                return _response("{", finish_reason="length")
            if locator == "1.1":
                return _response({"method": "CONTENT_FACT", "claims": [{"claim": "supported", "evidence_text": "CONTENT_CHILD_SENTINEL"}]})
            return _response(_empty_chunk())

        with patch.object(literature, "chunk_document_text", return_value=[parent]), patch.object(
            literature, "split_source_chunk_for_retry", return_value=(left, right)
        ):
            result = literature.analyze_literature_document(parent, "Study", llm, max_chars=len(parent) + 1)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["diagnostic"]["empty_source_locators"], ["1.2"])
        self.assertEqual([item["source_locator"] for item in result["evidence"]], ["chunk 1.1"])

    def test_reduction_rejects_empty_source_status(self):
        self.assertIsNone(literature._reduction_extraction(_empty_chunk()))

    def test_extra_wrapper_on_empty_leaf_fails_closed_and_preserves_existing_record(self):
        chunks = ["FIRST_SOURCE EXACT_OLD_QUOTE", "SECOND_SOURCE with no supported academic fields"]
        hidden_key = "PRIVATE_DOCUMENT_TEXT_SENTINEL"
        hidden_value = "HIDDEN_ACADEMIC_TEXT_SENTINEL"
        chunk_two_calls = 0
        synthesis_calls = 0

        def llm(prompt, **_kwargs):
            nonlocal chunk_two_calls, synthesis_calls
            if "[DOCUMENT SYNTHESIS]" in prompt:
                synthesis_calls += 1
                return _response(_profile())
            locator = re.search(r"Source locator: ([0-9.]+)", prompt).group(1)
            if locator == "1":
                return _response({"method": "Survey", "claims": [
                    {"claim": "Old evidence claim", "evidence_text": "EXACT_OLD_QUOTE"},
                ]})
            chunk_two_calls += 1
            invalid_empty = _empty_chunk() | {
                "analysis": {"method": hidden_value}, hidden_key: "API_KEY_SENTINEL",
            }
            return _response(invalid_empty)

        existing_record = {"id": "lit-7", "analysis": {"summary": "KEEP_OLD_PROFILE"}, "rating": 4}
        existing_evidence = {"lit-7": [{"evidence_text": "KEEP_OLD_EVIDENCE"}]}
        with patch.object(literature, "chunk_document_text", return_value=chunks):
            result = literature.analyze_literature_document("COMPLETE_SOURCE_SENTINEL", "Study", llm)
        updated_record, updated_evidence, applied = literature.apply_analysis_to_literature_record(
            existing_record, existing_evidence, result,
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["failure_code"], "schema_validation_failure")
        self.assertEqual(result["diagnostic"].get("schema_issue_code"), "inconsistent_no_relevant_content")
        self.assertEqual(result["diagnostic"].get("chunk_index"), 2)
        self.assertEqual(result["diagnostic"].get("source_locator"), "2")
        self.assertEqual(chunk_two_calls, 2)
        self.assertEqual(synthesis_calls, 0)
        self.assertIsNone(result["profile"])
        self.assertEqual(result["evidence"], [])
        self.assertFalse(applied)
        self.assertIs(updated_record, existing_record)
        self.assertEqual(updated_evidence, existing_evidence)

        notice = literature_ui._safe_batch_notice([{
            "filename": "paper.pdf", "success": False, "source_saved": True, "result": result,
        }])
        diagnostic_text = repr(notice) + "\n" + "\n".join(
            literature_ui._technical_diagnostic_lines(notice[0], "en")
        )
        self.assertIn("inconsistent_no_relevant_content", diagnostic_text)
        self.assertNotIn(hidden_key, diagnostic_text)
        self.assertNotIn(hidden_value, diagnostic_text)
        self.assertNotIn("API_KEY_SENTINEL", diagnostic_text)
        self.assertNotIn("COMPLETE_SOURCE_SENTINEL", diagnostic_text)


class ConservativeClaimsAndDiagnosticsTests(unittest.TestCase):
    def test_empty_claim_junk_is_dropped_and_non_string_evidence_is_never_coerced(self):
        normalized = literature.normalize_chunk_extraction({
            "method": "Survey",
            "claims": [
                None, "", "   ", {}, {"claim": "", "evidence_text": ""},
                {"claim": "supported", "evidence_text": None},
                {"claim": "list quote", "evidence_text": ["not a quote"]},
                {"claim": "dict quote", "evidence_text": {"quote": "not a quote"}},
                {"claim": "exact", "evidence_text": "EXACT SOURCE QUOTE"},
            ],
        })
        self.assertIsNotNone(normalized)
        self.assertEqual([claim["claim"] for claim in normalized["claims"]], ["supported", "list quote", "dict quote", "exact"])
        self.assertEqual([claim["evidence_text"] for claim in normalized["claims"]], ["", "", "", "EXACT SOURCE QUOTE"])

    def test_invalid_claim_container_and_nested_claims_remain_rejected(self):
        for value, expected in (({"method": "Survey", "claims": 3}, "invalid_claims_container"),
                                ({"method": "Survey", "claims": [[{"claim": "nested"}]]}, "invalid_claim_item")):
            validate = getattr(literature, "_validate_chunk_extraction_result", None)
            self.assertTrue(callable(validate))
            self.assertEqual(validate(value).issue_code, expected)

    def test_non_string_evidence_is_retained_as_claim_but_never_saved_as_evidence(self):
        def llm(prompt, **_kwargs):
            if "[DOCUMENT SYNTHESIS]" in prompt:
                self.assertIn("valid claim", prompt)
                return _response(_profile())
            return _response({"method": "Survey", "claims": [
                {"claim": "valid claim", "evidence_text": ["EXACT SOURCE QUOTE"]},
            ]})

        result = literature.analyze_literature_document("EXACT SOURCE QUOTE", "Study", llm, max_chars=100)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["evidence"], [])

    def test_schema_issue_codes_survive_validation_failure_without_response_content(self):
        cases = (
            ({"method": "Survey", "claims": [["nested"]]}, "invalid_claim_item"),
            ({"method": {"a": {"b": {"c": {"d": {"e": {"f": {"g": "deep"}}}}}}}, "claims": []}, "invalid_academic_field"),
        )
        for response, issue_code in cases:
            with self.subTest(issue_code=issue_code):
                def llm(prompt, **_kwargs):
                    if "[DOCUMENT SYNTHESIS]" in prompt:
                        self.fail("Schema failure must stop before synthesis")
                    return _response(response)

                result = literature.analyze_literature_document("source", "Study", llm, max_chars=100)
                self.assertEqual(result["failure_code"], "schema_validation_failure")
                self.assertEqual(result["diagnostic"].get("schema_issue_code"), issue_code)
                self.assertEqual(result["diagnostic"].get("chunk_index"), 1)
                self.assertEqual(result["diagnostic"].get("source_locator"), "1")
                self.assertNotIn("deep", repr(result["diagnostic"]))

    def test_schema_diagnostic_is_enumerated_and_does_not_echo_unknown_keys_or_values(self):
        private_key = "PRIVATE_DOCUMENT_TEXT_SENTINEL"
        private_value = "API_SECRET_AND_SOURCE_SENTINEL"

        def llm(prompt, **_kwargs):
            if "[DOCUMENT SYNTHESIS]" in prompt:
                self.fail("Invalid chunk schema must fail before synthesis")
            return _response({"chunk_status": "invalid-status", "claims": [None, {}, "bad"], private_key: private_value})

        result = literature.analyze_literature_document("source text", "Study", llm, max_chars=100)
        diagnostic = result["diagnostic"]
        rendered = repr(diagnostic)
        self.assertEqual(result["failure_code"], "schema_validation_failure")
        self.assertEqual(diagnostic.get("schema_issue_code"), "invalid_chunk_status")
        self.assertEqual(diagnostic["chunk_index"], 1)
        self.assertEqual(diagnostic["source_locator"], "1")
        self.assertEqual(diagnostic["claims_item_types"], {"null": 1, "dict": 1, "str": 1})
        self.assertEqual(diagnostic["unknown_top_level_key_count"], 1)
        self.assertNotIn(private_key, rendered)
        self.assertNotIn(private_value, rendered)

    def test_known_wrapper_is_diagnosed_but_not_auto_unwrapped(self):
        def llm(prompt, **_kwargs):
            return _response({"analysis": {"method": "Survey", "results": "Finding"}})

        result = literature.analyze_literature_document("source", "Study", llm, max_chars=100)
        self.assertEqual(result["failure_code"], "schema_validation_failure")
        self.assertEqual(result["diagnostic"].get("schema_issue_code"), "unsupported_wrapper_shape")
        self.assertEqual(result["diagnostic"].get("wrapper_shapes"), {"analysis": "dict"})
        self.assertIsNone(result["profile"])

    def test_safe_batch_notice_preserves_only_whitelisted_schema_diagnostics(self):
        private = "PRIVATE_DOCUMENT_TEXT_SENTINEL"
        notice = literature_ui._safe_batch_notice([{
            "filename": "paper.pdf", "success": False, "source_saved": True,
            "result": {
                "failure_code": "schema_validation_failure", "message": private,
                "diagnostic": {
                    "stage": "chunk", "chunk_index": 3, "chunks_total": 5,
                    "schema_issue_code": "invalid_claim_item", "unknown_top_level_key_count": 2,
                    "claims_item_types": {"dict": 1, "null": 2, private: 99},
                    "top_level_shape": {"claims": "list", private: "str"},
                    "raw_response": private,
                },
            },
        }])
        diagnostic = notice[0]["result"]["diagnostic"]
        self.assertEqual(diagnostic.get("schema_issue_code"), "invalid_claim_item")
        self.assertEqual(diagnostic.get("claims_item_types"), {"dict": 1, "null": 2})
        self.assertEqual(diagnostic["top_level_shape"], {"claims": "list"})
        self.assertNotIn(private, repr(notice))
        self.assertEqual(literature_ui._safe_batch_notice(notice), notice)

    def test_malformed_diagnostic_types_are_ignored_without_crashing(self):
        safe = literature_ui._safe_batch_notice([{
            "filename": "paper.pdf", "success": False,
            "result": {"failure_code": "schema_validation_failure", "diagnostic": {
                "stage": {"unexpected": "shape"}, "finish_reason": ["stop"],
                "structured_mode": {"private": "value"}, "schema_issue_code": ["invalid_claim_item"],
            }},
        }])
        self.assertEqual(safe[0]["result"]["diagnostic"], {})

    def test_bilingual_technical_diagnostic_survives_streamlit_rerun(self):
        for locale, expected_reason in (
            ("en", "invalid claim item"),
            ("zh", "主张条目结构无效"),
        ):
            with self.subTest(locale=locale), tempfile.TemporaryDirectory() as temp:
                script = Path(temp) / "diagnostic_app.py"
                batch_notice = [{
                    "filename": "paper.pdf", "success": False, "source_saved": True,
                    "result": {
                        "failure_code": "schema_validation_failure", "failure_stage": "chunk",
                        "diagnostic": {
                            "stage": "chunk", "chunk_index": 2, "chunks_total": 4,
                            "source_locator": "2.1", "failure_code": "schema_validation_failure",
                            "schema_issue_code": "invalid_claim_item", "finish_reason": "stop",
                            "top_level_shape": {"claims": "list"},
                            "claims_item_types": {"dict": 1, "null": 1},
                        },
                    },
                }]
                script.write_text("\n".join((
                    "import sys",
                    f"sys.path.insert(0, {str(ROOT)!r})",
                    "import streamlit as st",
                    "from literature_ui_support import render_literature_ingestion",
                    f"st.session_state['literature_batch_notice'] = {batch_notice!r}",
                    f"render_literature_ingestion(st, [], {{}}, {str(temp)!r}, lambda _u:'', lambda *_a,**_k:'', lambda *_a:None, lambda *_a:None, {locale!r})",
                )), encoding="utf-8")
                app = AppTest.from_file(str(script), default_timeout=30).run()
                self.assertFalse(app.exception)
                rendered = "\n".join(str(item.value) for item in list(app.caption) + list(app.markdown) + list(app.warning))
                self.assertIn(expected_reason, rendered.lower())
                app.run()
                self.assertFalse(app.exception)
                rendered_again = "\n".join(str(item.value) for item in list(app.caption) + list(app.markdown) + list(app.warning))
                self.assertIn(expected_reason, rendered_again.lower())
                self.assertNotIn("PRIVATE_DOCUMENT_TEXT_SENTINEL", rendered_again)


if __name__ == "__main__":
    unittest.main()
