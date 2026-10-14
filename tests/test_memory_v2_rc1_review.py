"""Regression tests for the bounded Memory V2 integration review fixes."""

import ast
import json
import shutil
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path

from chapter_memory_store import initialize_memory_store, save_chapter_memory
from chapter_memory_support import build_context_sandwich_v2, fit_context_sandwich_to_prompt
from chapter_consistency import check_deterministic_consistency


ROOT = Path(__file__).resolve().parents[1]


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _copy_app(root, app_name):
    for source in ROOT.glob("*.py"):
        shutil.copy2(source, root / source.name)
    shutil.copy2(ROOT / app_name, root / "app.py")
    data_dir = root / "data"
    data_dir.mkdir(exist_ok=True)
    return data_dir


def _save_memory(data_dir, chapter_id, body, summary, *, statement="The construct is stable.", quote="construct", confirmed=False):
    start = body.index(quote)
    save_chapter_memory(data_dir, chapter_id, body, {
        "narrative_summary": summary,
        "completion_status": "complete",
        "items": [{
            "memory_type": "definition",
            "statement": statement,
            "source_quote": quote,
            "source_start": start,
            "source_end": start + len(quote),
            "confirmed_for_writing": confirmed,
        }],
    })


class MemoryRebuildFailureUIRegressionTests(unittest.TestCase):
    def test_clicking_rebuild_on_provider_failure_shows_friendly_warning_in_both_locales(self):
        from streamlit.testing.v1 import AppTest

        for locale, button, warning in (
            ("en", "Rebuild this chapter's memory", "Memory update did not finish."),
            ("zh", "重新建立本章记忆", "记忆更新未完成。"),
        ):
            with self.subTest(locale=locale), tempfile.TemporaryDirectory() as temp_dir:
                script = Path(temp_dir) / "memory_failure_ui.py"
                script.write_text(
                    "import sys\n"
                    f"sys.path.insert(0, {str(ROOT)!r})\n"
                    "import streamlit as st\n"
                    "from chapter_memory_ui import render_chapter_memory_panel\n"
                    "def failing_llm(prompt, **kwargs):\n"
                    "    return {'content': '', 'error_code': 'authentication_failed', 'finish_reason': None}\n"
                    f"render_chapter_memory_panel(st, data_dir={str(Path(temp_dir) / 'data')!r}, chapter_id='c1', draft_text='Synthetic saved chapter.', legacy_summary=None, llm_call=failing_llm, locale={locale!r})\n",
                    encoding="utf-8",
                )
                app = AppTest.from_file(str(script), default_timeout=30).run()
                self.assertFalse(app.exception)
                next(item for item in app.button if item.label == button).click().run()
                self.assertFalse(app.exception)
                self.assertTrue(any(warning in item.value for item in app.warning))
                self.assertFalse(any("updated" in item.value.casefold() for item in app.success))


