"""Focused regressions for v1.2.0 RC2 chapter prompt budgeting and diagnostics."""

import ast
import hashlib
import types
import unittest
from pathlib import Path

import chapter_memory_support as memory_support
import structured_output_support
import writing_support
ContextBudgetError = memory_support.ContextBudgetError
LLMOutputError = writing_support.LLMOutputError


ROOT = Path(__file__).resolve().parents[1]


def _load_generation(app_name, locale, dispatch):
    source = (ROOT / app_name).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "generate_chapter_with_correction"
    )
    module = ast.Module(body=[function], type_ignores=[])
    code = compile(module, app_name, "exec")
    function_code = next(item for item in code.co_consts if isinstance(item, types.CodeType))
    namespace = {
        "ContextBudgetError": ContextBudgetError,
        "DEFAULT_PROMPT_TOKEN_BUDGET": memory_support.DEFAULT_PROMPT_TOKEN_BUDGET,
        "fit_context_sandwich_to_prompt": memory_support.fit_context_sandwich_to_prompt,
        "save_manifest_for_prompt": memory_support.save_manifest_for_prompt,
        "st": types.SimpleNamespace(session_state={}),
        "build_chapter_prompt": lambda sandwich, _prompts, note=None: (
            "BASE\n" + sandwich.get("upstream", "") + "\n" + (note or "")
        ),
        "dispatch_llm_call": dispatch,
        "LLMOutputError": LLMOutputError,
        "require_valid_llm_output": lambda value: value,
        "count_english_words": lambda _text: 1,
        "count_chinese_chars": lambda _text: 1,
        "review_chapter_candidate": lambda *args, **kwargs: {"status": "pass", "completed": True},
        "locale": locale,
    }
    loaded = types.FunctionType(function_code, namespace)
    loaded.__defaults__ = (3, 0.5)
    return loaded, namespace


def _response(content, finish_reason="stop", **extra):
    choice = types.SimpleNamespace(
        message=types.SimpleNamespace(content=content, reasoning_content="SECRET_REASONING"), finish_reason=finish_reason
    )
    response = types.SimpleNamespace(choices=[choice], **extra)
    return response


