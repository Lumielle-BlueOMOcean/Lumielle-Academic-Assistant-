import unittest
import json
import re
from pathlib import Path

from structured_output_support import (
    create_completion_with_json_fallback,
    is_response_format_unsupported,
    parse_first_json_value,
    structured_request_options,
)
from writing_support import parse_literature_analysis
from literature_intelligence import analyze_literature_document, apply_analysis_to_literature_record
from literature_ui_support import failure_message
from research_support import parse_research_source


class StructuredJSONParserTests(unittest.TestCase):
    def test_parses_plain_object_and_array(self):
        self.assertEqual(parse_first_json_value('{"status":"ok"}'), {"status": "ok"})
        self.assertEqual(parse_first_json_value('[{"title":"Fact"}]'), [{"title": "Fact"}])

    def test_parses_fenced_json_with_surrounding_prose(self):
        response = 'Here is the result:\n```json\n{"facts":[{"content":"x"}]}\n```\nDone.'
        self.assertEqual(parse_first_json_value(response), {"facts": [{"content": "x"}]})

    def test_braces_inside_json_strings_do_not_end_the_value(self):
        response = '{"content":"literal { brace and } brace", "items":[{"x":1}]}'
        self.assertEqual(
            parse_first_json_value(response),
            {"content": "literal { brace and } brace", "items": [{"x": 1}]},
        )

    def test_malformed_json_is_not_repaired(self):
        self.assertIsNone(parse_first_json_value('{"facts":[{"content":}]}'))

    def test_multiple_values_choose_the_first_valid_object(self):
        self.assertEqual(
            parse_first_json_value('first {"status":"first"} then [1,2]'),
            {"status": "first"},
        )

    def test_top_level_primitives_are_rejected(self):
        for response in ('42', 'true', 'null', '"a string"'):
            with self.subTest(response=response):
                self.assertIsNone(parse_first_json_value(response))

    def test_legacy_literature_parser_uses_first_value_not_greedy_span(self):
        response = 'Analysis: {"rating":4,"category":"Methods"} Follow-up: {"extra":true}'
        result = parse_literature_analysis(response)
        self.assertEqual(result["rating"], 4)
        self.assertEqual(result["analysis_status"], "ok")

    def test_bilingual_legacy_research_helpers_use_shared_json_decoder(self):
        root = Path(__file__).resolve().parents[1]
        for app_name in ("app.py", "app_zh.py"):
            with self.subTest(app=app_name):
                source = (root / app_name).read_text(encoding="utf-8")
                body = source.split("def _extract_json_array(", 1)[1].split("\ndef ", 1)[0]
                self.assertIn("parse_first_json_value", body)
                self.assertIn('"facts"', body)
                self.assertNotIn("re.search", body)


