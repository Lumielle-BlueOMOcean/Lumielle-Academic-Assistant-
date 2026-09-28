import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import llm_connection_support


class FakeHTTPError(Exception):
    def __init__(self, status_code, message="provider failure"):
        super().__init__(message)
        self.status_code = status_code


def successful_client(response=None):
    completion = response or SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Different valid response"))]
    )
    create = Mock(return_value=completion)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return client, create


class LLMConnectionSupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.support = llm_connection_support

    def test_connection_accepts_a_valid_chat_completion(self):
        client, create = successful_client()
        with patch.object(self.support, "OpenAI", return_value=client):
            result = self.support.test_model_connection(
                "https://provider.example/v1", "sk-test", "chat-model"
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], "ok")
        self.assertEqual(result["model"], "chat-model")
        self.assertEqual(create.call_args.kwargs["messages"], [{"role": "user", "content": "Reply with OK."}])
        self.assertEqual(create.call_args.kwargs["temperature"], 0)
        self.assertLessEqual(create.call_args.kwargs["max_tokens"], 8)

    def test_connection_rejects_missing_configuration_without_network(self):
        for values, expected in (
            (("", "key", "model"), "missing_base_url"),
            (("https://provider.example", "", "model"), "missing_api_key"),
            (("https://provider.example", "key", ""), "missing_model"),
        ):
            with self.subTest(code=expected), patch.object(self.support, "OpenAI") as client:
                result = self.support.test_model_connection(*values)
                self.assertFalse(result["ok"])
                self.assertEqual(result["code"], expected)
                client.assert_not_called()

    def test_http_errors_are_mapped_to_user_safe_codes(self):
        for status_code, expected in (
            (401, "authentication_failed"),
            (402, "insufficient_balance"),
            (404, "model_not_available"),
            (429, "rate_limited"),
            (503, "provider_error"),
        ):
            with self.subTest(status=status_code):
                client, _ = successful_client()
                client.chat.completions.create.side_effect = FakeHTTPError(status_code)
                with patch.object(self.support, "OpenAI", return_value=client):
                    result = self.support.test_model_connection(
                        "https://provider.example/v1", "sk-test", "chat-model"
                    )
                self.assertFalse(result["ok"])
                self.assertEqual(result["code"], expected)
                self.assertEqual(result["http_status"], status_code)

    def test_timeout_and_connection_failures_have_distinct_codes(self):
        import httpx
        import openai
        import requests

        request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
        for error, expected in (
            (requests.exceptions.Timeout("timed out"), "timeout"),
            (openai.APITimeoutError(request=request), "timeout"),
            (requests.exceptions.ConnectionError("offline"), "connection_error"),
            (openai.APIConnectionError(request=request), "connection_error"),
        ):
            with self.subTest(code=expected):
                client, _ = successful_client()
                client.chat.completions.create.side_effect = error
                with patch.object(self.support, "OpenAI", return_value=client):
                    result = self.support.test_model_connection(
                        "https://provider.example/v1", "sk-test", "chat-model"
                    )
                self.assertFalse(result["ok"])
                self.assertEqual(result["code"], expected)

    def test_provider_error_text_never_returns_an_api_key(self):
        secret = "sk-secret-test-value"
        client, _ = successful_client()
        client.chat.completions.create.side_effect = FakeHTTPError(503, f"request failed for {secret}")
        with patch.object(self.support, "OpenAI", return_value=client):
            result = self.support.test_model_connection(
                "https://provider.example/v1", secret, "chat-model"
            )

        self.assertNotIn(secret, repr(result))
        self.assertEqual(result["code"], "provider_error")

    def test_deepseek_receives_only_its_non_thinking_request_option(self):
        client, create = successful_client()
        with patch.object(self.support, "OpenAI", return_value=client):
            result = self.support.test_model_connection(
                "https://api.deepseek.com/", "sk-test", "deepseek-flash"
            )

        self.assertTrue(result["ok"])
        self.assertEqual(create.call_args.kwargs["extra_body"], {"thinking": {"type": "disabled"}})

    def test_generic_provider_does_not_receive_deepseek_options(self):
        client, create = successful_client()
        with patch.object(self.support, "OpenAI", return_value=client):
            self.support.test_model_connection(
                "https://provider.example/v1/", "sk-test", "chat-model"
            )

        self.assertNotIn("extra_body", create.call_args.kwargs)

    def test_model_list_url_preserves_provider_paths_without_duplicate_models(self):
        response = SimpleNamespace(status_code=200, json=lambda: {"data": [{"id": "model-a"}]})
        for base_url, expected_url in (
            ("https://api.deepseek.com", "https://api.deepseek.com/models"),
            ("https://api.deepseek.com/", "https://api.deepseek.com/models"),
            ("https://provider.example/v1", "https://provider.example/v1/models"),
            ("https://provider.example/v1/", "https://provider.example/v1/models"),
            ("https://provider.example/v1/models/", "https://provider.example/v1/models"),
        ):
            with self.subTest(base_url=base_url), patch.object(self.support.requests, "get", return_value=response) as get:
                result = self.support.fetch_available_models(base_url, "sk-test")
                self.assertTrue(result["ok"])
                self.assertEqual(result["models"], ["model-a"])
                self.assertEqual(get.call_args.args[0], expected_url)

    def test_model_list_endpoint_errors_are_safe_and_distinct_from_model_errors(self):
        for status_code, expected in ((401, "authentication_failed"), (404, "provider_error"), (429, "rate_limited")):
            response = SimpleNamespace(status_code=status_code, text="secret response that must not be shown")
            with self.subTest(status=status_code), patch.object(self.support.requests, "get", return_value=response):
                result = self.support.fetch_available_models("https://provider.example/v1", "sk-test")
                self.assertFalse(result["ok"])
                self.assertEqual(result["code"], expected)
                self.assertNotIn("secret response", repr(result))

    def test_malformed_model_list_url_returns_a_safe_result(self):
        result = self.support.fetch_available_models("https://[invalid", "sk-test")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "unknown_error")

    def test_configuration_signature_changes_for_each_connection_input(self):
        original = self.support.make_connection_signature(
            "https://provider.example/v1", "sk-secret", "model-a"
        )
        changed = (
            self.support.make_connection_signature("https://other.example/v1", "sk-secret", "model-a"),
            self.support.make_connection_signature("https://provider.example/v1", "sk-other", "model-a"),
            self.support.make_connection_signature("https://provider.example/v1", "sk-secret", "model-b"),
        )

        self.assertEqual(len(original), 64)
        self.assertTrue(all(value != original for value in changed))
        self.assertNotIn("sk-secret", original)

    def test_fresh_deepseek_profile_uses_current_api_default(self):
        profile = self.support.default_deepseek_profile("DeepSeek Default Profile")

        self.assertEqual(profile, {
            "id": "default",
            "name": "DeepSeek Default Profile",
            "base_url": "https://api.deepseek.com",
            "api_key": "",
            "model": "deepseek-flash",
        })


if __name__ == "__main__":
    unittest.main()