class GlobalMemoryClearRegressionTests(unittest.TestCase):
    def _prepare_app_data(self, app_root, app_name, *, fail_clear=False):
        from chapter_memory_store import database_path

        data_dir = _copy_app(app_root, app_name)
        body = "A synthetic construct appears in this test draft."
        _write_json(data_dir / "drafts.json", {"chapter-1": body})
        _write_json(data_dir / "drafts_summary.json", {"chapter-1": "OLD_SUMMARY"})
        _save_memory(data_dir, "chapter-1", body, "NEW_MEMORY_SUMMARY")
        if fail_clear:
            connection = sqlite3.connect(database_path(data_dir))
            try:
                connection.execute(
                    "CREATE TRIGGER reject_memory_clear BEFORE DELETE ON chapter_memories "
                    "BEGIN SELECT RAISE(ABORT, 'synthetic clear failure'); END"
                )
                connection.commit()
            finally:
                connection.close()
        return data_dir, body

    def test_global_clear_removes_legacy_and_sqlite_memory_but_preserves_drafts_in_both_apps(self):
        from streamlit.testing.v1 import AppTest
        from chapter_memory_store import read_chapter_memory

        cases = (
            ("app.py", "🧠 Chapter Memory Pool", "Execute Clear", "memory"),
            ("app_zh.py", "🧠 章节记忆池", "执行清除", "memory"),
        )
        for app_name, selection, button_label, _ in cases:
            with self.subTest(app=app_name), tempfile.TemporaryDirectory() as temp_dir:
                app_root = Path(temp_dir)
                data_dir, body = self._prepare_app_data(app_root, app_name)
                app = AppTest.from_file(str(app_root / "app.py"), default_timeout=30).run()
                next(item for item in app.multiselect if item.key == "clear_sel").set_value([selection]).run()
                next(item for item in app.checkbox if item.key == "clear_confirm").set_value(True).run()
                next(item for item in app.button if button_label in item.label).click().run()
                self.assertFalse(app.exception)
                self.assertEqual(json.loads((data_dir / "drafts_summary.json").read_text(encoding="utf-8")), {})
                self.assertEqual(json.loads((data_dir / "drafts.json").read_text(encoding="utf-8")), {"chapter-1": body})
                self.assertEqual(read_chapter_memory(data_dir, "chapter-1", body)["status"], "memory_missing")
                messages = "\n".join(item.value for group in (app.success, app.warning, app.error) for item in group)
                self.assertIn("cleared" if app_name == "app.py" else "已清除", messages.casefold() if app_name == "app.py" else messages)

    def test_sqlite_clear_failure_is_reported_and_does_not_claim_complete_memory_clear(self):
        from streamlit.testing.v1 import AppTest
        from chapter_memory_store import read_chapter_memory

        for app_name, selection, button_label in (
            ("app.py", "🧠 Chapter Memory Pool", "Execute Clear"),
            ("app_zh.py", "🧠 章节记忆池", "执行清除"),
        ):
            with self.subTest(app=app_name), tempfile.TemporaryDirectory() as temp_dir:
                app_root = Path(temp_dir)
                data_dir, body = self._prepare_app_data(app_root, app_name, fail_clear=True)
                app = AppTest.from_file(str(app_root / "app.py"), default_timeout=30).run()
                next(item for item in app.multiselect if item.key == "clear_sel").set_value([selection]).run()
                next(item for item in app.checkbox if item.key == "clear_confirm").set_value(True).run()
                next(item for item in app.button if button_label in item.label).click().run()
                self.assertFalse(app.exception)
                self.assertEqual(json.loads((data_dir / "drafts_summary.json").read_text(encoding="utf-8")), {"chapter-1": "OLD_SUMMARY"})
                self.assertEqual(json.loads((data_dir / "drafts.json").read_text(encoding="utf-8")), {"chapter-1": body})
                self.assertEqual(read_chapter_memory(data_dir, "chapter-1", body)["status"], "current")
                messages = "\n".join(item.value for group in (app.success, app.warning, app.error) for item in group)
                expected = "memory" if app_name == "app.py" else "记忆"
                self.assertIn(expected, messages.casefold() if app_name == "app.py" else messages)
                self.assertFalse(any("cleared" in item.value.casefold() for item in app.success) if app_name == "app.py" else any("已清除" in item.value for item in app.success))


