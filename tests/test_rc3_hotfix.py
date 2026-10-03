import ast
import json
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import call, patch

from streamlit.testing.v1 import AppTest

import literature_intelligence as literature
import research_support
import structured_output_support as structured
from literature_intelligence import analyze_literature_document
from research_support import (
    accept_smart_inbox_facts,
    create_default_research_base,
    make_manual_research_fact,
    migrate_research_base,
    parse_research_source,
    select_research_grounding_for_chapter,
)
from research_ui_support import _TEXT


ROOT = Path(__file__).resolve().parents[1]


def response(content, *, finish_reason="stop", mode="native", status=200, error=None):
    return {
        "content": content,
        "finish_reason": finish_reason,
        "structured_mode": mode,
        "http_status": status,
        "error_code": error,
    }


def valid_chunk(quote="EXACT SOURCE QUOTE"):
    return json.dumps({
        "results": "A supported result",
        "claims": [{"statement": "The result improved", "quote": quote}],
    })


def valid_profile():
    return json.dumps({"rating": "4", "type": "Other", "findings": ["Combined finding"]})


class ResearchGroundingRc3Tests(unittest.TestCase):
    def test_explicit_fact_survives_disabled_section_migration_and_is_selectable(self):
        base = create_default_research_base()
        base["sections"]["methods"].update({
            "allow_writing_grounding": False,
            "modules": [
                {"id": "on", "title": "Sampling method", "content": "sampling", "allow_writing_grounding": True},
                {"id": "off", "title": "Sampling exclusion", "content": "sampling", "allow_writing_grounding": False},
            ],
        })

        loaded = migrate_research_base(json.loads(json.dumps(base)))
        loaded = migrate_research_base(json.loads(json.dumps(loaded)))
        facts = loaded["sections"]["methods"]["modules"]

        self.assertEqual([item["allow_writing_grounding"] for item in facts], [True, False])
        selected = select_research_grounding_for_chapter(loaded, "sampling method")
        self.assertEqual([item["id"] for item in selected], ["on"])

    def test_new_fact_inherits_section_default_but_explicit_smart_inbox_choice_wins(self):
        for enabled in (False, True):
            with self.subTest(section_default=enabled):
                parsed = parse_research_source(
                    "Survey evidence", "methods",
                    lambda *_args, **_kwargs: json.dumps({"facts": [{"title": "Survey", "content": "Survey evidence"}]}),
                    allow_writing_grounding=enabled,
                )
                self.assertEqual(parsed["facts"][0]["allow_writing_grounding"], enabled)
                manual = make_manual_research_fact("methods", "Manual", "A fact", enabled)
                self.assertEqual(manual["allow_writing_grounding"], enabled)

        accepted = accept_smart_inbox_facts(
            {"facts": [{"title": "Reviewed", "content": "Evidence"}]},
            "methods", [], "", True, section_enabled=False,
        )
        self.assertTrue(accepted[0]["allow_writing_grounding"])

    def test_bilingual_bulk_controls_leave_facts_enabled_for_independent_selection(self):
        cases = (
            ("en", "Enable all", "Disable all", "Writing grounding: 1 / 3 facts enabled"),
            ("zh", "全部启用", "全部取消", "正文 Grounding：1 / 3 条事实已启用"),
        )
        for locale, enable_label, disable_label, count_label in cases:
            with self.subTest(locale=locale), tempfile.TemporaryDirectory() as temp:
                app_path = Path(temp) / "grounding_app.py"
                app_path.write_text("\n".join((
                    "import sys",
                    f"sys.path.insert(0, {str(ROOT)!r})",
                    "import streamlit as st",
                    "from research_ui_support import _render_section",
                    "if '_working' not in st.session_state:",
                    "    st.session_state['_working'] = {'id':'methods','name_en':'Methods','name_zh':'方法','allow_writing_grounding':False,'modules':[",
                    "        {'id':'a','title':'A','type':'fact','content':'sampling A','tags':['sampling'],'allow_writing_grounding':False},",
                    "        {'id':'b','title':'B','type':'fact','content':'sampling B','tags':['sampling'],'allow_writing_grounding':False},",
                    "        {'id':'c','title':'C','type':'fact','content':'sampling C','tags':['sampling'],'allow_writing_grounding':False},",
                    "    ]}",
                    "section = st.session_state['_working']",
                    "base = {'schema_version':2,'sections':{'methods':section}}",
                    "def save_base(value): st.session_state['_saved'] = value['sections']['methods']",
                    f"_render_section(st,base,'methods',lambda *_a,**_k:'',save_base,lambda *_a:'',{locale!r},[])",
                    "st.session_state['_working'] = section",
                )), encoding="utf-8")
                app = AppTest.from_file(str(app_path), default_timeout=30).run()
                self.assertFalse(app.exception)
                children = [item for item in app.checkbox if item.key.startswith("research_fact_allow_methods_")]
                self.assertEqual(len(children), 3)
                self.assertTrue(all(not item.disabled and not item.value for item in children))

                children[1].set_value(True)
                app.run()
                self.assertEqual([item.value for item in app.checkbox if item.key.startswith("research_fact_allow_methods_")], [False, True, False])
                self.assertTrue(any(count_label in item.value for item in app.caption))
                next(item for item in app.button if item.label == enable_label).click()
                app.run()
                self.assertEqual([item["allow_writing_grounding"] for item in app.session_state["_saved"]["modules"]], [True, True, True])
                self.assertTrue(app.session_state["_saved"]["allow_writing_grounding"])
                self.assertTrue(all(item.value for item in app.checkbox if item.key.startswith("research_fact_allow_methods_")))

                next(item for item in app.button if item.label == disable_label).click()
                app.run()
                self.assertFalse(app.session_state["_saved"]["allow_writing_grounding"])
                self.assertEqual([item["allow_writing_grounding"] for item in app.session_state["_saved"]["modules"]], [False, False, False])
                self.assertTrue(all(not item.disabled and not item.value for item in app.checkbox if item.key.startswith("research_fact_allow_methods_")))

                next(item for item in app.button if item.label == enable_label).click()
                app.run()
                self.assertTrue(all(item["allow_writing_grounding"] for item in app.session_state["_saved"]["modules"]))


