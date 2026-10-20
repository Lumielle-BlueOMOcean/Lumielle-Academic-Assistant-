"""RC3 acceptance regressions using synthetic application data only."""

import unittest
import json
import shutil
import tempfile
from pathlib import Path

from chapter_consistency import review_semantic_consistency
import chapter_memory_store
from literature_support import delete_literature_record


def _passing_review(*_args, **_kwargs):
    return {
        "content": '{"status":"pass","issues":[]}',
        "finish_reason": "stop",
        "error_code": None,
    }


class SemanticReviewGroundingAcceptanceTests(unittest.TestCase):
    def test_bilingual_review_prompt_uses_final_generation_grounding(self):
        context = {
            "current_title": "CURRENT_TITLE_SENTINEL",
            "current_desc": "CURRENT_DESC_SENTINEL",
            "research_constraints": "CONFIRMED_CONSTRAINT_SENTINEL",
            "research_facts": "SELECTED_RESEARCH_FACT_SENTINEL",
            "literature_evidence": "LIT_EVIDENCE_SENTINEL source_id=LIT_ID_SENTINEL",
            "upstream": "SELECTED_MEMORY_SENTINEL",
            "downstream": "DOWNSTREAM_BOUNDARY_SENTINEL",
            "_base_upstream": "OMITTED_OPTIONAL_MEMORY_SENTINEL",
        }

        for locale in ("en", "zh"):
            with self.subTest(locale=locale):
                calls = []

                def fake_llm(prompt, **kwargs):
                    calls.append((prompt, kwargs))
                    return _passing_review()

                result = review_semantic_consistency(
                    "CANDIDATE_BODY_SENTINEL", context, fake_llm, locale=locale
                )

                self.assertTrue(result["completed"])
                self.assertEqual(len(calls), 1)
                prompt, options = calls[0]
                self.assertTrue(options["json_mode"])
                self.assertTrue(options["return_metadata"])
                for sentinel in (
                    "CURRENT_TITLE_SENTINEL",
                    "CURRENT_DESC_SENTINEL",
                    "CONFIRMED_CONSTRAINT_SENTINEL",
                    "SELECTED_RESEARCH_FACT_SENTINEL",
                    "LIT_EVIDENCE_SENTINEL",
                    "LIT_ID_SENTINEL",
                    "SELECTED_MEMORY_SENTINEL",
                    "DOWNSTREAM_BOUNDARY_SENTINEL",
                    "CANDIDATE_BODY_SENTINEL",
                ):
                    self.assertIn(sentinel, prompt)
                self.assertNotIn("OMITTED_OPTIONAL_MEMORY_SENTINEL", prompt)

    def test_review_prompt_budget_trims_optional_context_and_keeps_constraints(self):
        context = {
            "current_title": "TITLE",
            "current_desc": "DESC",
            "research_constraints": "CONFIRMED_CONSTRAINT_SENTINEL",
            "research_facts": "FACT_SENTINEL " * 1800,
            "literature_evidence": "EVIDENCE_SENTINEL " * 1800,
            "upstream": "UPSTREAM_SENTINEL " * 1800,
            "downstream": "DOWNSTREAM_SENTINEL " * 1800,
        }
        calls = []

        result = review_semantic_consistency(
            "CANDIDATE_SENTINEL",
            context,
            lambda prompt, **kwargs: (calls.append(prompt) or _passing_review()),
            locale="en",
        )

        self.assertTrue(result["completed"])
        self.assertEqual(len(calls), 1)
        self.assertLessEqual(len(calls[0]), 48_000)
        self.assertIn("CONFIRMED_CONSTRAINT_SENTINEL", calls[0])
        self.assertIn("CANDIDATE_SENTINEL", calls[0])
        self.assertIn("FACT_SENTINEL", calls[0])
        self.assertTrue(any(marker in calls[0] for marker in ("[TRUNCATED", "（已截断", "[已截断")))

    def test_oversized_mandatory_review_context_fails_closed_without_call(self):
        calls = []
        context = {
            "research_constraints": "CONFIRMED_CONSTRAINT_SENTINEL " * 2_000,
        }

        result = review_semantic_consistency(
            "CANDIDATE_SENTINEL",
            context,
            lambda prompt, **kwargs: calls.append(prompt) or _passing_review(),
            locale="en",
        )

        self.assertFalse(result["completed"])
        self.assertEqual(result["failure_code"], "review_context_too_large")
        self.assertEqual(calls, [])