class ContextManifestRerunRegressionTests(unittest.TestCase):
    def _generation_source(self, app_name):
        tree = ast.parse((ROOT / app_name).read_text(encoding="utf-8"))
        node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "generate_chapter_with_correction")
        return ast.get_source_segment((ROOT / app_name).read_text(encoding="utf-8"), node)

    def test_apps_keep_preview_separate_from_last_actual_generation(self):
        for app_name in ("app.py", "app_zh.py"):
            with self.subTest(app=app_name):
                source = (ROOT / app_name).read_text(encoding="utf-8")
                self.assertIn('"chapter_context_preview_manifest"', source)
                self.assertIn('"last_generation_context_manifest"', source)
                self.assertNotIn('st.session_state["chapter_context_manifest"]', source)

    def test_actual_generation_manifest_survives_a_real_streamlit_rerun(self):
        from streamlit.testing.v1 import AppTest

        for app_name, locale in (("app.py", "en"), ("app_zh.py", "zh")):
            with self.subTest(app=app_name), tempfile.TemporaryDirectory() as temp_dir:
                script = Path(temp_dir) / "generation_manifest_app.py"
                function_source = self._generation_source(app_name)
                script.write_text(
                    "import sys, hashlib\n"
                    f"sys.path.insert(0, {str(ROOT)!r})\n"
                    "import streamlit as st\n"
                    "from chapter_memory_support import save_manifest_for_prompt, fit_context_sandwich_to_prompt, ContextBudgetError, DEFAULT_PROMPT_TOKEN_BUDGET\n"
                    "from chapter_consistency import review_chapter_candidate\n"
                    "from chapter_memory_ui import render_context_manifest_summary\n"
                    "LLMOutputError = RuntimeError\n"
                    f"locale = {locale!r}\n"
                    "def build_chapter_prompt(sandwich, prompts, note=None): return 'Exact generated prompt' + (' ' + note if note else '')\n"
                    "def count_english_words(text): return 2\n"
                    "def count_chinese_chars(text): return 2\n"
                    "def require_valid_llm_output(value):\n"
                    "    if not isinstance(value, str) or not value.strip(): raise RuntimeError('empty')\n"
                    "    return value\n"
                    "def dispatch_llm_call(prompt, **kwargs):\n"
                    "    if kwargs.get('json_mode'): return {'content':'{\"status\":\"pass\",\"issues\":[]}', 'finish_reason':'stop', 'error_code':None}\n"
                    "    return 'Generated chapter body.'\n"
                    + function_source + "\n"
                    "st.session_state.setdefault('chapter_context_preview_manifest', {'selected_memory_ids':['INITIAL_PREVIEW']})\n"
                    "if st.button('Generate'):\n"
                    "    sandwich = {'current_word_count': 0, 'current_refs': [], 'context_manifest': {'selected_memory_ids':['ACTUAL_MEMORY_42'], 'selected_chapters':[{'chapter_id':'c1','title':'Background'}], 'omission_reasons':{}}, '_base_upstream':'', '_base_downstream':'', '_memory_entries':[{'memory_id':'ACTUAL_MEMORY_42','chapter_id':'c1','chapter_title':'Background','side':'upstream','priority':1,'text':'Synthetic confirmed context','confirmed':True,'statement':'Synthetic confirmed fact','source_quote':'Synthetic quote'}]}\n"
                    "    generate_chapter_with_correction(sandwich, {})\n"
                    "    st.session_state['chapter_context_preview_manifest'] = {'selected_memory_ids':[]}\n"
                    "    st.rerun()\n"
                    "render_context_manifest_summary(st, locale=locale)\n",
                    encoding="utf-8",
                )
                app = AppTest.from_file(str(script), default_timeout=30).run()
                next(item for item in app.button if item.label == "Generate").click().run()
                self.assertFalse(app.exception)
                rendered = "\n".join(item.value for item in (*app.markdown, *app.caption, *app.code) if hasattr(item, "value"))
                if locale == "en":
                    self.assertIn("Selected memory items: 0", rendered)
                    self.assertIn("Selected memory items: 1", rendered)
                    self.assertIn("Background", rendered)
                else:
                    self.assertIn("选用的记忆条目：0", rendered)
                    self.assertIn("选用的记忆条目：1", rendered)
                    self.assertIn("Background", rendered)
                import hashlib
                expected_hash = hashlib.sha256("Exact generated prompt".encode("utf-8")).hexdigest()
                self.assertIn(expected_hash, rendered)

    def test_length_correction_tracks_the_manifest_for_the_last_generation_request(self):
        import hashlib

        for app_name, locale in (("app.py", "en"), ("app_zh.py", "zh")):
            with self.subTest(app=app_name):
                root = ROOT
                source = (root / app_name).read_text(encoding="utf-8")
                tree = ast.parse(source)
                node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "generate_chapter_with_correction")
                code = compile(ast.Module(body=[node], type_ignores=[]), app_name, "exec")
                function_code = next(item for item in code.co_consts if hasattr(item, "co_code"))
                calls = []

                def dispatch(prompt, **kwargs):
                    calls.append((prompt, kwargs))
                    if kwargs.get("json_mode"):
                        return {"content": '{"status":"pass","issues":[]}', "finish_reason": "stop", "error_code": None}
                    return "Short candidate."

                namespace = {
                    "st": types.SimpleNamespace(session_state={}),
                    "ContextBudgetError": __import__("chapter_memory_support").ContextBudgetError,
                    "DEFAULT_PROMPT_TOKEN_BUDGET": __import__("chapter_memory_support").DEFAULT_PROMPT_TOKEN_BUDGET,
                    "fit_context_sandwich_to_prompt": __import__("chapter_memory_support").fit_context_sandwich_to_prompt,
                    "build_chapter_prompt": lambda _sandwich, _prompts, note=None: "Exact generated prompt" + (f"\n{note}" if note else ""),
                    "dispatch_llm_call": dispatch,
                    "require_valid_llm_output": lambda value: value,
                    "count_english_words": lambda _text: 1,
                    "count_chinese_chars": lambda _text: 1,
                    "save_manifest_for_prompt": __import__("chapter_memory_support").save_manifest_for_prompt,
                    "review_chapter_candidate": __import__("chapter_consistency").review_chapter_candidate,
                    "LLMOutputError": RuntimeError,
                    "locale": locale,
                }
                generate = types.FunctionType(function_code, namespace)
                generate.__defaults__ = (3, 0.5)
                generate({
                    "current_word_count": 20, "current_refs": [],
                    "context_manifest": {"selected_memory_ids": [7]},
                    "_base_upstream": "", "_base_downstream": "",
                    "_memory_entries": [{
                        "memory_id": 7, "chapter_id": "prior", "chapter_title": "Prior",
                        "side": "upstream", "priority": 1, "text": "Synthetic prior memory",
                        "confirmed": False,
                    }],
                }, {}, max_attempts=2, tolerance=0.1)
                generation_prompts = [prompt for prompt, kwargs in calls if not kwargs.get("json_mode")]
                manifest = namespace["st"].session_state["last_generation_context_manifest"]
                self.assertEqual(len(generation_prompts), 2)
                self.assertNotEqual(generation_prompts[0], generation_prompts[1])
                self.assertEqual(manifest["prompt_hash"], hashlib.sha256(generation_prompts[-1].encode("utf-8")).hexdigest())
                self.assertEqual(manifest["selected_memory_ids"], [7])


