"""Safe diagnostics for real-use recovery paths; all inputs are synthetic."""

import tempfile
import unittest
from unittest.mock import patch

from chapter_memory_support import build_chapter_memory_v2, save_draft_and_maybe_refresh_memory
from writing_support import LLMOutputError, is_transient_provider_failure, llm_error_diagnostic, llm_error_message


class ChapterMemoryFailureDiagnosticTests(unittest.TestCase):
    @staticmethod
    def _response(value):
        import json
        return {"content": json.dumps(value), "finish_reason": "stop", "error_code": None}

    def test_provider_timeout_is_classified_and_saved_draft_is_marked_pending(self):
        draft_writes = []

        def unavailable(_prompt, **_kwargs):
            return {"content": "", "finish_reason": None, "error_code": "timeout", "http_status": 504}

        with tempfile.TemporaryDirectory() as data_dir, patch("chapter_memory_support.time.sleep", lambda _seconds: None):
            result = save_draft_and_maybe_refresh_memory(
                data_dir, {}, "synthetic-chapter", "Synthetic source text.",
                lambda value: draft_writes.append(value) or True, unavailable,
                refresh_memory=True,
            )

        self.assertTrue(result["saved"])
        self.assertTrue(result["memory_pending"])
        self.assertEqual(result["memory_failure"]["category"], "provider_temporarily_unavailable")
        self.assertEqual(result["memory_failure"]["code"], "timeout")
        self.assertEqual(result["memory_failure"]["stage"], "chunk_extraction")
        self.assertEqual(draft_writes, [{"synthetic-chapter": "Synthetic source text."}])

    def test_truncated_memory_response_is_classified_without_partial_snapshot(self):
        calls = []

        def truncated(prompt, **_kwargs):
            calls.append(prompt)
            return {"content": "partial", "finish_reason": "length", "error_code": "truncated_output"}

        with self.assertRaises(Exception) as captured:
            build_chapter_memory_v2("Synthetic source has multiple short sentences. Another sentence.", truncated, max_chars=500)

        error = captured.exception
        self.assertEqual(error.error_code, "truncated_output")
        self.assertEqual(error.safe_details()["category"], "truncated_output")
        self.assertGreater(len(calls), 1)

    def test_invalid_json_and_source_anchor_failures_have_distinct_safe_codes(self):
        invalid_json = lambda _prompt, **_kwargs: {"content": "not-json", "finish_reason": "stop", "error_code": None}
        with self.assertRaises(Exception) as invalid:
            build_chapter_memory_v2("Synthetic source text.", invalid_json)
        self.assertEqual(invalid.exception.error_code, "invalid_json")
        self.assertEqual(invalid.exception.safe_details()["category"], "json_schema_invalid")

        anchor_result = self._response({
            "chunk_status": "content", "summary": "", "items": [{
                "memory_type": "finding", "statement": "Synthetic unsupported fact", "source_quote": "NOT_IN_SOURCE_SENTINEL",
            }],
        })
        with self.assertRaises(Exception) as anchor:
            build_chapter_memory_v2("Synthetic source text.", lambda *_args, **_kwargs: anchor_result)
        self.assertEqual(anchor.exception.error_code, "source_anchor_validation_failed")
        self.assertEqual(anchor.exception.safe_details()["category"], "source_anchor_validation_failed")

    def test_sqlite_persistence_failure_is_reported_as_safe_stage(self):
        valid = self._response({
            "chunk_status": "content", "summary": "Synthetic summary.",
            "items": [{"memory_type": "method", "statement": "Synthetic method.", "source_quote": "Synthetic source"}],
        })
        with tempfile.TemporaryDirectory() as data_dir, patch("chapter_memory_support.save_chapter_memory", side_effect=OSError("private path details")):
            result = save_draft_and_maybe_refresh_memory(
                data_dir, {}, "synthetic-chapter", "Synthetic source text.",
                lambda _value: True, lambda *_args, **_kwargs: valid, refresh_memory=True,
            )

        self.assertTrue(result["saved"])
        self.assertEqual(result["memory_failure"]["code"], "persistence_failure")
        self.assertEqual(result["memory_failure"]["category"], "persistence_failed")
        self.assertEqual(result["memory_failure"]["stage"], "persistence")
        self.assertNotIn("private path details", str(result["memory_failure"]))


class ChapterProviderFailureDiagnosticTests(unittest.TestCase):
    def test_only_bounded_transient_failure_categories_stop_a_batch(self):
        for code in ("rate_limited", "timeout", "connection_error", "server_error", "provider_temporarily_unavailable", "insufficient_system_resource", "aborted"):
            self.assertTrue(is_transient_provider_failure(LLMOutputError("safe", error_code=code)))
        self.assertFalse(is_transient_provider_failure(LLMOutputError("safe", error_code="authentication_failed")))

    def test_provider_interruption_message_does_not_claim_a_retry_that_did_not_happen(self):
        message = llm_error_message("en", LLMOutputError("safe", error_code="aborted"))
        self.assertIn("interrupted", message)
        self.assertNotIn("retried", message)

    def test_safe_metadata_diagnostic_never_includes_provider_text(self):
        error = LLMOutputError(
            "secret prompt/body must not be rendered", error_code="truncated_output",
            http_status=200, finish_reason="length", completion_tokens=4000, reasoning_tokens=128,
        )
        diagnostic = llm_error_diagnostic(error, "en")
        self.assertIn("error_code=truncated_output", diagnostic)
        self.assertIn("HTTP 200", diagnostic)
        self.assertIn("finish_reason=length", diagnostic)
        self.assertIn("completion_tokens=4000", diagnostic)
        self.assertIn("reasoning_tokens=128", diagnostic)
        self.assertNotIn("secret", diagnostic)


if __name__ == "__main__":
    unittest.main()
