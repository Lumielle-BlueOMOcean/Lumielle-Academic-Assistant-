import json
import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

import literature_intelligence
import research_support
from literature_intelligence import (
    analyze_literature_document,
    apply_analysis_to_literature_record,
)
from literature_ui_support import render_legacy_reanalysis
from research_support import (
    accept_smart_inbox_facts,
    create_default_research_base,
    parse_research_source,
    select_research_grounding_for_chapter,
)
import research_ui_support
from research_ui_support import _TEXT


ROOT = Path(__file__).resolve().parents[1]


class ResearchGroundingCascadeTests(unittest.TestCase):
    @staticmethod
    def section(enabled=False):
        return {
            "id": "methods",
            "allow_writing_grounding": enabled,
            "modules": [
                {"id": "fact-a", "title": "Sampling method", "content": "A", "allow_writing_grounding": enabled},
                {"id": "fact-b", "title": "Survey method", "content": "B", "allow_writing_grounding": enabled},
            ],
        }

    def test_parent_toggle_cascades_both_directions_and_reenable_resets_exclusions(self):
        section = self.section(False)
        self.assertTrue(callable(getattr(research_support, "apply_section_grounding", None)))

        research_support.apply_section_grounding(section, True)
        self.assertTrue(section["allow_writing_grounding"])
        self.assertEqual([fact["allow_writing_grounding"] for fact in section["modules"]], [True, True])

        section["modules"][1]["allow_writing_grounding"] = False
        research_support.apply_section_grounding(section, False)
        self.assertFalse(section["allow_writing_grounding"])
        self.assertEqual([fact["allow_writing_grounding"] for fact in section["modules"]], [False, False])

        research_support.apply_section_grounding(section, True)
        self.assertEqual([fact["allow_writing_grounding"] for fact in section["modules"]], [True, True])

    def test_individual_fact_exclusion_remains_effective_under_enabled_parent(self):
        base = create_default_research_base()
        section = base["sections"]["methods"]
        section["allow_writing_grounding"] = True
        section["modules"] = [
            {"id": "included", "title": "Sampling methods", "content": "sample A", "tags": ["sampling"], "allow_writing_grounding": True},
            {"id": "excluded", "title": "Sampling methods", "content": "sample B", "tags": ["sampling"], "allow_writing_grounding": False},
        ]

        selected = select_research_grounding_for_chapter(base, "sampling methods")

        self.assertEqual([fact["id"] for fact in selected], ["included"])

    def test_section_default_does_not_gate_an_explicitly_enabled_fact(self):
        base = create_default_research_base()
        base["sections"]["methods"].update({
            "allow_writing_grounding": False,
            "modules": [{"id": "legacy-on", "title": "Sampling", "content": "source", "tags": ["sampling"], "allow_writing_grounding": True}],
        })

        self.assertEqual([fact["id"] for fact in select_research_grounding_for_chapter(base, "sampling")], ["legacy-on"])

    def test_newly_parsed_facts_inherit_parent_state(self):
        for enabled in (False, True):
            with self.subTest(section_enabled=enabled):
                result = parse_research_source(
                    "A source fact.", "methods",
                    lambda *_args, **_kwargs: json.dumps({"facts": [{"title": "Method", "content": "A source fact."}]}),
                    allow_writing_grounding=enabled,
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["facts"][0]["allow_writing_grounding"], enabled)

    def test_smart_inbox_fact_choice_is_not_gated_by_section_default(self):
        pending = {"facts": [{"title": "Reviewed fact", "content": "fact"}]}

        accepted = accept_smart_inbox_facts(
            pending, "methods", [], "", True, section_enabled=False,
        )

        self.assertTrue(accepted[0]["allow_writing_grounding"])

    def test_ui_sync_updates_child_widget_state_for_each_fact(self):
        section = self.section(False)
        session_state = {
            "research_fact_allow_methods_0": False,
            "research_fact_allow_methods_1": False,
        }

        sync = getattr(research_ui_support, "sync_section_grounding_state", None)
        self.assertTrue(callable(sync))
        sync(section, "methods", True, session_state)

        self.assertTrue(section["allow_writing_grounding"])
        self.assertEqual([fact["allow_writing_grounding"] for fact in section["modules"]], [True, True])
        self.assertEqual(session_state, {
            "research_fact_allow_methods_0": True,
            "research_fact_allow_methods_1": True,
        })

    def test_bilingual_section_bulk_controls_keep_children_independently_selectable(self):
        for locale, enable_label, disable_label, count_label in (
            ("en", "Enable all", "Disable all", "Writing grounding: 1 / 2 facts enabled"),
            ("zh", "全部启用", "全部取消", "正文 Grounding：1 / 2 条事实已启用"),
        ):
            with self.subTest(locale=locale), tempfile.TemporaryDirectory() as temp_dir:
                app_path = Path(temp_dir) / "grounding_app.py"
                app_path.write_text(
                    "\n".join((
                        "import sys",
                        f"sys.path.insert(0, {str(ROOT)!r})",
                        "import streamlit as st",
                        "from research_ui_support import _render_section",
                        "if '_working_section' not in st.session_state:",
                        "    st.session_state['_working_section'] = {'id': 'methods', 'name_en': 'Research Methods', 'name_zh': '研究方法', 'allow_writing_grounding': False, 'modules': [",
                        "        {'id': 'a', 'title': 'Fact A', 'type': 'fact', 'content': 'A', 'tags': [], 'allow_writing_grounding': False},",
                        "        {'id': 'b', 'title': 'Fact B', 'type': 'fact', 'content': 'B', 'tags': [], 'allow_writing_grounding': False},",
                        "    ]}",
                        "section = st.session_state['_working_section']",
                        "base = {'schema_version': 2, 'sections': {'methods': section}}",
                        "def save_base(value): st.session_state['_saved_section'] = value['sections']['methods']",
                        f"_render_section(st, base, 'methods', lambda *_a, **_k: '', save_base, lambda *_a: '', {locale!r}, [])",
                        "st.session_state['_working_section'] = section",
                    )),
                    encoding="utf-8",
                )
                app = AppTest.from_file(str(app_path), default_timeout=30).run()
                self.assertFalse(app.exception)
                children = [box for box in app.checkbox if box.key.startswith("research_fact_allow_methods_")]
                self.assertEqual(len(children), 2)
                self.assertTrue(all(not box.disabled and not box.value for box in children))
                self.assertTrue(any(button.label == enable_label for button in app.button))
                self.assertTrue(any(button.label == disable_label for button in app.button))

                children[1].set_value(True)
                app.run()
                self.assertEqual([box.value for box in app.checkbox if box.key.startswith("research_fact_allow_methods_")], [False, True])
                self.assertTrue(any(count_label in item.value for item in app.caption))
                next(button for button in app.button if button.label == enable_label).click()
                app.run()
                children = [box for box in app.checkbox if box.key.startswith("research_fact_allow_methods_")]
                self.assertTrue(all(not box.disabled and box.value for box in children))
                self.assertEqual([fact["allow_writing_grounding"] for fact in app.session_state["_saved_section"]["modules"]], [True, True])

                next(button for button in app.button if button.label == disable_label).click()
                app.run()
                children = [box for box in app.checkbox if box.key.startswith("research_fact_allow_methods_")]
                self.assertTrue(all(not box.disabled and not box.value for box in children))
                self.assertEqual([fact["allow_writing_grounding"] for fact in app.session_state["_saved_section"]["modules"]], [False, False])

                next(field for field in app.text_input if field.key == "research_manual_title_methods").set_value("Manual fact")
                next(field for field in app.text_area if field.key == "research_manual_content_methods").set_value("Manual content")
                next(button for button in app.button if button.key == "research_manual_add_methods").click()
                app.run()
                self.assertFalse(app.session_state["_saved_section"]["modules"][-1]["allow_writing_grounding"])

                next(button for button in app.button if button.label == enable_label).click()
                app.run()
                next(field for field in app.text_input if field.key == "research_manual_title_methods").set_value("Enabled default fact")
                next(field for field in app.text_area if field.key == "research_manual_content_methods").set_value("Enabled default content")
                next(button for button in app.button if button.key == "research_manual_add_methods").click()
                app.run()
                self.assertTrue(app.session_state["_saved_section"]["modules"][-1]["allow_writing_grounding"])

    def test_smart_inbox_ui_can_enable_fact_when_section_default_is_off(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app_path = Path(temp_dir) / "inbox_app.py"
            app_path.write_text(
                "\n".join((
                    "import sys",
                    f"sys.path.insert(0, {str(ROOT)!r})",
                    "import streamlit as st",
                    "from research_support import create_default_research_base",
                    "from research_ui_support import _render_inbox",
                    "if '_base' not in st.session_state:",
                    "    st.session_state['_base'] = create_default_research_base()",
                    "    st.session_state['_base']['sections']['methods']['allow_writing_grounding'] = False",
                    "    st.session_state['research_inbox_pending'] = {'source': {'title': 'Reviewed source'}, 'facts': [{'title': 'Fact', 'content': 'Evidence'}], 'suggestion': {'section': 'methods'}}",
                    "base = st.session_state['_base']",
                    "def save_base(value): st.session_state['_saved_base'] = value",
                    "_render_inbox(st, base, lambda *_a, **_k: '', save_base, lambda *_a: '', 'en')",
                    "st.session_state['_base'] = base",
                )),
                encoding="utf-8",
            )
            app = AppTest.from_file(str(app_path), default_timeout=30).run()
            self.assertFalse(app.exception)
            next(box for box in app.checkbox if box.key == "research_inbox_grounding").set_value(True)
            app.run()
            next(button for button in app.button if button.key == "research_inbox_accept").click()
            app.run()

            facts = app.session_state["_saved_base"]["sections"]["methods"]["modules"]
            self.assertEqual(len(facts), 1)
            self.assertTrue(facts[0]["allow_writing_grounding"])

    def test_grounding_copy_explains_parent_child_semantics_in_both_locales(self):
        self.assertIn("independently selectable", _TEXT["en"]["grounding_help"].lower())
        self.assertIn("事实", _TEXT["zh"]["grounding_help"])
        self.assertIn("grounding_status", _TEXT["en"])
        self.assertIn("grounding_status", _TEXT["zh"])


class LiteraturePartialSchemaTests(unittest.TestCase):
    def test_missing_and_null_chunk_fields_normalize_to_complete_canonical_shape(self):
        normalize = getattr(literature_intelligence, "normalize_chunk_extraction", None)
        self.assertTrue(callable(normalize))
        normalized = normalize({
            "research_question": None,
            "method": "Interview",
            "claims": None,
        })

        self.assertEqual(normalized, {
            "research_question": "", "theory": "", "method": "Interview", "sample": "",
            "data": "", "results": "", "claims": [], "limitations": "", "conclusion": "",
            "definitions": "",
        })

    def test_partial_academic_chunk_is_accepted_without_retry(self):
        calls = []

        def llm(prompt, **_kwargs):
            calls.append(prompt)
            if "[DOCUMENT SYNTHESIS]" in prompt:
                return json.dumps({"rating": "4", "summary": "Survey findings"})
            return json.dumps({"method": "Survey", "sample": "426 respondents", "results": "Significant relationship"})

        result = analyze_literature_document("Survey source", "Study", llm, max_chars=200)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["profile"]["rating"], 4)
        self.assertEqual(result["profile"]["category"], "Other")
        self.assertEqual(result["profile"]["methods"], "")
        self.assertEqual(sum("[SOURCE CHUNK " in prompt for prompt in calls), 1)
        self.assertEqual(sum("[DOCUMENT SYNTHESIS]" in prompt for prompt in calls), 1)

    def test_text_lists_and_nested_objects_are_deterministically_flattened(self):
        normalize = getattr(literature_intelligence, "normalize_chunk_extraction", None)
        self.assertTrue(callable(normalize))
        normalized = normalize({"method": ["Survey", "Interview", 2, {"nested": "value"}], "results": None})
        self.assertEqual(normalized["method"], "Survey; Interview; 2; nested: value")
        self.assertEqual(normalize({"method": {"nested": "value"}})["method"], "nested: value")
        self.assertEqual(literature_intelligence._validate_chunk_extraction({"method": ["Survey", {"nested": "value"}]})["method"], "Survey; nested: value")

    def test_partial_claim_can_synthesize_without_creating_evidence(self):
        calls = []

        def llm(prompt, **_kwargs):
            calls.append(prompt)
            if "[DOCUMENT SYNTHESIS]" in prompt:
                self.assertIn("The intervention improved outcomes", prompt)
                return json.dumps({"rating": 3, "category": "Intervention", "summary": "A profile"})
            return json.dumps({"claims": [{"claim": "The intervention improved outcomes"}]})

        result = analyze_literature_document("The source describes an intervention.", "Study", llm, max_chars=200)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["evidence"], [])

    def test_claim_string_is_normalized_to_one_claim_without_evidence(self):
        def llm(prompt, **_kwargs):
            if "[DOCUMENT SYNTHESIS]" in prompt:
                self.assertIn("plain string claim", prompt)
                return json.dumps({"rating": 3, "summary": "A supported profile"})
            return json.dumps({"method": "Survey", "claims": "plain string claim"})

        result = analyze_literature_document("Survey source", "Study", llm, max_chars=200)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["evidence"], [])

    def test_only_exact_quotes_in_original_chunk_create_evidence(self):
        evidence_text = "EXACT SOURCE QUOTATION"

        def llm(prompt, **_kwargs):
            if "[DOCUMENT SYNTHESIS]" in prompt:
                return json.dumps({"rating": 4, "summary": "Valid synthesis"})
            quote = evidence_text if evidence_text in prompt else "PARAPHRASED QUOTE"
            return json.dumps({"results": "A result", "claims": [
                {"claim": "Supported claim", "evidence_text": quote},
                {"claim": "Unsupported claim", "evidence_text": "HALLUCINATED QUOTE"},
            ]})

        result = analyze_literature_document(
            f"Study source reports {evidence_text} in the results.", "Study", llm, max_chars=500,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual([item["evidence_text"] for item in result["evidence"]], [evidence_text])
        self.assertEqual(result["evidence"][0]["source_locator"], "chunk 1")
        self.assertEqual(result["evidence"][0]["literature_id"], "")

    def test_empty_or_irrelevant_chunk_structures_fail_closed(self):
        for response, stage in (
            ({}, "empty_structured_output"),
            ({"claims": []}, "schema_validation_failure"),
            ({"research_question": "", "method": "", "claims": []}, "schema_validation_failure"),
            ({"status": "ok"}, "schema_validation_failure"),
        ):
            with self.subTest(response=response):
                result = analyze_literature_document(
                    "A source", "Study", lambda *_args, _response=response, **_kwargs: json.dumps(_response), max_chars=200,
                )
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["failure_stage"], stage)
                self.assertEqual(result["evidence"], [])

    def test_profile_normalization_allows_omitted_text_and_category_with_digit_rating(self):
        normalize = getattr(literature_intelligence, "normalize_profile", None)
        self.assertTrue(callable(normalize))
        profile = normalize({"rating": "4", "summary": "Synthesized"})

        self.assertEqual(profile["rating"], 4)
        self.assertEqual(profile["category"], "Other")
        self.assertEqual(profile["methods"], "")
        self.assertEqual(profile["summary"], "Synthesized")

    def test_invalid_profile_rating_or_no_academic_content_is_rejected(self):
        normalize = getattr(literature_intelligence, "normalize_profile", None)
        self.assertTrue(callable(normalize))
        for rating in (True, 0, 6, "4/5", 4.0, None):
            with self.subTest(rating=rating):
                self.assertIsNone(normalize({"rating": rating, "summary": "Content"}))
        self.assertIsNone(normalize({"rating": 4, "category": "Other"}))

    def test_realistic_three_chunk_deepseek_partial_outputs_synthesize_with_exact_provenance(self):
        chunks = [
            "CHUNK1_SENTINEL".ljust(120, "A"),
            "CHUNK2_SENTINEL".ljust(120, "B"),
            "CHUNK3_SENTINEL EXACT_QUOTE_SENTINEL".ljust(120, "C"),
        ]
        source = "".join(chunks)
        prompts = []

        def llm(prompt, **_kwargs):
            prompts.append(prompt)
            if "[DOCUMENT SYNTHESIS]" in prompt:
                self.assertLess(prompt.index("RQ_CHUNK1"), prompt.index("RESULT_CHUNK2"))
                self.assertLess(prompt.index("RESULT_CHUNK2"), prompt.index("LIMIT_CHUNK3"))
                return json.dumps({"rating": "4", "summary": "Combined study profile"})
            if "[SOURCE CHUNK 1/3]" in prompt:
                return json.dumps({"research_question": "RQ_CHUNK1", "method": "Survey", "claims": []})
            if "[SOURCE CHUNK 2/3]" in prompt:
                return json.dumps({"sample": "426 respondents", "data": "Dataset", "results": "RESULT_CHUNK2"})
            if "[SOURCE CHUNK 3/3]" in prompt:
                return json.dumps({"limitations": "LIMIT_CHUNK3", "conclusion": "Conclusion", "claims": [
                    {"claim": "Supported final claim", "evidence_text": "EXACT_QUOTE_SENTINEL"},
                    {"claim": "Paraphrased claim", "evidence_text": "NOT_IN_SOURCE"},
                ]})
            self.fail("Each ordered source chunk should be called once; the successful partial schemas must not retry.")

        result = analyze_literature_document(source, "DeepSeek fixture", llm, max_chars=120)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["chunks_total"], 3)
        self.assertEqual(sum("[SOURCE CHUNK " in prompt for prompt in prompts), 3)
        self.assertEqual(len(result["evidence"]), 1)
        self.assertEqual(result["evidence"][0]["evidence_text"], "EXACT_QUOTE_SENTINEL")
        self.assertEqual(result["profile"]["category"], "Other")

        existing = {"id": "same-lit-id", "title": "DeepSeek fixture", "analysis": {"summary": "old"}}
        updated, evidence_store, success = apply_analysis_to_literature_record(existing, {}, result)
        self.assertTrue(success)
        self.assertEqual(updated["id"], "same-lit-id")
        self.assertEqual(list(evidence_store), ["same-lit-id"])
        self.assertEqual(evidence_store["same-lit-id"][0]["literature_id"], "same-lit-id")

    def test_failed_saved_literature_can_be_reanalyzed_in_place_without_duplicate_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.txt"
            source_path.write_text("Original full source EXACT_REANALYSIS_QUOTE", encoding="utf-8")
            app_path = Path(temp_dir) / "reanalysis_app.py"
            app_path.write_text(
                "\n".join((
                    "import sys",
                    f"sys.path.insert(0, {str(ROOT)!r})",
                    "import json",
                    "import streamlit as st",
                    "from literature_ui_support import render_legacy_reanalysis",
                    "if '_record' not in st.session_state:",
                    f"    st.session_state['_record'] = {{'id': 'failed-lit', 'title': 'Failed saved source', 'file_path': {str(source_path)!r}, 'rating': 0, 'category': 'Unrated', 'analysis_status': 'unavailable', 'analysis': {{'analysis_status': 'unavailable'}}}}",
                    "record = st.session_state['_record']",
                    "literatures = [record]",
                    "evidence_store = st.session_state.get('_evidence', {'failed-lit': [{'literature_id': 'failed-lit', 'evidence_text': 'KEEP_OLD_EVIDENCE'}]})",
                    "def llm(prompt, **_kwargs):",
                    "    if '[DOCUMENT SYNTHESIS]' in prompt: return json.dumps({'rating': '4', 'summary': 'Recovered profile'})",
                    "    return json.dumps({'results': 'Recovered result', 'claims': [{'claim': 'Supported claim', 'evidence_text': 'EXACT_REANALYSIS_QUOTE'}]})",
                    "def save_records(value): st.session_state['_records'] = value",
                    "def save_evidence(value): st.session_state['_evidence'] = value",
                    "render_legacy_reanalysis(st, record, literatures, evidence_store, '', lambda raw, _name: raw.decode('utf-8'), llm, save_records, save_evidence, 'en')",
                    "st.session_state['_record'] = record",
                )),
                encoding="utf-8",
            )
            app = AppTest.from_file(str(app_path), default_timeout=30).run()
            self.assertFalse(app.exception)
            next(button for button in app.button if button.key == "literature_reanalyze_failed-lit").click()
            app.run()

            records = app.session_state["_records"]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["id"], "failed-lit")
            self.assertEqual(records[0]["analysis"]["summary"], "Recovered profile")
            self.assertEqual(app.session_state["_evidence"]["failed-lit"][0]["evidence_text"], "EXACT_REANALYSIS_QUOTE")


if __name__ == "__main__":
    unittest.main()