class ConservativeNumericConflictRegressionTests(unittest.TestCase):
    def _check(self, statement, quote, candidate):
        return check_deterministic_consistency(candidate, confirmed_items=[{
            "id": "confirmed-1", "statement": statement, "source_quote": quote,
            "confirmed_for_writing": True,
        }])

    def test_same_explicit_metric_entity_and_year_with_different_value_is_hard_conflict(self):
        result = self._check(
            "Acme annual revenue in 2023 was 12 million yuan.",
            "Acme annual revenue in 2023 was 12 million yuan.",
            "Acme annual revenue in 2023 was 15 million yuan.",
        )
        self.assertIn("confirmed_number_conflict", [item["code"] for item in result["hard_conflicts"]])

    def test_same_enterprise_but_different_metrics_are_not_a_hard_conflict(self):
        result = self._check(
            "Acme annual revenue in 2023 was 12 million yuan.",
            "Acme annual revenue in 2023 was 12 million yuan.",
            "Acme employed 15 people in 2023.",
        )
        self.assertNotIn("confirmed_number_conflict", [item["code"] for item in result["hard_conflicts"]])

    def test_same_metric_in_different_years_is_not_a_hard_conflict(self):
        result = self._check(
            "Acme annual revenue in 2022 was 12 million yuan.",
            "Acme annual revenue in 2022 was 12 million yuan.",
            "Acme annual revenue in 2023 was 15 million yuan.",
        )
        self.assertNotIn("confirmed_number_conflict", [item["code"] for item in result["hard_conflicts"]])

    def test_same_metric_and_year_but_different_geographic_scope_is_not_hard_conflict(self):
        result = self._check(
            "Acme annual revenue in China in 2023 was 12 million yuan.",
            "Acme annual revenue in China in 2023 was 12 million yuan.",
            "Acme annual revenue in the US in 2023 was 15 million yuan.",
        )
        self.assertNotIn("confirmed_number_conflict", [item["code"] for item in result["hard_conflicts"]])

    def test_ambiguous_number_without_comparable_metric_and_scope_is_not_hard_conflict(self):
        result = self._check("Acme reached 12 in the evaluation.", "Acme reached 12 in the evaluation.", "Acme reached 15 in the evaluation.")
        self.assertNotIn("confirmed_number_conflict", [item["code"] for item in result["hard_conflicts"]])

    def test_confirmed_metric_is_ignored_when_candidate_does_not_discuss_it(self):
        result = self._check(
            "Acme annual revenue in 2023 was 12 million yuan.",
            "Acme annual revenue in 2023 was 12 million yuan.",
            "Acme hired 15 researchers in 2023.",
        )
        self.assertNotIn("confirmed_number_conflict", [item["code"] for item in result["hard_conflicts"]])


