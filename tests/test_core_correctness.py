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

    def test_readme_marks_v101_unreleased_and_keeps_v100_history(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("v1.0.1 — Unreleased", readme)
        self.assertIn("v1.0.0", readme)
        self.assertIn("Changelog", readme)


if __name__ == "__main__":
    unittest.main()