class LiteratureDeleteAcceptanceTests(unittest.TestCase):
    def _fixture(self, file_name="source.pdf"):
        temporary = tempfile.TemporaryDirectory()
        raw_dir = Path(temporary.name) / "raw_files"
        raw_dir.mkdir()
        source = raw_dir / file_name
        source.write_bytes(b"synthetic source")
        records = [{"id": "lit-1", "title": "Synthetic", "file_path": str(source)}]
        evidence = {"lit-1": [{"claim": "synthetic"}]}
        return temporary, raw_dir, source, records, evidence

    def test_cited_literature_is_not_deleted(self):
        temporary, raw_dir, source, records, evidence = self._fixture()
        self.addCleanup(temporary.cleanup)
        calls = []
        result = delete_literature_record(
            records, evidence, "lit-1", raw_dir,
            lambda value: calls.append(("records", value)) or True,
            lambda value: calls.append(("evidence", value)) or True,
            cited=True,
        )
        self.assertEqual(result["status"], "cited")
        self.assertTrue(source.exists())
        self.assertEqual(calls, [])

    def test_record_save_failure_preserves_original_and_metadata(self):
        temporary, raw_dir, source, records, evidence = self._fixture()
        self.addCleanup(temporary.cleanup)
        result = delete_literature_record(
            records, evidence, "lit-1", raw_dir,
            lambda value: False, lambda value: self.fail("evidence must not be saved"),
        )
        self.assertEqual(result["status"], "save_failed")
        self.assertTrue(source.exists())

    def test_evidence_save_failure_rolls_back_record_and_preserves_source(self):
        temporary, raw_dir, source, records, evidence = self._fixture()
        self.addCleanup(temporary.cleanup)
        record_writes = []
        evidence_writes = []
        result = delete_literature_record(
            records, evidence, "lit-1", raw_dir,
            lambda value: record_writes.append(value) or True,
            lambda value: evidence_writes.append(value) or (len(evidence_writes) > 1),
        )
        self.assertEqual(result["status"], "save_failed")
        self.assertFalse(result["rollback_failed"])
        self.assertEqual(record_writes, [[], records])
        self.assertEqual(evidence_writes, [{}, evidence])
        self.assertTrue(source.exists())

    def test_failed_compensation_is_reported_and_source_is_preserved(self):
        temporary, raw_dir, source, records, evidence = self._fixture()
        self.addCleanup(temporary.cleanup)
        record_calls = []

        def save_records(value):
            record_calls.append(value)
            return len(record_calls) == 1

        result = delete_literature_record(
            records, evidence, "lit-1", raw_dir, save_records,
            lambda value: False,
        )
        self.assertEqual(result["status"], "rollback_failed")
        self.assertTrue(result["rollback_failed"])
        self.assertFalse(result["source_deleted"])
        self.assertTrue(source.exists())

    def test_source_cleanup_failure_is_reported_after_metadata_commit(self):
        temporary, raw_dir, source, records, evidence = self._fixture()
        self.addCleanup(temporary.cleanup)
        source.unlink()
        source.mkdir()
        result = delete_literature_record(
            records, evidence, "lit-1", raw_dir,
            lambda value: True, lambda value: True,
        )
        self.assertEqual(result["status"], "cleanup_warning")
        self.assertTrue(result["records_saved"] and result["evidence_saved"])
        self.assertTrue(result["cleanup_failed"])

    def test_success_persists_both_stores_before_source_delete(self):
        temporary, raw_dir, source, records, evidence = self._fixture()
        self.addCleanup(temporary.cleanup)
        writes = []

        def save_records(value):
            writes.append(("records", value, source.exists()))
            return True

        def save_evidence(value):
            writes.append(("evidence", value, source.exists()))
            return True

        result = delete_literature_record(
            records, evidence, "lit-1", raw_dir, save_records, save_evidence,
        )
        self.assertEqual(result["status"], "deleted")
        self.assertEqual([entry[0] for entry in writes], ["records", "evidence"])
        self.assertTrue(all(entry[2] for entry in writes))
        self.assertFalse(source.exists())


class BilingualWritingPageAcceptanceTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def _make_page(self, locale):
        from streamlit.testing.v1 import AppTest

        temporary = tempfile.TemporaryDirectory()
        app_root = Path(temporary.name)
        module_name = "app_rc3_" + app_root.name.replace("-", "_")
        for source in self.ROOT.glob("*.py"):
            if source.name not in {"app.py", "app_zh.py"}:
                shutil.copy2(source, app_root / source.name)
        source_app = self.ROOT / ("app.py" if locale == "en" else "app_zh.py")
        shutil.copy2(source_app, app_root / f"{module_name}.py")
        presets = self.ROOT / "format_presets"
        if presets.exists():
            shutil.copytree(presets, app_root / "format_presets")
        data_dir = app_root / "data"
        data_dir.mkdir()
        tree = [{"id": "root", "title": "Synthetic Thesis", "children": [
            {"id": "chapter-1", "title": "Chapter One", "desc": "shared method synthetic continuity", "word_count": 0, "references": ["lit-1"]},
            {"id": "chapter-2", "title": "Chapter Two", "desc": "shared method follow-up", "word_count": 0, "references": []},
        ]}]
        fixtures = {
            "logic_tree.json": tree,
            "drafts.json": {"chapter-1": "OLD_C1_BODY", "chapter-2": "OLD_C2_BODY"},
            "draft_reference_maps.json": {"chapter-1": ["lit-1"], "chapter-2": []},
            "drafts_summary.json": {},
            "literatures.json": [{"id": "lit-1", "title": "Synthetic Literature", "analysis_status": "ok", "category": "Study", "rating": 4, "analysis": {"summary": "Synthetic reference."}, "file_path": ""}],
            "literature_evidence.json": {"lit-1": [{"claim": "Synthetic source fact", "evidence_text": "Synthetic exact source quote", "source_quote": "Synthetic exact source quote"}]},
            "research_base.json": {"schema_version": 2, "sections": {}},
            "prompts.json": {"global_topic": "Synthetic topic", "target_word_count": 5000, "style_prompt": "Synthetic style."},
        }
        for filename, value in fixtures.items():
            (data_dir / filename).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        script = app_root / "run_app.py"
        script.write_text(
            "import json, sys\n"
            f"sys.path.insert(0, {str(app_root)!r})\n"
            "import streamlit as st\n"
            f"import {module_name} as app\n"
            "if not hasattr(app, '_rc3_original_persist_draft_text'):\n"
            "    app._rc3_original_persist_draft_text = app.persist_draft_text\n"
            "_real_save = app.save_json_file\n"
            "def _controlled_save(name, value):\n"
            "    if name == 'drafts.json' and st.session_state.get('_rc3_fail_draft_once'):\n"
            "        st.session_state['_rc3_fail_draft_once'] = False\n"
            "        st.session_state['_rc3_draft_save_failed'] = True\n"
            "        return False\n"
            "    if name == 'literature_evidence.json' and st.session_state.get('_rc3_fail_literature_evidence_once') and not st.session_state.get('_rc3_literature_evidence_failed'):\n"
            "        st.session_state['_rc3_literature_evidence_failed'] = True\n"
            "        return False\n"
            "    if name == 'draft_reference_maps.json':\n"
            "        if st.session_state.get('_rc3_fail_ref_first') and not st.session_state.get('_rc3_ref_first_failed'):\n"
            "            st.session_state['_rc3_ref_first_failed'] = True\n"
            "            return False\n"
            "        if st.session_state.get('_rc3_fail_ref_rollback') and st.session_state.get('_rc3_draft_save_failed'):\n"
            "            return False\n"
            "    return _real_save(name, value)\n"
            "app.save_json_file = _controlled_save\n"
            "def _tracked_persist(chapter_id, draft_text, locale, **kwargs):\n"
            "    st.session_state['_rc3_memory_chapter'] = str(chapter_id)\n"
            "    st.session_state['_rc3_memory_text'] = draft_text\n"
            "    return app._rc3_original_persist_draft_text(chapter_id, draft_text, locale, **kwargs)\n"
            "app.persist_draft_text = _tracked_persist\n"
            "def _fake_dispatch(prompt, **kwargs):\n"
            "    requests = st.session_state.setdefault('_rc3_llm_requests', [])\n"
            "    is_review = 'review' in str(kwargs.get('system_prompt', '')).lower() or '审阅' in str(kwargs.get('system_prompt', ''))\n"
            "    is_memory = 'memory' in str(kwargs.get('system_prompt', '')).lower() or '记忆提取' in str(kwargs.get('system_prompt', ''))\n"
            "    if is_review:\n"
            "        requests.append({'kind': 'review', 'prompt': prompt})\n"
            "        mode = st.session_state.get('_rc3_review_mode', 'pass')\n"
            "        if mode == 'invalid': return {'content': 'not json', 'finish_reason': 'stop', 'error_code': None}\n"
            "        status = 'needs_review' if mode == 'needs_review' else 'pass'\n"
            "        return {'content': json.dumps({'status': status, 'issues': []}), 'finish_reason': 'stop', 'error_code': None}\n"
            "    if is_memory:\n"
            "        requests.append({'kind': 'memory', 'prompt': prompt})\n"
            "        if st.session_state.get('_rc3_memory_mode') == 'invalid': return {'content': 'not json', 'finish_reason': 'stop', 'error_code': None}\n"
            "        chapter = st.session_state.get('_rc3_memory_chapter', 'chapter-1')\n"
            "        source = str(st.session_state.get('_rc3_memory_text', 'Synthetic memory source.'))\n"
            "        quote = source[:min(36, len(source))]\n"
            "        value = {'chunk_status': 'content', 'summary': f'{chapter} MEMORY_SUMMARY_SENTINEL', 'items': [{'memory_type': 'method', 'statement': f'{chapter} shared method MEMORY_FACT_SENTINEL', 'source_quote': quote}]}\n"
            "        return {'content': json.dumps(value), 'finish_reason': 'stop', 'error_code': None}\n"
            "    modes = st.session_state.get('_rc3_generation_modes', [])\n"
            "    index = int(st.session_state.get('_rc3_generation_index', 0))\n"
            "    st.session_state['_rc3_generation_index'] = index + 1\n"
            "    mode = modes[index] if index < len(modes) else st.session_state.get('_rc3_generation_mode', 'normal')\n"
            "    title = 'Chapter Two' if ('[CURRENT CHAPTER]\\nChapter Two' in prompt or '【当前章节】\\n《Chapter Two》' in prompt) else 'Chapter One'\n"
            "    requests.append({'kind': 'generation', 'prompt': prompt, 'title': title})\n"
            "    if mode == 'length': return {'content': 'TRUNCATED_BODY_SENTINEL', 'finish_reason': 'length', 'error_code': 'truncated_output'}\n"
            "    if mode == 'empty': return {'content': '', 'finish_reason': 'stop', 'error_code': 'empty_content'}\n"
            "    if mode == 'provider': return {'content': '', 'finish_reason': None, 'error_code': 'authentication_failed'}\n"
            "    body = f'NEW_{title.upper().replace(\" \", \"_\")}_BODY_SENTINEL. Synthetic generated chapter has a verified method.'\n"
            "    return {'content': body, 'finish_reason': 'stop', 'error_code': None}\n"
            "app.dispatch_llm_call = _fake_dispatch\n"
            "app.main()\n",
            encoding="utf-8",
        )
        page = AppTest.from_file(str(script), default_timeout=45).run()
        self.assertFalse(page.exception)
        page.radio[0].set_value("5. Chapter Writing" if locale == "en" else "5. 正文写作").run()
        self.assertFalse(page.exception)
        return temporary, app_root, page

    @staticmethod
    def _json(app_root, filename):
        return json.loads((app_root / "data" / filename).read_text(encoding="utf-8"))

    @staticmethod
    def _click(page, key):
        next(button for button in page.button if button.key == key).click().run()

    def test_scenario_1_bilingual_auto_save_rerun_and_manual_save(self):
        for locale in ("en", "zh"):
            with self.subTest(locale=locale):
                temporary, root, page = self._make_page(locale)
                self.addCleanup(temporary.cleanup)
                self._click(page, "navsel_writing_selected_id_chapter-1")
                self._click(page, "gen_chapter-1")
                self.assertFalse(page.exception)
                saved = self._json(root, "drafts.json")
                self.assertIn("NEW_CHAPTER_ONE_BODY_SENTINEL", saved["chapter-1"])
                self.assertEqual(self._json(root, "draft_reference_maps.json")["chapter-1"], ["lit-1"])
                editor = next(item for item in page.text_area if item.key.startswith("draft_chapter-1_v"))
                self.assertIn("NEW_CHAPTER_ONE_BODY_SENTINEL", editor.value)
                self._click(page, "save_chapter-1")
                self.assertFalse(page.exception)
                self.assertIn("NEW_CHAPTER_ONE_BODY_SENTINEL", self._json(root, "drafts.json")["chapter-1"])

    def test_scenario_2_bilingual_candidate_requires_confirmation(self):
        for locale in ("en", "zh"):
            with self.subTest(locale=locale):
                temporary, root, page = self._make_page(locale)
                self.addCleanup(temporary.cleanup)
                page.session_state["_rc3_review_mode"] = "needs_review"
                page.run()
                self._click(page, "gen_chapter-1")
                self.assertFalse(page.exception)
                self.assertEqual(self._json(root, "drafts.json")["chapter-1"], "OLD_C1_BODY")
                self.assertIn("chapter-1", page.session_state["pending_chapter_candidates"])
                self.assertTrue(any(
                    "NEW_CHAPTER_ONE_BODY_SENTINEL" in area.value
                    for area in page.text_area
                    if area.key == "pending_candidate_text_chapter-1"
                ))
                self._click(page, "pending_candidate_save_chapter-1")
                self.assertFalse(page.exception)
                self.assertIn("NEW_CHAPTER_ONE_BODY_SENTINEL", self._json(root, "drafts.json")["chapter-1"])
                editor = next(item for item in page.text_area if item.key.startswith("draft_chapter-1_v"))
                self.assertIn("NEW_CHAPTER_ONE_BODY_SENTINEL", editor.value)
                self._click(page, "save_chapter-1")
                self.assertIn("NEW_CHAPTER_ONE_BODY_SENTINEL", self._json(root, "drafts.json")["chapter-1"])

    def test_scenario_3_bilingual_generation_review_and_save_failures_preserve_old_draft(self):
        failure_modes = ("length", "empty", "provider", "invalid_review", "ref_save", "draft_save", "rollback_failure")
        for locale in ("en", "zh"):
            for mode in failure_modes:
                with self.subTest(locale=locale, mode=mode):
                    temporary, root, page = self._make_page(locale)
                    self.addCleanup(temporary.cleanup)
                    if mode in {"length", "empty", "provider"}:
                        page.session_state["_rc3_generation_mode"] = mode
                    elif mode == "invalid_review":
                        page.session_state["_rc3_review_mode"] = "invalid"
                    elif mode == "ref_save":
                        page.session_state["_rc3_fail_ref_first"] = True
                    elif mode == "draft_save":
                        page.session_state["_rc3_fail_draft_once"] = True
                    else:
                        page.session_state["_rc3_fail_draft_once"] = True
                        page.session_state["_rc3_fail_ref_rollback"] = True
                    page.run()
                    self._click(page, "gen_chapter-1")
                    self.assertFalse(page.exception)
                    self.assertEqual(self._json(root, "drafts.json")["chapter-1"], "OLD_C1_BODY")
                    if mode == "invalid_review":
                        self.assertIn("chapter-1", page.session_state["pending_chapter_candidates"])
                    else:
                        self.assertNotIn("chapter-1", page.session_state.get("pending_chapter_candidates", {}))
                    if mode in {"ref_save", "draft_save", "rollback_failure"}:
                        self.assertEqual(self._json(root, "draft_reference_maps.json")["chapter-1"], ["lit-1"])
                        self.assertEqual(page.session_state.get("content_ver_chapter-1", 0), 0)
                        self.assertFalse(any(row["kind"] == "memory" for row in page.session_state.get("_rc3_llm_requests", [])))
                    if mode == "rollback_failure":
                        rendered = "\n".join(item.value for item in page.error)
                        self.assertIn("citation" if locale == "en" else "引用", rendered.lower())
                        self.assertIn("draft_reference_warning_chapter-1", page.session_state)
                        page.run()
                        self.assertFalse(page.exception)
                        editor_warnings = "\n".join(item.value for item in page.error)
                        self.assertIn("citation" if locale == "en" else "引用", editor_warnings.lower())

    def test_bilingual_literature_delete_page_reports_persistence_and_cleanup_outcomes(self):
        for locale in ("en", "zh"):
            for outcome in ("success", "evidence_failure"):
                with self.subTest(locale=locale, outcome=outcome):
                    temporary, root, page = self._make_page(locale)
                    self.addCleanup(temporary.cleanup)
                    raw_dir = root / "data" / "raw_files"
                    raw_dir.mkdir(exist_ok=True)
                    source = raw_dir / "synthetic-literature.pdf"
                    source.write_bytes(b"synthetic source only")
                    literature = self._json(root, "literatures.json")
                    literature[0]["file_path"] = str(source)
                    (root / "data" / "literatures.json").write_text(json.dumps(literature), encoding="utf-8")
                    references = self._json(root, "draft_reference_maps.json")
                    references["chapter-1"] = []
                    (root / "data" / "draft_reference_maps.json").write_text(json.dumps(references), encoding="utf-8")
                    if outcome == "evidence_failure":
                        page.session_state["_rc3_fail_literature_evidence_once"] = True
                    page.radio[0].set_value("3. Literature Processing" if locale == "en" else "3. 文献处理").run()
                    self.assertFalse(page.exception)
                    self._click(page, "litdel_lit-1")
                    self.assertFalse(page.exception)
                    notice = "\n".join(item.value for item in (*page.success, *page.warning, *page.error))
                    records = self._json(root, "literatures.json")
                    evidence = self._json(root, "literature_evidence.json")
                    if outcome == "success":
                        self.assertFalse(records)
                        self.assertNotIn("lit-1", evidence)
                        self.assertFalse(source.exists())
                        self.assertIn("deleted" if locale == "en" else "已删除", notice)
                    else:
                        self.assertEqual([item["id"] for item in records], ["lit-1"])
                        self.assertIn("lit-1", evidence)
                        self.assertTrue(source.exists())
                        self.assertIn("could not be saved" if locale == "en" else "保存失败", notice)

    def test_scenario_4_bilingual_memory_save_retrieval_and_stale_exclusion(self):
        for locale in ("en", "zh"):
            with self.subTest(locale=locale):
                temporary, root, page = self._make_page(locale)
                self.addCleanup(temporary.cleanup)
                self._click(page, "gen_chapter-1")
                first_body = self._json(root, "drafts.json")["chapter-1"]
                memory = chapter_memory_store.read_chapter_memory(root / "data", "chapter-1", first_body)
                self.assertEqual(memory["status"], "current")
                self.assertTrue(memory["items"])
                self.assertEqual(memory["draft_hash"], chapter_memory_store.draft_hash(first_body))

                page.session_state["writing_selected_id"] = "chapter-2"
                page.run()
                manifest = page.session_state["chapter_context_preview_manifest"]
                self.assertTrue(manifest["selected_memory_ids"])
                self._click(page, "gen_chapter-2")
                requests = page.session_state["_rc3_llm_requests"]
                second_generation = [row for row in requests if row["kind"] == "generation"][-1]
                self.assertIn("MEMORY_FACT_SENTINEL", second_generation["prompt"])

                page.session_state["writing_selected_id"] = "chapter-1"
                page.session_state["_rc3_memory_mode"] = "invalid"
                page.run()
                editor = next(item for item in page.text_area if item.key.startswith("draft_chapter-1_v"))
                editor.set_value("UPDATED_C1_BODY_SENTINEL. Revised synthetic draft.")
                page.run()
                self._click(page, "save_chapter-1")
                updated_body = self._json(root, "drafts.json")["chapter-1"]
                self.assertIn("UPDATED_C1_BODY_SENTINEL", updated_body)
                stale = chapter_memory_store.read_chapter_memory(root / "data", "chapter-1", updated_body)
                self.assertEqual(stale["status"], "stale")

                page.session_state["_rc3_memory_mode"] = "normal"
                page.session_state["writing_selected_id"] = "chapter-2"
                page.run()
                stale_manifest = page.session_state["chapter_context_preview_manifest"]
                self.assertFalse(stale_manifest["selected_memory_ids"])
                self._click(page, "gen_chapter-2")
                requests = page.session_state["_rc3_llm_requests"]
                latest_generation = [row for row in requests if row["kind"] == "generation"][-1]
                self.assertNotIn("MEMORY_FACT_SENTINEL", latest_generation["prompt"])

    def test_scenario_5_bilingual_batch_saves_independently_and_refreshes_editor(self):
        for locale in ("en", "zh"):
            with self.subTest(locale=locale):
                temporary, root, page = self._make_page(locale)
                self.addCleanup(temporary.cleanup)
                page.session_state["bchk_text_chapter-1"] = True
                page.session_state["bchk_text_chapter-2"] = True
                page.session_state["_rc3_generation_modes"] = ["normal", "length"]
                page.run()
                label = "📝 Batch Generate Drafts (chapter by chapter)" if locale == "en" else "📝 批量生成正文（逐章循环）"
                next(button for button in page.button if button.label == label).click().run()
                self.assertFalse(page.exception)
                drafts = self._json(root, "drafts.json")
                self.assertIn("NEW_CHAPTER_ONE_BODY_SENTINEL", drafts["chapter-1"])
                self.assertEqual(drafts["chapter-2"], "OLD_C2_BODY")
                requests = page.session_state["_rc3_llm_requests"]
                chapter_two_generation = [
                    row for row in requests
                    if row["kind"] == "generation" and row["title"] == "Chapter Two"
                ]
                self.assertEqual(len(chapter_two_generation), 1)
                self.assertIn("MEMORY_FACT_SENTINEL", chapter_two_generation[0]["prompt"])
                summary = "\n".join(item.value for item in (*page.warning, *page.error))
                if locale == "en":
                    self.assertIn("1 saved", summary)
                    self.assertIn("1 failed", summary)
                else:
                    self.assertIn("已保存 1 章", summary)
                    self.assertIn("失败 1 章", summary)
                self.assertEqual(self._json(root, "draft_reference_maps.json")["chapter-1"], ["lit-1"])
                self.assertEqual(self._json(root, "draft_reference_maps.json")["chapter-2"], [])
                self.assertEqual(page.session_state.get("content_ver_chapter-1"), 1)
                page.run()
                editor = next(item for item in page.text_area if item.key.startswith("draft_chapter-1_v"))
                self.assertIn("NEW_CHAPTER_ONE_BODY_SENTINEL", editor.value)



if __name__ == "__main__":
    unittest.main()