class ChapterPromptBudgetRC2Tests(unittest.TestCase):
    def test_realistic_8200_token_context_is_not_blocked_by_default_budget(self):
        self.assertEqual(memory_support.DEFAULT_PROMPT_TOKEN_BUDGET, 16000)
        prompt = "\n".join((
            "[RESEARCH FACTS]\n" + "Synthetic supported research fact with defined scope and numeric evidence. " * 145,
            "[GLOBAL OUTLINE]\n" + "Synthetic chapter outline and surrounding section descriptions. " * 145,
            "[LITERATURE]\n" + "Synthetic literature analysis and exact source-grounded evidence. " * 145,
            "[STYLE REQUIREMENTS]\n" + "Synthetic academic writing style and formatting requirements. " * 145,
        ))
        estimate = memory_support.estimate_prompt_tokens(prompt)
        self.assertGreaterEqual(estimate, 8200)
        self.assertLess(estimate, memory_support.DEFAULT_PROMPT_TOKEN_BUDGET)
        fitted = memory_support.fit_context_sandwich_to_prompt(
            {"context_manifest": {}}, lambda _context: prompt
        )
        self.assertFalse(fitted.get("prompt_budget_error", False))

    def test_over_budget_prompt_fails_before_dispatch_with_safe_estimates(self):
        calls = []
        generate, _namespace = _load_generation(
            "app.py", "en", lambda prompt, **kwargs: calls.append((prompt, kwargs))
        )
        original_builder = lambda _sandwich, _prompts, note=None: "x" * 50000 + (note or "")
        namespace = generate.__globals__
        namespace["build_chapter_prompt"] = original_builder
        with self.assertRaises(ContextBudgetError) as caught:
            generate({"current_word_count": 0, "current_refs": [], "context_manifest": {}}, {})
        self.assertGreater(caught.exception.estimated_tokens, caught.exception.max_estimated_tokens)
        self.assertEqual(calls, [])

    def test_correction_prompt_is_refitted_and_manifest_hash_matches_second_request(self):
        calls = []

        def dispatch(prompt, **kwargs):
            calls.append((prompt, kwargs))
            if kwargs.get("json_mode"):
                return {"content": '{"status":"pass","issues":[]}', "finish_reason": "stop", "error_code": None}
            return {"content": "short", "finish_reason": "stop", "error_code": None}

        generate, namespace = _load_generation("app.py", "en", dispatch)
        # The first prompt fits just below 16k estimated tokens. The correction
        # note pushes it over, so the optional memory is removed before request 2.
        sandwich = {
            "current_word_count": 10,
            "current_refs": [],
            "context_manifest": {"selected_memory_ids": [], "omitted_memory_ids": [], "omission_reasons": {}},
            "upstream": "OPTIONAL_CONTEXT " * 230,
            "_base_upstream": "",
            "_base_downstream": "",
            "_memory_entries": [{
                "text": "OPTIONAL_CONTEXT " * 230, "side": "upstream", "priority": 1,
                "chapter_id": "prior", "chapter_title": "Prior", "memory_id": "optional-1",
                "confirmed": False,
            }],
        }
        namespace["build_chapter_prompt"] = lambda context, _prompts, note=None: (
            "x" * 44000 + context.get("upstream", "") + (note or "")
        )
        generate(sandwich, {}, max_attempts=2, tolerance=0.1)
        generation_prompts = [p for p, options in calls if not options.get("json_mode")]
        self.assertEqual(len(generation_prompts), 2)
        self.assertIn("OPTIONAL_CONTEXT", generation_prompts[0])
        manifest = namespace["st"].session_state["last_generation_context_manifest"]
        self.assertEqual(manifest["prompt_hash"], hashlib.sha256(generation_prompts[-1].encode()).hexdigest())
        self.assertIn("optional-1", manifest["omitted_memory_ids"])
        self.assertNotIn("OPTIONAL_CONTEXT", generation_prompts[-1])

    def test_confirmed_memory_is_not_dropped_when_correction_prompt_is_fitted(self):
        calls = []

        def dispatch(prompt, **kwargs):
            calls.append((prompt, kwargs))
            return {"content": "short", "finish_reason": "stop", "error_code": None} if not kwargs.get("json_mode") else {"content": "{}", "finish_reason": "stop", "error_code": None}

        generate, namespace = _load_generation("app_zh.py", "zh", dispatch)
        confirmed = "CONFIRMED_FACT " * 500
        optional = "OPTIONAL_FACT " * 500
        sandwich = {
            "current_word_count": 10, "current_refs": [],
            "context_manifest": {"selected_memory_ids": [], "omitted_memory_ids": [], "omission_reasons": {}},
            "upstream": confirmed + optional, "_base_upstream": "", "_base_downstream": "",
            "_memory_entries": [
                {"text": confirmed, "side": "upstream", "priority": 100, "chapter_id": "p", "chapter_title": "P", "memory_id": "confirmed-1", "confirmed": True},
                {"text": optional, "side": "upstream", "priority": 1, "chapter_id": "p", "chapter_title": "P", "memory_id": "optional-1", "confirmed": False},
            ],
        }
        namespace["build_chapter_prompt"] = lambda context, _prompts, note=None: "x" * 37500 + context.get("upstream", "") + (note or "")
        generate(sandwich, {}, max_attempts=2, tolerance=0.1)
        sent = [p for p, options in calls if not options.get("json_mode")]
        self.assertEqual(len(sent), 2)
        self.assertTrue(all("CONFIRMED_FACT" in prompt for prompt in sent))
        manifest = namespace["st"].session_state["last_generation_context_manifest"]
        self.assertIn("confirmed-1", manifest["selected_memory_ids"])

    def test_correction_budget_failure_reports_prior_request_without_sending_another(self):
        calls = []
        generate, namespace = _load_generation(
            "app.py", "en",
            lambda prompt, **kwargs: calls.append((prompt, kwargs)) or {
                "content": "short", "finish_reason": "stop", "error_code": None,
            },
        )
        namespace["build_chapter_prompt"] = lambda _context, _prompts, note=None: "x" * 47900 + (note or "")
        with self.assertRaises(ContextBudgetError) as caught:
            generate({"current_word_count": 10, "current_refs": [], "context_manifest": {}}, {}, max_attempts=2, tolerance=0.1)
        self.assertEqual(len(calls), 1)
        self.assertTrue(caught.exception.request_already_sent)
        self.assertIn("earlier generation request had been sent", writing_support.context_budget_error_message("en", caught.exception))
        actual_prompt = calls[0][0]
        manifest = namespace["st"].session_state["last_generation_context_manifest"]
        self.assertEqual(manifest["prompt_hash"], hashlib.sha256(actual_prompt.encode()).hexdigest())

    def test_bilingual_budget_message_is_distinct_from_provider_configuration_error(self):
        error = ContextBudgetError("too large", estimated_tokens=17000, max_estimated_tokens=16000)
        for locale, api_error in (
            ("zh", "模型调用失败，请检查当前模型/API 配置后重试。"),
            ("en", "The model call failed. Please check the current model/API configuration and try again."),
        ):
            message = writing_support.context_budget_error_message(locale, error)
            self.assertIn("16,000", message)
            self.assertIn("17,000", message)
            self.assertNotEqual(message, api_error)
            self.assertIn("No chapter-generation request was sent" if locale == "en" else "尚未向 AI 发送", message)
        sent_error = ContextBudgetError(
            "too large", estimated_tokens=17000, max_estimated_tokens=16000, request_already_sent=True
        )
        self.assertIn("earlier generation request had been sent", writing_support.context_budget_error_message("en", sent_error))
        self.assertIn("前一轮生成请求已发送", writing_support.context_budget_error_message("zh", sent_error))

    def test_truncated_nonempty_text_response_fails_closed_before_candidate_return(self):
        calls = []
        generate, _namespace = _load_generation(
            "app.py", "en",
            lambda prompt, **kwargs: calls.append((prompt, kwargs)) or {
                "content": "Incomplete but non-empty draft", "finish_reason": "length", "error_code": None,
            },
        )
        with self.assertRaises(LLMOutputError) as caught:
            generate({"current_word_count": 0, "current_refs": [], "context_manifest": {}}, {})
        self.assertEqual(caught.exception.error_code, "truncated_output")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["max_tokens"], 4000)
        self.assertTrue(calls[0][1]["return_metadata"])