class StructuredRequestPolicyTests(unittest.TestCase):
    def test_only_official_deepseek_host_receives_thinking_option(self):
        deepseek = structured_request_options("https://api.deepseek.com/v1/")
        self.assertEqual(deepseek["response_format"], {"type": "json_object"})
        self.assertEqual(deepseek["extra_body"], {"thinking": {"type": "disabled"}})

        compatible = structured_request_options("https://proxy.example/deepseek-compatible")
        self.assertEqual(compatible, {"response_format": {"type": "json_object"}})

    def test_fallback_requires_explicit_response_format_parameter_rejection(self):
        class ProviderError(Exception):
            def __init__(self, status_code, message):
                super().__init__(message)
                self.status_code = status_code

        self.assertTrue(is_response_format_unsupported(ProviderError(400, "response_format is unsupported")))
        self.assertTrue(is_response_format_unsupported(ProviderError(422, "Invalid json_object parameter")))
        for error in (
            ProviderError(400, "invalid model name"),
            ProviderError(401, "response_format unsupported"),
            ProviderError(429, "response_format unsupported"),
            ProviderError(500, "response_format unsupported"),
            TimeoutError("response_format request timed out"),
            ConnectionError("response_format network failure"),
        ):
            with self.subTest(error=str(error)):
                self.assertFalse(is_response_format_unsupported(error))

    def test_completion_falls_back_once_only_for_explicit_format_rejection(self):
        class ProviderError(Exception):
            status_code = 400

        calls = []
        response = object()

        def complete(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise ProviderError("response_format is not supported")
            return response

        result, mode = create_completion_with_json_fallback(
            complete,
            {"model": "model", "response_format": {"type": "json_object"}, "extra_body": {"x": 1}},
        )
        self.assertIs(result, response)
        self.assertEqual(mode, "compatibility")
        self.assertEqual(len(calls), 2)
        self.assertIn("response_format", calls[0])
        self.assertNotIn("response_format", calls[1])
        self.assertEqual(calls[1]["extra_body"], {"x": 1})

    def test_auth_rate_network_and_server_failures_do_not_fallback(self):
        class ProviderError(Exception):
            def __init__(self, status_code, message):
                super().__init__(message)
                self.status_code = status_code

        for error in (
            ProviderError(401, "response_format unsupported"),
            ProviderError(429, "response_format unsupported"),
            ProviderError(500, "response_format unsupported"),
            TimeoutError("network timeout"),
            ConnectionError("network failure"),
        ):
            calls = []

            def complete(**kwargs):
                calls.append(kwargs)
                raise error

            with self.subTest(error=str(error)), self.assertRaises(type(error)):
                create_completion_with_json_fallback(
                    complete, {"response_format": {"type": "json_object"}}
                )
            self.assertEqual(len(calls), 1)


class ResearchStructuredPipelineTests(unittest.TestCase):
    @staticmethod
    def _valid_fact():
        return {"title": "Research fact", "type": "text", "content": "FACT_SENTINEL", "tags": ["method"]}

    def test_accepts_canonical_legacy_and_single_module_shapes_in_both_locales(self):
        fact = self._valid_fact()
        shapes = (
            {"facts": [fact]},
            {"modules": [fact]},
            {"items": [fact]},
            [fact],
            fact,
        )
        for locale in ("en", "zh"):
            for value in shapes:
                with self.subTest(locale=locale, shape=type(value).__name__):
                    result = parse_research_source(
                        "Source text", "methods", lambda _prompt, **_kwargs: json.dumps(value), locale=locale
                    )
                    self.assertEqual(result["status"], "ok")
                    self.assertEqual(result["facts"][0]["content"], "FACT_SENTINEL")
                    self.assertEqual(result["facts"][0]["source_chunk"], 1)

    def test_table_module_keeps_columns_and_data(self):
        table = {"title": "Table", "type": "table", "columns": ["Year", "Value"], "data": [[2024, 12]]}
        result = parse_research_source("Table source", "data", lambda *_args, **_kwargs: json.dumps({"facts": [table]}))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["facts"][0]["columns"], ["Year", "Value"])
        self.assertEqual(result["facts"][0]["data"], [[2024, 12]])

    def test_empty_and_schema_garbage_are_rejected(self):
        for value in (
            {"facts": []},
            {"facts": [{"title": "Missing content", "type": "text"}]},
            {"unrelated": "not a module"},
            12,
        ):
            with self.subTest(value=value):
                result = parse_research_source("Source text", "methods", lambda *_args, **_kwargs: json.dumps(value))
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["facts"], [])

    def test_failure_stages_distinguish_invalid_empty_and_schema_output(self):
        cases = (
            ("not JSON", "invalid_structured_output"),
            (json.dumps({"facts": []}), "empty_structured_output"),
            (json.dumps({"facts": [{"title": "No content", "type": "text"}]}), "schema_validation_failure"),
        )
        for response, expected_stage in cases:
            with self.subTest(stage=expected_stage):
                result = parse_research_source("Source text", "methods", lambda *_args, **_kwargs: response)
                self.assertEqual(result["failure_stage"], expected_stage)
                self.assertEqual(result["facts"], [])

    def test_provider_failure_message_is_safe_and_stage_specific(self):
        secret = "sk-secret-value-123456789"
        result = parse_research_source(
            "Source text", "methods",
            lambda *_args, **_kwargs: f"API call failed repeatedly: authorization Bearer {secret}",
            locale="zh",
        )
        self.assertEqual(result["failure_stage"], "provider_call")
        self.assertIn("AI 服务调用失败", result["message"])
        self.assertNotIn(secret, result["message"])

    def test_both_locales_send_structured_mode_and_canonical_object_prompt(self):
        for locale in ("en", "zh"):
            calls = []

            def llm(prompt, **kwargs):
                calls.append((prompt, kwargs))
                return json.dumps({"facts": [self._valid_fact()]})

            result = parse_research_source("Source text", "methods", llm, locale=locale)
            self.assertEqual(result["status"], "ok")
            self.assertTrue(calls[0][1].get("json_mode"))
            self.assertIn("facts", calls[0][0].lower())

    def test_invalid_first_chunk_retries_once_then_returns_complete_result(self):
        calls = []

        def llm(prompt, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return "not JSON"
            return json.dumps({"facts": [self._valid_fact()]})

        result = parse_research_source("Source text", "methods", llm)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call.get("json_mode") for call in calls))

    def test_later_chunk_failure_returns_no_partial_facts(self):
        calls = []

        def llm(prompt, **_kwargs):
            part = int(re.search(r"DOCUMENT PART (\d+)/(\d+)", prompt).group(1))
            calls.append(part)
            if part == 1:
                return json.dumps({"facts": [self._valid_fact()]})
            return "not JSON"

        result = parse_research_source("A" * 55, "methods", llm, max_chars=20)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["facts"], [])
        self.assertEqual(calls, [1, 2, 2])