class StructuredMetadataRc3Tests(unittest.TestCase):
    def test_success_metadata_contains_only_safe_response_fields(self):
        build = getattr(structured, "structured_response_result", None)
        self.assertTrue(callable(build))
        result = build(types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                finish_reason="length",
                message=types.SimpleNamespace(content='{"partial":'),
            )],
            status_code=200,
        ), "native")
        self.assertEqual(result, {
            "content": '{"partial":',
            "finish_reason": "length",
            "structured_mode": "native",
            "http_status": 200,
            "error_code": None,
        })

    def test_provider_exception_classifier_distinguishes_safe_categories(self):
        classify = getattr(structured, "classify_provider_exception", None)
        self.assertTrue(callable(classify))

        class StatusError(Exception):
            def __init__(self, status):
                super().__init__("Authorization: Bearer sk-secret-value")
                self.status_code = status

        cases = {
            401: "authentication_failed", 402: "insufficient_balance", 404: "model_not_available",
            429: "rate_limited", 400: "invalid_request", 422: "invalid_request",
            500: "server_error", 503: "server_error",
        }
        for status, category in cases.items():
            with self.subTest(status=status):
                self.assertEqual(classify(StatusError(status)), category)
        self.assertEqual(classify(TimeoutError("timeout")), "timeout")
        self.assertEqual(classify(ConnectionError("connection")), "connection_error")
        secret_error = StatusError(429)
        envelope = structured.structured_error_result(secret_error)
        self.assertEqual(envelope["http_status"], 429)
        self.assertEqual(envelope["error_code"], "rate_limited")
        self.assertNotIn("sk-secret-value", repr(envelope))

    def test_both_apps_keep_string_compatibility_and_offer_metadata_for_structured_calls(self):
        for app_name in ("app.py", "app_zh.py"):
            with self.subTest(app=app_name):
                tree = ast.parse((ROOT / app_name).read_text(encoding="utf-8"))
                node = next(
                    item for item in tree.body
                    if isinstance(item, ast.FunctionDef) and item.name == "call_llm_api"
                )
                module = ast.Module(body=[node], type_ignores=[])
                code = compile(module, app_name, "exec")
                namespace = {
                    "OpenAI": None,
                    "create_completion_with_json_fallback": structured.create_completion_with_json_fallback,
                    "structured_request_options": structured.structured_request_options,
                    "structured_response_result": structured.structured_response_result,
                    "structured_error_result": structured.structured_error_result,
                }
                calls = []

                def create(**kwargs):
                    calls.append(kwargs)
                    return types.SimpleNamespace(
                        choices=[types.SimpleNamespace(
                            finish_reason="stop",
                            message=types.SimpleNamespace(content='{"method":"Survey"}'),
                        )],
                        status_code=200,
                    )

                class FakeOpenAI:
                    def __init__(self, **_kwargs):
                        self.chat = types.SimpleNamespace(
                            completions=types.SimpleNamespace(create=create),
                        )

                namespace["OpenAI"] = FakeOpenAI
                exec(code, namespace)
                call = namespace["call_llm_api"]
                metadata = call(
                    "PRIVATE PROMPT", "system", 2200, "sk-private-key", "https://api.deepseek.com",
                    "deepseek-chat", json_mode=True, return_metadata=True,
                )
                self.assertEqual(metadata["finish_reason"], "stop")
                self.assertEqual(metadata["structured_mode"], "native")
                self.assertEqual(metadata["http_status"], 200)
                self.assertEqual(calls[0]["extra_body"], {"thinking": {"type": "disabled"}})
                self.assertNotIn("PRIVATE PROMPT", repr(metadata))
                self.assertNotIn("sk-private-key", repr(metadata))

                legacy = call(
                    "PRIVATE PROMPT", "system", 2200, "sk-private-key", "https://api.deepseek.com",
                    "deepseek-chat", json_mode=True,
                )
                self.assertEqual(legacy, '{"method":"Survey"}')

    def test_both_apps_report_compatibility_json_mode_without_raw_fallback_error(self):
        class UnsupportedResponseFormat(Exception):
            status_code = 422

        for app_name in ("app.py", "app_zh.py"):
            with self.subTest(app=app_name):
                node = next(
                    item for item in ast.parse((ROOT / app_name).read_text(encoding="utf-8")).body
                    if isinstance(item, ast.FunctionDef) and item.name == "call_llm_api"
                )
                namespace = {
                    "OpenAI": None,
                    "create_completion_with_json_fallback": structured.create_completion_with_json_fallback,
                    "structured_request_options": structured.structured_request_options,
                    "structured_response_result": structured.structured_response_result,
                    "structured_error_result": structured.structured_error_result,
                }
                attempts = []

                def create(**kwargs):
                    attempts.append(kwargs)
                    if "response_format" in kwargs:
                        raise UnsupportedResponseFormat("response_format is not supported; private sk-secret-value")
                    return types.SimpleNamespace(
                        choices=[types.SimpleNamespace(
                            finish_reason="stop",
                            message=types.SimpleNamespace(content='{"method":"Survey"}'),
                        )],
                        status_code=200,
                    )

                class FakeOpenAI:
                    def __init__(self, **_kwargs):
                        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))

                namespace["OpenAI"] = FakeOpenAI
                exec(compile(ast.Module(body=[node], type_ignores=[]), app_name, "exec"), namespace)
                result = namespace["call_llm_api"](
                    "PRIVATE PROMPT", "system", 2200, "sk-private-key", "https://api.example.com/v1",
                    "model", json_mode=True, return_metadata=True,
                )
                self.assertEqual(result["structured_mode"], "compatibility")
                self.assertIsNone(result["error_code"])
                self.assertEqual(len(attempts), 2)
                self.assertNotIn("sk-secret-value", repr(result))

    def test_unknown_finish_reason_is_safe_and_fails_as_incomplete_response(self):
        for finish_reason, expected_calls, expected_code in (
            ("aborted", 2, None),
            ("unexpected-provider-state", 1, "incomplete_response"),
            (None, 1, "incomplete_response"),
        ):
            calls = []

            def llm(prompt, **_kwargs):
                calls.append(prompt)
                if "[DOCUMENT SYNTHESIS]" in prompt:
                    return response(valid_profile())
                if finish_reason == "aborted" and sum("[SOURCE CHUNK" in item for item in calls) == 1:
                    return response("", finish_reason=finish_reason)
                if finish_reason != "aborted":
                    return response(valid_chunk(), finish_reason=finish_reason)
                return response(valid_chunk())

            with self.subTest(finish_reason=finish_reason), patch.object(literature.time, "sleep") as sleep:
                result = analyze_literature_document("EXACT SOURCE QUOTE", "Study", llm, max_chars=500)
            self.assertEqual(len(calls), expected_calls if expected_code else 3)
            if expected_code:
                self.assertEqual(result["failure_code"], expected_code)
                self.assertEqual(result["diagnostic"]["finish_reason"], "unknown" if finish_reason else None)
            else:
                self.assertEqual(result["status"], "ok")
                sleep.assert_called_once()

    def test_both_apps_offer_opt_in_metadata_without_changing_default_string_api(self):
        for name in ("app.py", "app_zh.py"):
            with self.subTest(app=name):
                tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
                call = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "call_llm_api")
                dispatch = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "dispatch_llm_call")
                call_args = {arg.arg for arg in call.args.args + call.args.kwonlyargs}
                dispatch_args = {arg.arg for arg in dispatch.args.args + dispatch.args.kwonlyargs}
                self.assertIn("return_metadata", call_args)
                self.assertIn("return_metadata", dispatch_args)
                self.assertTrue(any(isinstance(node, ast.keyword) and node.arg == "return_metadata" for node in ast.walk(dispatch)))