class PlainTextResponseMetadataTests(unittest.TestCase):
    def _load_call(self, app_name, response_factory):
        source = (ROOT / app_name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "call_llm_api")
        code = compile(ast.Module(body=[function], type_ignores=[]), app_name, "exec")
        function_code = next(item for item in code.co_consts if isinstance(item, types.CodeType))
        attempts = []

        def create(**_kwargs):
            attempts.append(dict(_kwargs))
            return response_factory(len(attempts))

        completions = types.SimpleNamespace(create=create)
        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))
        client_options = []

        def openai(**kwargs):
            client_options.append(kwargs)
            return client

        namespace = {
            "OpenAI": openai,
            "time": types.SimpleNamespace(sleep=lambda _seconds: None),
            "call_structured_with_bounded_retry": structured_output_support.call_structured_with_bounded_retry,
            "structured_request_options": structured_output_support.structured_request_options,
            "structured_error_result": structured_output_support.structured_error_result,
            "plain_text_response_result": structured_output_support.plain_text_response_result,
            "plain_text_error_result": structured_output_support.plain_text_error_result,
        }
        loaded = types.FunctionType(function_code, namespace)
        loaded.__defaults__ = (0.7, False, False, True)
        return loaded, attempts, client_options

    def test_plain_text_default_remains_string_and_explicit_metadata_reports_finish_reason(self):
        for app_name in ("app.py", "app_zh.py"):
            with self.subTest(app=app_name):
                call, attempts, client_options = self._load_call(app_name, lambda _index: _response("Complete text"))
                args = ("synthetic prompt", "", 4000, "fake-key", "https://example.invalid/v1", "fake-model")
                plain = call(*args)
                self.assertEqual(plain, "Complete text")
                metadata = call(*args, return_metadata=True)
                self.assertEqual(metadata["content"], "Complete text")
                self.assertEqual(metadata["finish_reason"], "stop")
                self.assertIsNone(metadata["error_code"])
                self.assertEqual(attempts[-1]["max_tokens"], 4000)
                self.assertEqual(client_options[-1]["max_retries"], 0)

    def test_text_call_retries_transient_provider_errors_but_not_authentication_errors(self):
        class ProviderError(Exception):
            def __init__(self, status_code):
                super().__init__("synthetic provider failure sk-do-not-leak")
                self.status_code = status_code

        for app_name in ("app.py", "app_zh.py"):
            with self.subTest(app=app_name, status=503):
                call, attempts, _options = self._load_call(
                    app_name,
                    lambda index: (_ for _ in ()).throw(ProviderError(503)) if index == 1 else _response("Recovered"),
                )
                result = call("synthetic", "", 4000, "fake-key", "https://example.invalid/v1", "fake-model", return_metadata=True)
                self.assertEqual(result["content"], "Recovered")
                self.assertEqual(len(attempts), 2)
            with self.subTest(app=app_name, status=401):
                call, attempts, _options = self._load_call(
                    app_name, lambda _index: (_ for _ in ()).throw(ProviderError(401))
                )
                result = call("synthetic", "", 4000, "fake-key", "https://example.invalid/v1", "fake-model", return_metadata=True)
                self.assertEqual(result["error_code"], "authentication_failed")
                self.assertEqual(result["http_status"], 401)
                self.assertEqual(len(attempts), 1)
                self.assertNotIn("do-not-leak", repr(result))

    def test_text_call_does_not_retry_or_return_nonempty_truncated_output(self):
        call, attempts, _options = self._load_call("app.py", lambda _index: _response("partial text", "length"))
        result = call("synthetic", "", 4000, "fake-key", "https://example.invalid/v1", "fake-model", return_metadata=True)
        self.assertEqual(result["error_code"], "truncated_output")
        self.assertEqual(len(attempts), 1)

    def test_stop_length_empty_and_provider_metadata_are_classified_without_reasoning_text(self):
        stop = structured_output_support.plain_text_response_result(_response("Valid text", status_code=200, usage=types.SimpleNamespace(
            completion_tokens=31,
            completion_tokens_details=types.SimpleNamespace(reasoning_tokens=7),
        )))
        self.assertEqual(stop["content"], "Valid text")
        self.assertEqual(stop["finish_reason"], "stop")
        self.assertEqual(stop["completion_tokens"], 31)
        self.assertEqual(stop["reasoning_tokens"], 7)
        self.assertEqual(stop["http_status"], 200)
        self.assertNotIn("reasoning_content", stop)
        self.assertNotIn("SECRET_REASONING", repr(stop))

        truncated = structured_output_support.plain_text_response_result(_response("partial", "length"))
        self.assertEqual(truncated["finish_reason"], "length")
        self.assertNotIn("http_status", truncated)
        empty = structured_output_support.plain_text_response_result(_response("", "stop"))
        self.assertEqual(empty["error_code"], "empty_content")
        filtered = structured_output_support.plain_text_response_result(_response("", "content_filter"))
        self.assertEqual(filtered["error_code"], "content_filtered")

    def test_provider_errors_are_safe_and_bilingual_messages_are_specific(self):
        error = LLMOutputError("safe failure", error_code="rate_limited")
        self.assertIn("temporarily busy", writing_support.llm_error_message("en", error).casefold())
        self.assertIn("暂时繁忙", writing_support.llm_error_message("zh", error))
        self.assertIn("configuration", writing_support.llm_error_message("en", LLMOutputError("x", error_code="authentication_failed")).casefold())
        self.assertIn("配置", writing_support.llm_error_message("zh", LLMOutputError("x", error_code="authentication_failed")))
        self.assertIn("request parameters", writing_support.llm_error_message("en", LLMOutputError("x", error_code="invalid_request")).casefold())
        self.assertIn("请求参数", writing_support.llm_error_message("zh", LLMOutputError("x", error_code="invalid_request")))


if __name__ == "__main__":
    unittest.main()
