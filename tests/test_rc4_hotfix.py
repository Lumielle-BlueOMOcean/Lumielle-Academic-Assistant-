import json
import ast
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
import literature_intelligence as literature
import literature_ui_support as literature_ui

ROOT = Path(__file__).resolve().parents[1]


def _response(content, *, finish_reason="stop"):
    return {
        "content": content,
        "finish_reason": finish_reason,
        "structured_mode": "native",
        "http_status": 200,
        "error_code": None,
    }


def _chunk(method="Survey"):
    return json.dumps({"method": method})


def _profile():
    return json.dumps({"rating": 4, "category": "Empirical Study", "summary": "Supported summary"})


class LiteratureAdaptiveSplitTests(unittest.TestCase):
    def test_length_limited_source_chunk_is_split_and_both_children_are_analyzed(self):
        text = ("BEGIN_SENTINEL. " + "A" * 2760 + "\n\nMIDDLE_SENTINEL. " + "B" * 2760 + "\n\nFINAL_SENTINEL.")
        self.assertGreaterEqual(len(text), 5500)
        calls = []

        def llm(prompt, **kwargs):
            calls.append((prompt, kwargs))
            if "[DOCUMENT SYNTHESIS]" in prompt:
                return _response(_profile())
            if "Source locator: 1 (" in prompt:
                return _response("{", finish_reason="length")
            return _response(_chunk("all sentinels retained"))

        result = literature.analyze_literature_document(text, "Study", llm, max_chars=20000)
        self.assertEqual(result["status"], "ok")
        child_prompts = [p for p, _ in calls if "[SOURCE CHUNK" in p and "Source locator: 1." in p]
        self.assertEqual(len(child_prompts), 2)
        self.assertTrue(any("BEGIN_SENTINEL" in p for p in child_prompts))
        self.assertTrue(any("MIDDLE_SENTINEL" in p for p in child_prompts))
        self.assertTrue(any("FINAL_SENTINEL" in p for p in child_prompts))
        self.assertEqual({kw["max_tokens"] for p, kw in calls if "[SOURCE CHUNK" in p}, {2200})

    def test_source_split_is_lossless_ordered_and_uses_natural_boundaries(self):
        split = getattr(literature, "split_source_chunk_for_retry", None)
        self.assertTrue(callable(split))
        source = "First paragraph.\n\nSecond paragraph has enough text to split.\n\nThird paragraph."
        left, right = split(source)
        self.assertEqual(left + right, source)
        self.assertLess(len(left), len(source))
        self.assertTrue(left.endswith("\n\n"))
        self.assertTrue(right.startswith("Second paragraph"))

    def test_exhausted_split_returns_safe_location_diagnostic_and_no_partial_profile(self):
        text = "FINAL_SECRET " + ("word " * 3000)
        calls = []

        def llm(prompt, **_kwargs):
            calls.append(prompt)
            if "[DOCUMENT SYNTHESIS]" in prompt:
                return _response(_profile())
            return _response("{", finish_reason="length")

        result = literature.analyze_literature_document(text, "Study", llm, max_chars=20000)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["failure_code"], "truncated_output")
        self.assertIn("source_locator", result["diagnostic"])
        self.assertIn("split_depth", result["diagnostic"])
        self.assertEqual(result["diagnostic"]["split_depth"], literature.MAX_SOURCE_SPLIT_DEPTH)
        self.assertNotIn("FINAL_SECRET", repr(result))
        self.assertEqual(result["evidence"], [])
        self.assertLessEqual(len(calls), 2 ** (literature.MAX_SOURCE_SPLIT_DEPTH + 1) - 1)

    def test_final_synthesis_length_retry_keeps_fixed_budget_then_fails_truncated(self):
        calls = []

        def llm(prompt, **kwargs):
            calls.append((prompt, kwargs))
            if "[DOCUMENT SYNTHESIS]" in prompt:
                return _response("{", finish_reason="length")
            return _response(_chunk())

        result = literature.analyze_literature_document("short source", "Study", llm, max_chars=1000)
        synthesis = [kw["max_tokens"] for prompt, kw in calls if "[DOCUMENT SYNTHESIS]" in prompt]
        self.assertEqual(result["failure_code"], "truncated_output")
        self.assertEqual(synthesis, [2600, 2600])

    def test_final_synthesis_compact_retry_can_succeed_at_same_budget(self):
        calls = []

        def llm(prompt, **kwargs):
            calls.append((prompt, kwargs))
            synthesis_count = sum("[DOCUMENT SYNTHESIS]" in prior for prior, _ in calls)
            if "[DOCUMENT SYNTHESIS]" in prompt and synthesis_count == 1:
                return _response("{", finish_reason="length")
            if "[DOCUMENT SYNTHESIS]" in prompt:
                return _response(_profile())
            return _response(_chunk())

        result = literature.analyze_literature_document("short source", "Study", llm, max_chars=1000)
        prompts = [prompt for prompt, _ in calls if "[DOCUMENT SYNTHESIS]" in prompt]
        budgets = [kwargs["max_tokens"] for prompt, kwargs in calls if "[DOCUMENT SYNTHESIS]" in prompt]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(budgets, [2600, 2600])
        self.assertIn("further compress", prompts[1].lower())
        self.assertIn("close the JSON object completely", prompts[1])

    def test_nested_source_split_uses_stable_ordered_locators(self):
        text = ("START_SENTINEL " + "a " * 4000 + "\n\nMIDDLE_SENTINEL " + "b " * 4000 + "\n\nTAIL_SENTINEL")
        prompts = []

        def llm(prompt, **_kwargs):
            prompts.append(prompt)
            if "[DOCUMENT SYNTHESIS]" in prompt:
                return _response(_profile())
            if "Source locator: 1 (" in prompt or "Source locator: 1.2 (" in prompt:
                return _response("{", finish_reason="length")
            return _response(_chunk("processed"))

        result = literature.analyze_literature_document(text, "Study", llm, max_chars=20000)
        self.assertEqual(result["status"], "ok")
        locators = []
        for prompt in prompts:
            if "[SOURCE CHUNK" in prompt:
                match = __import__("re").search(r"Source locator: ([0-9.]+)", prompt)
                if match:
                    locators.append(match.group(1))
        self.assertEqual(locators, ["1", "1.1", "1.2", "1.2.1", "1.2.2"])
        self.assertIn("TAIL_SENTINEL", "\n".join(prompt for prompt in prompts if "[SOURCE CHUNK" in prompt))

    def test_10k_and_50k_documents_process_first_middle_and_tail_source(self):
        for size in (10000, 50000):
            with self.subTest(size=size):
                text = "FIRST_SENTINEL " + ("a" * (size // 2)) + "\nMIDDLE_SENTINEL\n" + ("b" * (size // 2 - 40)) + "\nFINAL_CHAPTER_FACT_SENTINEL"
                prompts = []

                def llm(prompt, **_kwargs):
                    prompts.append(prompt)
                    if "[DOCUMENT SYNTHESIS]" in prompt:
                        self.assertIn("processed final fact", prompt)
                        return _response(_profile())
                    if "[SOURCE CHUNK" in prompt:
                        locator = __import__("re").search(r"Source locator: ([0-9.]+) \((\d+) chars\)", prompt)
                        self.assertIsNotNone(locator)
                        if int(locator.group(2)) > 9000:
                            return _response("{", finish_reason="length")
                        method = "processed final fact" if "FINAL_CHAPTER_FACT_SENTINEL" in prompt else "processed other section"
                        return _response(_chunk(method))
                    self.fail("unexpected LLM call")

                result = literature.analyze_literature_document(text, "Study", llm, max_chars=size + 1000)
                self.assertEqual(result["status"], "ok")
                source_prompts = "\n".join(prompt for prompt in prompts if "[SOURCE CHUNK" in prompt)
                self.assertIn("FIRST_SENTINEL", source_prompts)
                self.assertIn("MIDDLE_SENTINEL", source_prompts)
                self.assertIn("FINAL_CHAPTER_FACT_SENTINEL", source_prompts)
                self.assertIn("processed final fact", "\n".join(prompt for prompt in prompts if "[DOCUMENT SYNTHESIS]" in prompt))

    def test_evidence_quote_is_validated_against_exact_leaf_and_keeps_leaf_locator(self):
        left_quote = "LEFT EXACT QUOTE"
        right_quote = "RIGHT EXACT QUOTE"
        text = left_quote + " " + ("A " * 1800) + "\n\n" + ("B " * 1800) + " " + right_quote

        def llm(prompt, **_kwargs):
            if "[DOCUMENT SYNTHESIS]" in prompt:
                return _response(_profile())
            if "Source locator: 1 (" in prompt:
                return _response("{", finish_reason="length")
            if "Source locator: 1.1 (" in prompt:
                return _response(json.dumps({"method": "A", "claims": [
                    {"claim": "A supported", "evidence_text": left_quote},
                    {"claim": "Wrong leaf", "evidence_text": right_quote},
                ]}))
            return _response(json.dumps({"method": "B", "claims": [
                {"claim": "B supported", "evidence_text": right_quote},
            ]}))

        result = literature.analyze_literature_document(text, "Study", llm, max_chars=20000)
        self.assertEqual(result["status"], "ok")
        self.assertEqual([item["claim"] for item in result["evidence"]], ["A supported", "B supported"])
        self.assertEqual([item["source_locator"] for item in result["evidence"]], ["chunk 1.1", "chunk 1.2"])

    def test_evidence_quote_is_not_trimmed_before_provenance_validation(self):
        def llm(prompt, **_kwargs):
            if "[DOCUMENT SYNTHESIS]" in prompt:
                return _response(_profile())
            return _response(json.dumps({"method": "Survey", "claims": [
                {"claim": "Claim", "evidence_text": " EXACT SOURCE QUOTE "},
            ]}))

        result = literature.analyze_literature_document("EXACT SOURCE QUOTE", "Study", llm, max_chars=1000)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["evidence"], [])

    def test_reduction_length_splits_group_without_increasing_token_budget(self):
        group = [
            {"source_locator": "1", "extraction": {"method": "A", "claims": []}},
            {"source_locator": "2", "extraction": {"method": "B", "claims": []}},
        ]
        calls = []

        def llm(prompt, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return _response("{", finish_reason="length")
            return _response(_chunk("compact"))

        reduced = literature._reduce_extraction_group(llm, "Study", group, 1, "en")
        self.assertEqual(len(reduced), 2)
        self.assertEqual([kwargs["max_tokens"] for kwargs in calls], [1800, 1800, 1800])

    def test_non_converging_reduction_fails_closed_with_bounded_levels(self):
        extraction = {
            "source_locator": "1",
            "extraction": {"method": "x" * 100, "claims": [], **{name: "" for name in literature._CHUNK_TEXT_FIELDS if name != "method"}},
        }
        calls = []

        def llm(prompt, **_kwargs):
            calls.append(prompt)
            return _response(_chunk("x" * 100))

        with patch.object(literature, "MAX_SYNTHESIS_INPUT_CHARS", 10), patch.object(literature, "MAX_REDUCTION_GROUP_CHARS", 10000):
            with self.assertRaises(literature._StructuredCallFailure) as raised:
                literature._hierarchically_reduce_extractions(llm, "Study", [extraction], "en")
        self.assertEqual(raised.exception.failure_code, "synthesis_reduction_failure")
        self.assertLessEqual(len(calls), literature.MAX_REDUCTION_LEVELS * 2)

    def test_extraction_prompt_requests_concise_claims_and_short_exact_quotes(self):
        for locale in ("en", "zh"):
            prompt = literature.build_chunk_extraction_prompt("Study", "SOURCE", 1, 1, locale)
            self.assertIn("6", prompt)
            self.assertIn("300", prompt)
            self.assertIn("evidence_text", prompt)

    def test_long_document_triggers_ordered_hierarchical_reduction(self):
        pieces = [f"SECTION_{index:02d}. " + (chr(65 + index) * 2500) for index in range(8)]
        text = "\n\n".join(pieces)
        calls = []
        extraction_index = 0

        def llm(prompt, **kwargs):
            nonlocal extraction_index
            calls.append((prompt, kwargs))
            if "[DOCUMENT SYNTHESIS REDUCTION]" in prompt:
                return _response(_chunk("compact ordered digest"))
            if "[DOCUMENT SYNTHESIS]" in prompt:
                return _response(_profile())
            extraction_index += 1
            return _response(_chunk(f"FACT_{extraction_index:02d}_" + ("x" * 4000)))

        result = literature.analyze_literature_document(text, "Study", llm, max_chars=3000)
        self.assertEqual(result["status"], "ok")
        reductions = [prompt for prompt, _ in calls if "[DOCUMENT SYNTHESIS REDUCTION]" in prompt]
        self.assertTrue(reductions)
        all_reduction_inputs = "\n".join(reductions)
        for index in range(1, extraction_index + 1):
            self.assertIn(f"FACT_{index:02d}_", all_reduction_inputs)
        self.assertEqual(
            {kwargs["max_tokens"] for prompt, kwargs in calls if "[DOCUMENT SYNTHESIS REDUCTION]" in prompt},
            {1800},
        )


class LiteratureTaxonomyAndPresentationTests(unittest.TestCase):
    def test_missing_category_is_unclassified_while_other_remains_explicit(self):
        missing = literature.normalize_profile({"rating": 4, "summary": "Has academic content"})
        explicit_other = literature.normalize_profile({"rating": 4, "category": "Other", "summary": "Content"})
        self.assertEqual(missing["category"], "Unclassified")
        self.assertEqual(explicit_other["category"], "Other")

    def test_synthesis_prompt_uses_controlled_taxonomy_and_concise_example(self):
        prompt = literature.build_synthesis_prompt("Study", [{"method": "survey"}])
        self.assertIn("Unclassified", prompt)
        self.assertIn("Literature Review", prompt)
        self.assertIn("Policy / Official Document", prompt)
        self.assertIn("Standard / Guideline", prompt)
        self.assertIn("Data / Research Report", prompt)
        self.assertIn("Thesis / Dissertation", prompt)
        self.assertIn("Theoretical / Conceptual Study", prompt)
        self.assertIn('"category":"Empirical Study"', prompt)
        self.assertIn("Only use Other", prompt)
        self.assertNotIn('"category":"Other"', prompt)

    def test_failed_record_summary_never_falls_back_to_raw_extracted_text(self):
        render = getattr(literature_ui, "literature_summary_for_display", None)
        self.assertTrue(callable(render))
        failed = {"analysis_status": "failed", "summary": "RAW EXTRACTION PREFIX PRIVATE"}
        success = {"analysis_status": "ok", "summary": "RAW", "analysis": {"summary": "AI summary"}}
        self.assertNotIn("RAW EXTRACTION", render(failed, "en"))
        self.assertIn("not generated", render(failed, "en").lower())
        self.assertIn("AI literature summary: not generated because analysis is incomplete.", render(failed, "en"))
        self.assertIn("AI summary", render(success, "en"))
        self.assertIn("尚未生成", render(failed, "zh"))

    def test_category_display_localizes_unclassified_and_other(self):
        display = getattr(literature_ui, "display_literature_category", None)
        self.assertTrue(callable(display))
        self.assertEqual(display("Unclassified", "zh"), "未分类")
        self.assertEqual(display("Other", "zh"), "其他")
        self.assertEqual(display("Unclassified", "en"), "Unclassified")
        self.assertEqual(display("Methodological Study", "zh"), "方法研究")
        self.assertEqual(display("unexpected", "zh"), "未分类")

    def test_common_category_aliases_normalize_to_controlled_labels(self):
        cases = {
            "systematic review": "Literature Review",
            "survey study": "Empirical Study",
            "empirical research": "Empirical Study",
            "case report": "Case Study",
            "methodology": "Methodological Study",
            "unfamiliar explicit category": "Unclassified",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                profile = literature.normalize_profile({"rating": 3, "category": source, "summary": "content"})
                self.assertEqual(profile["category"], expected)

    def test_bilingual_literature_ui_uses_pin_not_importance_stars_or_raw_prefix(self):
        for filename in ("app.py", "app_zh.py"):
            source = (ROOT / filename).read_text(encoding="utf-8")
            tree = ast.parse(source)
            module = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "module3_literature")
            module_source = ast.get_source_segment(source, module)
            self.assertIn("literature_summary_for_display(lit", module_source)
            self.assertIn("literature_importance_control(lit", module_source)
            self.assertIn("toggle_literature_importance(lit)", module_source)
            self.assertNotIn('st.button("🌟"', module_source)
            self.assertNotIn('star_prefix = "⭐⭐⭐ "', module_source)
            self.assertNotIn("lit['summary'][:200]", module_source)

    def test_importance_marker_toggles_without_changing_rating_and_is_explained(self):
        record = {"important": False, "rating": 5}
        label_en, help_en = literature_ui.literature_importance_control(record, "en")
        label_zh, help_zh = literature_ui.literature_importance_control(record, "zh")
        self.assertEqual(label_en, "📌 Mark important")
        self.assertEqual(label_zh, "📌 标记重点")
        self.assertIn("does not change the AI relevance rating", help_en)
        self.assertIn("不会自动绑定到章节", help_zh)
        self.assertTrue(literature_ui.toggle_literature_importance(record))
        self.assertEqual(record["rating"], 5)
        self.assertEqual(literature_ui.literature_importance_control(record, "zh")[0], "📌 已标记重点")
        self.assertFalse(literature_ui.toggle_literature_importance(record))
        self.assertEqual(record["rating"], 5)

    def test_adaptive_failure_details_and_safe_diagnostics_are_file_bound(self):
        outcome = {
            "filename": "paper.pdf", "success": False, "source_saved": True,
            "result": {
                "failure_code": "truncated_output",
                "diagnostic": {"source_locator": "2.1", "split_depth": 3, "source_chars": 740},
            },
        }
        rendered = literature_ui.format_batch_outcome(outcome, "en")
        self.assertIn("paper.pdf", rendered)
        self.assertIn("source part 2.1", rendered)
        safe = literature_ui._safe_batch_notice([{
            **outcome,
            "result": {
                **outcome["result"],
                "diagnostic": {
                    "source_locator": "2.1", "split_depth": 3, "source_chars": 740,
                    "prompt": "PRIVATE SOURCE TEXT", "source_locator_bad": "PRIVATE SOURCE",
                },
            },
        }])
        self.assertEqual(safe[0]["result"]["diagnostic"]["source_locator"], "2.1")
        self.assertNotIn("PRIVATE SOURCE", repr(safe))

    def test_library_summary_category_and_importance_render_in_both_apps(self):
        for module_name, locale, failed_label, success_label, localized_category, marked_label in (
            ("app", "en", "AI literature summary: not generated because analysis is incomplete.",
             "AI literature summary: Successful AI summary", "Unclassified", "📌 Important"),
            ("app_zh", "zh", "AI 文献摘要：尚未生成（文献分析未完成）。",
             "AI 文献摘要： Successful AI summary", "未分类", "📌 已标记重点"),
        ):
            with self.subTest(locale=locale), tempfile.TemporaryDirectory() as temp:
                script = Path(temp) / "library_app.py"
                script.write_text("\n".join((
                    "import sys",
                    f"sys.path.insert(0, {str(ROOT)!r})",
                    "import streamlit as st",
                    f"import {module_name} as app",
                    "if '_records' not in st.session_state:",
                    "    st.session_state['_records'] = [",
                    "        {'id':'failed','title':'Failed paper','summary':'GARBLED_RAW_SENTINEL','analysis_status':'unavailable','analysis':{'analysis_status':'unavailable'},'rating':0,'category':'Unrated','file_path':'','important':False},",
                    "        {'id':'success','title':'Success paper','summary':'raw','analysis_status':'ok','analysis':{'summary':'Successful AI summary'},'rating':4,'category':'Unclassified','file_path':'','important':False},",
                    "    ]",
                    "def _load(name, default=None):",
                    "    if name == 'literatures.json': return st.session_state['_records']",
                    "    if name == 'literature_evidence.json': return {}",
                    "    if name == 'prompts.json': return {}",
                    "    return default",
                    "app.load_json_file = _load",
                    "app.save_json_file = lambda name, value: st.session_state.update(_records=value) if name == 'literatures.json' else None",
                    "app.get_research_context_text = lambda: ''",
                    f"app.RAW_DIR = {str(Path(temp) / 'raw_files')!r}",
                    "app.module3_literature()",
                )), encoding="utf-8")
                app_test = AppTest.from_file(str(script), default_timeout=30).run()
                self.assertFalse(app_test.exception)
                rendered = "\n".join(item.value for item in app_test.markdown)
                self.assertIn(failed_label, rendered)
                self.assertIn(success_label, rendered)
                self.assertNotIn("GARBLED_RAW_SENTINEL", rendered)
                self.assertIn(localized_category, rendered)
                button = next(item for item in app_test.button if item.key == "litstar_success")
                self.assertEqual(button.label, "📌 Mark important" if locale == "en" else "📌 标记重点")
                button.click()
                app_test.run()
                self.assertTrue(app_test.session_state["_records"][1]["important"])
                self.assertEqual(app_test.session_state["_records"][1]["rating"], 4)
                self.assertIn(marked_label, [item.label for item in app_test.button])


if __name__ == "__main__":
    unittest.main()
