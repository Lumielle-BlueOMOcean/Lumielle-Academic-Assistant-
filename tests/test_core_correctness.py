import importlib.util
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_project_module(test_case, name):
    path = ROOT / f"{name}.py"
    if not path.is_file():
        test_case.fail(f"Expected project module {path.name} to exist")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        test_case.fail(f"Could not load project module {path.name}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        test_case.fail(f"Could not import {path.name}: {exc}")
    return module


class CoreCorrectnessTests(unittest.TestCase):
    def test_english_defaults_are_nested_in_prompts_file(self):
        support = load_project_module(self, "writing_support")
        defaults = {
            "prompts.json": {
                "global_topic": "keep this value",
                "aigc_detect_engine": "llm",
            }
        }

        support.apply_english_prompt_defaults(defaults)

        prompts = defaults["prompts.json"]
        self.assertIn("academic paper writing engine", prompts["style_prompt"].lower())
        self.assertIn("page setup", prompts["format_prompt"].lower())
        self.assertIn("rewrite the following academic text", prompts["aigc_rewrite_prompt"].lower())
        self.assertIn("aigc text detection expert", prompts["aigc_detect_prompt"].lower())
        self.assertEqual(prompts["aigc_detect_engine"], "llm")
        self.assertEqual(prompts["global_topic"], "keep this value")
        self.assertEqual(set(defaults), {"prompts.json"})

        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("apply_english_prompt_defaults(default_files)", app_source)
        self.assertIsNone(re.search(r'default_files\["(?:style_prompt|format_prompt|aigc_rewrite_prompt|aigc_detect_prompt)"\]', app_source))

    def test_context_layers_are_in_chapter_prompts_in_both_locales(self):
        support = load_project_module(self, "writing_support")
        sandwich = {
            "global_topic": "TOPIC_SENTINEL",
            "target_word_count": 900,
            "global_outline": "GLOBAL_SENTINEL",
            "current_title": "CURRENT_SENTINEL",
            "current_desc": "DESC_SENTINEL",
            "current_word_count": 300,
            "current_refs": [],
            "upstream": "UPSTREAM_SENTINEL",
            "downstream": "DOWNSTREAM_SENTINEL",
        }

        for locale in ("en", "zh"):
            with self.subTest(locale=locale):
                prompt = support.build_chapter_prompt(
                    sandwich,
                    {"style_prompt": "STYLE_SENTINEL"},
                    [],
                    locale=locale,
                )
                for sentinel in (
                    "GLOBAL_SENTINEL",
                    "UPSTREAM_SENTINEL",
                    "CURRENT_SENTINEL",
                    "DOWNSTREAM_SENTINEL",
                ):
                    self.assertIn(sentinel, prompt)

    def test_custom_style_prompt_is_interpolated(self):
        support = load_project_module(self, "writing_support")
        sandwich = {
            "global_topic": "Topic",
            "target_word_count": 100,
            "global_outline": "Outline",
            "current_title": "Chapter",
            "current_desc": "Description",
            "current_word_count": 100,
            "current_refs": [],
            "upstream": "None",
            "downstream": "None",
        }

        for locale in ("en", "zh"):
            with self.subTest(locale=locale):
                prompt = support.build_chapter_prompt(
                    sandwich,
                    {"style_prompt": "STYLE_SENTINEL_123"},
                    [],
                    locale=locale,
                )
                self.assertIn("STYLE_SENTINEL_123", prompt)
                self.assertNotIn("{style_prompt}", prompt)

    def test_english_word_counter_counts_words_and_compound_tokens(self):
        support = load_project_module(self, "writing_support")

        self.assertEqual(
            support.count_english_words("Artificial intelligence changes legal research."),
            5,
        )
        self.assertEqual(
            support.count_english_words("OpenAI's state-of-the-art model scored 3.14 in 2024."),
            7,
        )
        self.assertEqual(support.count_english_words("... — !"), 0)

    def test_chinese_character_counter_keeps_non_whitespace_behavior(self):
        support = load_project_module(self, "writing_support")
        self.assertEqual(support.count_chinese_chars("人工 智能，AI"), 7)

    def test_version_is_canonical_v101(self):
        version = load_project_module(self, "version")
        self.assertEqual(version.__version__, "1.0.1")

    def test_readme_matches_canonical_unreleased_version_and_keeps_v100_history(self):
        version = load_project_module(self, "version")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"v{version.__version__}", readme)
        self.assertRegex(
            readme,
            rf"(?m)^### v{re.escape(version.__version__)} — Unreleased$",
        )
        self.assertIn("v1.0.0", readme)
        self.assertIn("Changelog", readme)

    def test_global_reference_registry_uses_snapshots_and_deduplicates(self):
        support = load_project_module(self, "writing_support")
        tree = [
            {"id": "chapter-a", "references": ["A", "B"], "children": []},
            # Current binding changed after this draft's snapshot was established.
            {"id": "chapter-b", "references": ["A"], "children": []},
        ]
        drafts = {"chapter-a": "A text", "chapter-b": "B text"}
        snapshots = {"chapter-b": ["B", "C"]}
        literatures = [{"id": rid, "title": rid} for rid in ("A", "B", "C")]

        ordered, number_by_id, literature_by_id = support.collect_global_reference_registry(
            tree, drafts, snapshots, literatures
        )

        self.assertEqual(ordered, ["A", "B", "C"])
        self.assertEqual(number_by_id, {"A": 1, "B": 2, "C": 3})
        self.assertEqual(set(literature_by_id), {"A", "B", "C"})

    def test_citations_are_remapped_from_local_to_global_numbers(self):
        support = load_project_module(self, "writing_support")
        # Chapter A binds [A, B], while chapter B binds only [B].
        tree = [
            {"id": "chapter-a", "references": ["A", "B"], "children": []},
            {"id": "chapter-b", "references": ["B"], "children": []},
        ]
        drafts = {"chapter-a": "First", "chapter-b": "Evidence [1]."}
        ordered, number_by_id, _ = support.collect_global_reference_registry(
            tree, drafts, {}, [{"id": "A"}, {"id": "B"}]
        )
        self.assertEqual(ordered, ["A", "B"])
        self.assertEqual(
            support.remap_local_citations(drafts["chapter-b"], ["B"], number_by_id),
            "Evidence [2].",
        )

    def test_multiple_citations_and_unknown_numbers_are_preserved_correctly(self):
        support = load_project_module(self, "writing_support")
        self.assertEqual(
            support.remap_local_citations(
                "Evidence [1][2]. Unknown [99] and year [2024].",
                ["B", "C"],
                {"A": 1, "B": 2, "C": 3},
            ),
            "Evidence [2][3]. Unknown [99] and year [2024].",
        )

    def test_citation_remapping_preserves_markdown_links_and_plain_brackets(self):
        support = load_project_module(self, "writing_support")
        source = "See [1](https://example.test) and [1][source] and [Author, 2024]; cite [1] (as shown).\n[1]: https://example.test/ref"
        self.assertEqual(
            support.remap_local_citations(source, ["B"], {"B": 2}),
            "See [1](https://example.test) and [1][source] and [Author, 2024]; cite [2] (as shown).\n[1]: https://example.test/ref",
        )

    def test_legacy_reference_registry_falls_back_to_tree_bindings(self):
        support = load_project_module(self, "writing_support")
        tree = [{"id": "legacy", "references": ["B", "A", "B"], "children": []}]
        ordered, numbers, _ = support.collect_global_reference_registry(
            tree,
            {"legacy": "Legacy draft [1]."},
            {},
            [{"id": "A"}, {"id": "B"}],
        )
        self.assertEqual(ordered, ["B", "A"])
        self.assertEqual(numbers, {"B": 1, "A": 2})

    def test_llm_failures_are_rejected_but_normal_prose_is_accepted(self):
        support = load_project_module(self, "writing_support")
        for failure in (
            None,
            "",
            "   \n\t",
            "API key missing. Add a key.",
            "API call failed repeatedly: timeout",
            "OpenAI client initialization error: bad config",
            "No LLM configured. Please add one.",
            "No valid LLM configuration found.",
        ):
            with self.subTest(failure=failure):
                self.assertTrue(support.get_llm_failure_reason(failure))
                with self.assertRaises(support.LLMOutputError):
                    support.require_valid_llm_output(failure)

        prose = "The study compares API access policies across three research groups."
        self.assertIsNone(support.get_llm_failure_reason(prose))
        self.assertEqual(support.require_valid_llm_output(prose), prose)
        quoted_error = "The paper analyzes the interface message 'API key missing' as an example."
        self.assertIsNone(support.get_llm_failure_reason(quoted_error))

    def test_failed_generation_does_not_change_draft_or_reference_snapshot(self):
        support = load_project_module(self, "writing_support")
        drafts = {"chapter": "Old body"}
        snapshots = {"chapter": ["old-reference"]}
        with self.assertRaises(support.LLMOutputError):
            support.record_generated_draft(drafts, snapshots, "chapter", "", ["new-reference"])
        self.assertEqual(drafts, {"chapter": "Old body"})
        self.assertEqual(snapshots, {"chapter": ["old-reference"]})


if __name__ == "__main__":
    unittest.main()