class NarrativeSummaryContextRegressionTests(unittest.TestCase):
    def test_summary_is_added_as_lower_priority_candidate_alongside_atomic_memory(self):
        with tempfile.TemporaryDirectory() as data_dir:
            initialize_memory_store(data_dir)
            body = "The construct appears in this synthetic source passage."
            _save_memory(data_dir, "written", body, "NARRATIVE_SUMMARY_LOW_PRIORITY", statement="The construct remains stable.", quote="construct")
            context = build_context_sandwich_v2(
                "current", [{"id": "written", "title": "Background", "desc": "construct"}, {"id": "current", "title": "Methods", "desc": "construct"}],
                {"written": body}, data_dir,
            )
        entries = context["_memory_entries"]
        atom = next(item for item in entries if item["memory_id"] is not None)
        summary = next(item for item in entries if item.get("is_summary"))
        self.assertLess(summary["priority"], atom["priority"])
        self.assertIn("NARRATIVE_SUMMARY_LOW_PRIORITY", context["upstream"])
        self.assertIn("The construct remains stable", context["upstream"])

    def test_budget_can_drop_summary_before_atomic_memory_and_records_omission(self):
        with tempfile.TemporaryDirectory() as data_dir:
            initialize_memory_store(data_dir)
            body = "The construct appears in this synthetic source passage."
            _save_memory(data_dir, "written", body, "Narrative " * 180, statement="Construct is stable.", quote="construct", confirmed=True)
            context = build_context_sandwich_v2(
                "current", [{"id": "written", "title": "Background", "desc": "construct"}, {"id": "current", "title": "Methods", "desc": "construct"}],
                {"written": body}, data_dir,
            )
            fitted = fit_context_sandwich_to_prompt(
                context, lambda value: f"{value['current_title']}\n{value['upstream']}\n{value['downstream']}", max_estimated_tokens=90,
            )
        manifest = fitted["context_manifest"]
        self.assertIn("The construct remains stable" if "The construct remains stable" in body else "Construct is stable", fitted["upstream"])
        self.assertNotIn("Narrative", fitted["upstream"])
        self.assertEqual(manifest["omission_reasons"].get("written:summary"), "prompt_budget")
        self.assertEqual(len(manifest["selected_memory_ids"]), 1)


if __name__ == "__main__":
    unittest.main()