class LiteratureStructuredPipelineTests(unittest.TestCase):
    @staticmethod
    def _chunk_extraction(evidence_text="EXACT_SOURCE_QUOTE"):
        return {
            "research_question": "RQ", "theory": "Theory", "method": "Survey",
            "sample": "N=10", "data": "Survey data", "results": "Result",
            "claims": [{"section": "Results", "claim": "Claim", "evidence_text": evidence_text}],
            "limitations": "Limit", "conclusion": "Conclusion", "definitions": [],
        }

    @staticmethod
    def _profile():
        return {
            "rating": 4, "category": "Methods", "research_question": "RQ",
            "methods": "Survey", "sample": "N=10", "key_findings": "Finding",
            "limitations": "Limit", "quality_assessment": "Adequate",
            "relevance_reason": "Relevant", "summary": "Summary",
        }

    def test_chunk_and_synthesis_both_request_json_mode_in_both_locales(self):
        for locale in ("en", "zh"):
            calls = []

            def llm(prompt, **kwargs):
                calls.append((prompt, kwargs))
                value = self._profile() if "[DOCUMENT SYNTHESIS]" in prompt else self._chunk_extraction()
                return json.dumps(value)

            result = analyze_literature_document(
                "EXACT_SOURCE_QUOTE", "study", llm, locale=locale, max_chars=200,
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(kwargs.get("json_mode") for _, kwargs in calls))

    def test_invalid_chunk_output_retries_once_then_synthesizes(self):
        calls = []

        def llm(prompt, **kwargs):
            calls.append((prompt, kwargs))
            if len(calls) == 1:
                return "not JSON"
            value = self._profile() if "[DOCUMENT SYNTHESIS]" in prompt else self._chunk_extraction()
            return "```json\n" + json.dumps(value) + "\n```"

        result = analyze_literature_document("EXACT_SOURCE_QUOTE", "study", llm, max_chars=200)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(kwargs.get("json_mode") for _, kwargs in calls))
        self.assertIn("previous output was invalid", calls[1][0].lower())

    def test_two_invalid_chunk_attempts_fail_closed_without_partial_evidence(self):
        calls = []
        result = analyze_literature_document(
            "EXACT_SOURCE_QUOTE", "study",
            lambda prompt, **kwargs: (calls.append(kwargs) or "not JSON"),
            max_chars=200,
        )
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["profile"])
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["failure_stage"], "invalid_structured_output")
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(kwargs.get("json_mode") for kwargs in calls))

    def test_empty_and_schema_invalid_chunk_results_have_distinct_failure_codes(self):
        for response, expected in (
            ("", "empty_structured_output"),
            ("{}", "empty_structured_output"),
            (json.dumps({"research_question": "RQ"}), "schema_validation_failure"),
        ):
            with self.subTest(expected=expected):
                result = analyze_literature_document(
                    "EXACT_SOURCE_QUOTE", "study", lambda *_args, **_kwargs: response, max_chars=200,
                )
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["failure_stage"], expected)
                self.assertEqual(result["evidence"], [])

    def test_provider_exception_details_are_not_returned(self):
        secret = "sk-secret-value-123456789"
        calls = []
        result = analyze_literature_document(
            "EXACT_SOURCE_QUOTE", "study",
            lambda *_args, **_kwargs: (
                calls.append(1)
                or (_ for _ in ()).throw(RuntimeError(f"provider failed {secret}"))
            ),
            max_chars=200,
        )
        self.assertEqual(result["failure_stage"], "provider_call")
        self.assertNotIn(secret, repr(result))
        self.assertEqual(len(calls), 1)

    def test_invalid_synthesis_retry_preserves_existing_profile_and_evidence(self):
        calls = []

        def llm(prompt, **kwargs):
            calls.append(prompt)
            if "[DOCUMENT SYNTHESIS]" in prompt:
                if sum("[DOCUMENT SYNTHESIS]" in item for item in calls) <= 1:
                    return json.dumps({"rating": 0, "category": ""})
                return json.dumps(self._profile())
            return json.dumps(self._chunk_extraction())

        result = analyze_literature_document("EXACT_SOURCE_QUOTE", "study", llm, max_chars=200)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(calls), 3)

        old_record = {"id": "lit-1", "rating": 5, "analysis": {"summary": "KEEP_OLD_PROFILE"}}
        old_evidence = {"lit-1": [{"claim": "KEEP_OLD_EVIDENCE"}]}
        failed = analyze_literature_document(
            "EXACT_SOURCE_QUOTE", "study",
            lambda prompt, **_kwargs: self._chunk_extraction() and (
                json.dumps(self._chunk_extraction()) if "[DOCUMENT SYNTHESIS]" not in prompt else json.dumps({"rating": 0, "category": ""})
            ),
            max_chars=200,
        )
        updated_record, updated_evidence, success = apply_analysis_to_literature_record(old_record, old_evidence, failed)
        self.assertFalse(success)
        self.assertEqual(updated_record, old_record)
        self.assertEqual(updated_evidence, old_evidence)
        self.assertEqual(failed["failure_stage"], "document_synthesis")

    def test_only_verbatim_chunk_evidence_is_retained(self):
        def llm(prompt, **_kwargs):
            if "[DOCUMENT SYNTHESIS]" in prompt:
                return json.dumps(self._profile())
            extraction = self._chunk_extraction()
            extraction["claims"].append({
                "section": "Results", "claim": "Unsupported", "evidence_text": "NOT_IN_SOURCE",
            })
            return json.dumps(extraction)

        result = analyze_literature_document("EXACT_SOURCE_QUOTE", "study", llm, max_chars=200)
        self.assertEqual([item["evidence_text"] for item in result["evidence"]], ["EXACT_SOURCE_QUOTE"])

    def test_failure_messages_distinguish_provider_output_and_synthesis_stages(self):
        cases = (
            ({"failure_stage": "provider_call"}, "AI 服务调用失败"),
            ({"failure_stage": "invalid_structured_output"}, "格式异常"),
            ({"failure_stage": "empty_structured_output"}, "空"),
            ({"failure_stage": "schema_validation_failure"}, "不符合"),
            ({"failure_stage": "document_synthesis"}, "合并"),
        )
        for result, expected in cases:
            with self.subTest(stage=result["failure_stage"]):
                message = failure_message(result, "zh")
                self.assertIn(expected, message)
                self.assertNotIn("traceback", message.casefold())
                self.assertNotIn("Authorization", message)


if __name__ == "__main__":
    unittest.main()