class LiteratureRobustnessRc3Tests(unittest.TestCase):
    def test_realistic_aliases_and_nested_text_values_normalize_deterministically(self):
        normalize = literature.normalize_chunk_extraction
        source = "EXACT SOURCE QUOTE"
        payload = {
            "methods": {"instrument": "questionnaire", "design": "survey"},
            "participants": {"population": "students", "n": 426},
            "findings": ["Finding A", "Finding B"],
            "claims": ["Claim A", {"statement": "Claim B", "quote": source}],
        }
        normalized = normalize(payload)
        self.assertEqual(normalized["method"], "design: survey; instrument: questionnaire")
        self.assertEqual(normalized["sample"], "n: 426; population: students")
        self.assertEqual(normalized["results"], "Finding A; Finding B")
        self.assertEqual(normalized["claims"], [
            {"section": "", "claim": "Claim A", "evidence_text": ""},
            {"section": "", "claim": "Claim B", "evidence_text": source},
        ])
        self.assertEqual(normalize(payload), normalized)

    def test_profile_aliases_and_flattening_keep_rating_strict(self):
        normalize = literature.normalize_profile
        profile = normalize({
            "rating": "4", "type": "Other", "methodology": {"instrument": "survey", "design": "cross-sectional"},
            "findings": ["Finding A", "Finding B"],
        })
        self.assertEqual(profile["category"], "Other")
        self.assertEqual(profile["methods"], "design: cross-sectional; instrument: survey")
        self.assertEqual(profile["key_findings"], "Finding A; Finding B")
        for rating in (True, 0, 6, "4/5", "four", 4.5):
            with self.subTest(rating=rating):
                self.assertIsNone(normalize({"rating": rating, "summary": "Has content"}))
        self.assertIsNone(normalize({"rating": 4, "category": {"name": "Other"}, "summary": "Has content"}))

    def test_prompts_include_complete_canonical_json_examples_in_both_locales(self):
        for locale in ("en", "zh"):
            with self.subTest(locale=locale):
                chunk = literature.build_chunk_extraction_prompt("Study", "SOURCE", 1, 1, locale)
                synthesis = literature.build_synthesis_prompt("Study", [{"results": "A"}], locale)
                for field in ('"research_question":""', '"evidence_text":""', '"claims":['):
                    self.assertIn(field, chunk)
                for field in ('"rating":4', '"category":"Other"', '"summary":""'):
                    self.assertIn(field, synthesis)

    def test_length_response_retries_once_with_larger_chunk_budget(self):
        calls = []

        def llm(prompt, **kwargs):
            calls.append((prompt, kwargs))
            if "[DOCUMENT SYNTHESIS]" in prompt:
                return response(valid_profile())
            if sum("[SOURCE CHUNK" in p for p, _ in calls) == 1:
                return response('{"partial":', finish_reason="length")
            return response(valid_chunk())

        with patch.object(literature.time, "sleep"):
            result = analyze_literature_document("EXACT SOURCE QUOTE", "Study", llm, max_chars=500)
        chunk_budgets = [kwargs["max_tokens"] for prompt, kwargs in calls if "[SOURCE CHUNK" in prompt]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(chunk_budgets), 2)
        self.assertGreater(chunk_budgets[1], chunk_budgets[0])
        self.assertGreater(chunk_budgets[0], 1400)

    def test_synthesis_length_retry_uses_its_own_larger_budget(self):
        calls = []

        def llm(prompt, **kwargs):
            calls.append((prompt, kwargs))
            if "[DOCUMENT SYNTHESIS]" in prompt:
                synthesis_attempts = sum("[DOCUMENT SYNTHESIS]" in prior for prior, _ in calls)
                if synthesis_attempts == 1:
                    return response('{"partial":', finish_reason="length")
                return response(valid_profile())
            return response(valid_chunk())

        result = analyze_literature_document("EXACT SOURCE QUOTE", "Study", llm, max_chars=500)
        budgets = [kwargs["max_tokens"] for prompt, kwargs in calls if "[DOCUMENT SYNTHESIS]" in prompt]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(budgets, [2600, 4000])

    def test_repeated_length_response_fails_as_truncated_output_with_safe_diagnostic(self):
        calls = []

        def llm(prompt, **kwargs):
            calls.append(kwargs)
            return response('{"partial":', finish_reason="length")

        with patch.object(literature.time, "sleep"):
            result = analyze_literature_document("PRIVATE SOURCE TEXT", "Study", llm, max_chars=500)
        self.assertEqual(result["failure_code"], "truncated_output")
        self.assertEqual(result["diagnostic"]["finish_reason"], "length")
        self.assertEqual(result["diagnostic"]["chunk_index"], 1)
        self.assertNotIn("PRIVATE SOURCE TEXT", repr(result))
        self.assertEqual(len(calls), 2)

    def test_all_transient_attempts_are_bounded_and_diagnostic_reports_retries(self):
        calls = []

        def unavailable(prompt, **_kwargs):
            calls.append(prompt)
            return response("", status=503, error="server_error")

        with patch.object(literature.time, "sleep") as sleep:
            result = analyze_literature_document("source", "Study", unavailable, max_chars=500)
        self.assertEqual(result["failure_code"], "server_error")
        self.assertEqual(result["diagnostic"]["retry_count"], 2)
        self.assertEqual(len(calls), 3)
        sleep.assert_has_calls([call(1), call(2)])

    def test_content_filter_is_not_retried_and_interruption_is_retried(self):
        calls = []

        def filtered(prompt, **_kwargs):
            calls.append(prompt)
            return response("blocked", finish_reason="content_filter")

        with patch.object(literature.time, "sleep"):
            denied = analyze_literature_document("source", "Study", filtered, max_chars=500)
        self.assertEqual(denied["failure_code"], "content_filtered")
        self.assertEqual(len(calls), 1)

        calls.clear()

        def interrupted(prompt, **_kwargs):
            calls.append(prompt)
            if "[DOCUMENT SYNTHESIS]" in prompt:
                return response(valid_profile())
            if sum("[SOURCE CHUNK" in value for value in calls) == 1:
                return response("", finish_reason="insufficient_system_resource")
            return response(valid_chunk())

        with patch.object(literature.time, "sleep") as sleep:
            recovered = analyze_literature_document("EXACT SOURCE QUOTE", "Study", interrupted, max_chars=500)
        self.assertEqual(recovered["status"], "ok")
        self.assertEqual(sum("[SOURCE CHUNK" in value for value in calls), 2)
        sleep.assert_called_once()

    def test_transient_provider_failures_retry_bounded_but_auth_does_not(self):
        for status, code in ((429, "rate_limited"), (503, "server_error")):
            with self.subTest(status=status):
                calls = []

                def llm(prompt, **_kwargs):
                    calls.append(prompt)
                    if "[DOCUMENT SYNTHESIS]" in prompt:
                        return response(valid_profile())
                    if len(calls) == 1:
                        return response("", status=status, error=code)
                    return response(valid_chunk())

                with patch.object(literature.time, "sleep") as sleep:
                    result = analyze_literature_document("EXACT SOURCE QUOTE", "Study", llm, max_chars=500)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(len(calls), 3)
                sleep.assert_called_once()

        calls = []

        def unauthorized(prompt, **_kwargs):
            calls.append(prompt)
            return response("", status=401, error="authentication_failed")

        with patch.object(literature.time, "sleep") as sleep:
            failed = analyze_literature_document("source", "Study", unauthorized, max_chars=500)
        self.assertEqual(failed["failure_code"], "authentication_failed")
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()

    def test_transient_status_and_transport_exceptions_are_bounded_and_nontransient_statuses_stop(self):
        class StatusError(Exception):
            def __init__(self, status):
                super().__init__(f"provider failure with secret sk-secret-value")
                self.status_code = status

        for status in (500, 502, 504):
            with self.subTest(status=status):
                calls = []

                def server(prompt, **_kwargs):
                    calls.append(prompt)
                    if len(calls) == 1:
                        raise StatusError(status)
                    if "[DOCUMENT SYNTHESIS]" in prompt:
                        return response(valid_profile())
                    return response(valid_chunk())

                with patch.object(literature.time, "sleep") as sleep:
                    result = analyze_literature_document("EXACT SOURCE QUOTE", "Study", server, max_chars=500)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(len(calls), 3)
                sleep.assert_called_once()
                self.assertNotIn("sk-secret-value", repr(result))

        for error in (TimeoutError("private timeout"), ConnectionError("private connection")):
            with self.subTest(error=type(error).__name__):
                calls = []

                def interrupted(prompt, **_kwargs):
                    calls.append(prompt)
                    if len(calls) == 1:
                        raise error
                    if "[DOCUMENT SYNTHESIS]" in prompt:
                        return response(valid_profile())
                    return response(valid_chunk())

                with patch.object(literature.time, "sleep") as sleep:
                    result = analyze_literature_document("EXACT SOURCE QUOTE", "Study", interrupted, max_chars=500)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(len(calls), 3)
                sleep.assert_called_once()

        for status, code in ((400, "invalid_request"), (402, "insufficient_balance"), (404, "model_not_available"), (422, "invalid_request")):
            with self.subTest(status=status):
                calls = []

                def rejected(prompt, **_kwargs):
                    calls.append(prompt)
                    return response("", status=status, error=code)

                with patch.object(literature.time, "sleep") as sleep:
                    result = analyze_literature_document("source", "Study", rejected, max_chars=500)
                self.assertEqual(result["failure_code"], code)
                self.assertEqual(len(calls), 1)
                sleep.assert_not_called()

    def test_malformed_and_empty_outputs_get_one_business_correction(self):
        for first, second, expected in (
            (response(""), response(valid_chunk()), "ok"),
            (response("not JSON"), response(valid_chunk()), "ok"),
            (response(""), response(""), "empty_structured_output"),
            (response("not JSON"), response("still invalid"), "invalid_structured_output"),
        ):
            calls = []

            def llm(prompt, **_kwargs):
                calls.append(prompt)
                if "[DOCUMENT SYNTHESIS]" in prompt:
                    return response(valid_profile())
                return first if sum("[SOURCE CHUNK" in p for p in calls) == 1 else second

            with self.subTest(expected=expected), patch.object(literature.time, "sleep"):
                result = analyze_literature_document("EXACT SOURCE QUOTE", "Study", llm, max_chars=500)
            if expected == "ok":
                self.assertEqual(result["status"], "ok")
            else:
                self.assertEqual(result["failure_code"], expected)
            self.assertEqual(sum("[SOURCE CHUNK" in p for p in calls), 2)

    def test_exact_evidence_only_survives_alias_claim_normalization(self):
        def llm(prompt, **_kwargs):
            if "[DOCUMENT SYNTHESIS]" in prompt:
                return response(valid_profile())
            return response(json.dumps({"findings": "A finding", "claims": [
                "A claim without provenance",
                {"assertion": "Supported claim", "quotation": "EXACT SOURCE QUOTE"},
                {"text": "Fabricated quote claim", "source_quote": "PARAPHRASED SOURCE"},
            ]}))

        result = analyze_literature_document("EXACT SOURCE QUOTE is in the paper.", "Study", llm, max_chars=500)
        self.assertEqual(result["status"], "ok")
        self.assertEqual([item["claim"] for item in result["evidence"]], ["Supported claim"])
        self.assertEqual(result["evidence"][0]["evidence_text"], "EXACT SOURCE QUOTE")

    def test_batch_outcomes_name_files_and_summarize_success_source_only_and_reasons(self):
        ui_support = __import__("literature_ui_support")
        format_item = getattr(ui_support, "format_batch_outcome", None)
        summarize = getattr(ui_support, "summarize_literature_batch", None)
        self.assertTrue(callable(format_item))
        self.assertTrue(callable(summarize))
        items = [
            {"filename": "one.pdf", "success": True, "source_saved": True, "result": {}},
            {"filename": "two.pdf", "success": False, "source_saved": True, "result": {"failure_code": "schema_validation_failure", "diagnostic": {"chunk_index": 2, "chunks_total": 4}}},
            {"filename": "three.pdf", "success": False, "source_saved": True, "result": {"failure_code": "invalid_structured_output"}},
            {"filename": "four.pdf", "success": False, "source_saved": True, "result": {"failure_code": "truncated_output"}},
            {"filename": "five.pdf", "success": False, "source_saved": True, "result": {"failure_code": "server_error", "diagnostic": {"http_status": 503, "retry_count": 2}}},
            {"filename": "six.pdf", "success": True, "source_saved": True, "result": {}},
        ]
        rendered = [format_item(item, "zh") for item in items]
        self.assertIn("two.pdf", rendered[1])
        self.assertIn("第 2/4 分块", rendered[1])
        self.assertIn("HTTP 503", rendered[4])
        self.assertNotIn("PRIVATE", "".join(rendered))
        summary = summarize(items, "zh")
        self.assertIn("共 6 篇", summary)
        self.assertIn("成功分析 2", summary)
        self.assertIn("仅保存原文 4", summary)
        self.assertIn("结构化数据不兼容 1", summary)
        self.assertIn("输出达到长度限制 1", summary)
        safe_notice = ui_support._safe_batch_notice([
            {
                "filename": "private.pdf", "success": False, "source_saved": True,
                "result": {
                    "failure_code": "invalid_structured_output",
                    "message": "PRIVATE SOURCE AND sk-secret-value",
                    "diagnostic": {
                        "stage": "chunk", "chunk_index": 2, "chunks_total": 4,
                        "response_chars": 1300, "top_level_shape": {"method": "dict"},
                        "source_text": "PRIVATE SOURCE AND sk-secret-value",
                    },
                },
            },
        ])
        self.assertNotIn("PRIVATE SOURCE", repr(safe_notice))
        self.assertNotIn("sk-secret-value", repr(safe_notice))

    def test_batch_statuses_and_summary_render_after_streamlit_rerun_in_both_locales(self):
        case_data = (
            ("en", "good.pdf — analyzed successfully", "bad.pdf — original saved only", "Batch: 2 files; analyzed 1; original only 1."),
            ("zh", "good.pdf — 分析成功", "bad.pdf — 仅保存原文：结构化数据不兼容（第 2/4 分块）", "本批次共 2 篇：成功分析 1，仅保存原文 1。"),
        )
        for locale, success_text, failure_text, summary_text in case_data:
            with self.subTest(locale=locale), tempfile.TemporaryDirectory() as temp:
                app_path = Path(temp) / "literature_batch_app.py"
                app_path.write_text("\n".join((
                    "import sys",
                    f"sys.path.insert(0, {str(ROOT)!r})",
                    "import streamlit as st",
                    "from literature_ui_support import render_literature_ingestion",
                    "st.session_state['literature_batch_notice'] = [",
                    " {'filename':'good.pdf','success':True,'source_saved':True,'result':{'failure_code':'provider_call','failure_stage':'provider_call','diagnostic':{}}},",
                    " {'filename':'bad.pdf','success':False,'source_saved':True,'result':{'failure_code':'schema_validation_failure','failure_stage':'chunk','diagnostic':{'stage':'chunk','chunk_index':2,'chunks_total':4}}},",
                    "]",
                    f"render_literature_ingestion(st, [], {{}}, {str(temp)!r}, lambda _upload:'', lambda *_a,**_k:'', lambda *_a:None, lambda *_a:None, {locale!r})",
                )), encoding="utf-8")
                app = AppTest.from_file(str(app_path), default_timeout=30).run()
                self.assertFalse(app.exception)
                self.assertTrue(any(success_text in item.value for item in app.success))
                self.assertTrue(any(failure_text in item.value for item in app.warning))
                self.assertTrue(any(summary_text in item.value for item in app.info))


if __name__ == "__main__":
    unittest.main()
