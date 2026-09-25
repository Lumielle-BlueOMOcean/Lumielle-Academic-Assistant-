import importlib.util
import json
import re
import struct
import tempfile
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


class ChartSupportTests(unittest.TestCase):
    def setUp(self):
        self.chart = load_project_module(self, "chart_support")

    def load_chart_support(self):
        return self.chart

    def parse(self, spec, instruction="A=10, B=20", **kwargs):
        chart = self.load_chart_support()
        return chart.parse_chart_spec(json.dumps(spec), instruction=instruction, **kwargs)

    def test_valid_grouped_bar_chart_spec_is_accepted(self):
        spec = self.parse({
            "status": "ok",
            "chart_type": "bar",
            "title": "Comparison",
            "x_label": "Category",
            "y_label": "Value",
            "categories": ["A", "B"],
            "series": [
                {"name": "Group 1", "values": [10, 20]},
                {"name": "Group 2", "values": [5, 8]},
            ],
        }, instruction="A=10, B=20; Group 2 has A=5 and B=8")
        self.assertEqual(spec["status"], "ok")
        self.assertEqual(spec["chart_type"], "bar")
        self.assertEqual(len(spec["series"]), 2)

    def test_bar_series_length_must_match_categories(self):
        chart = self.load_chart_support()
        with self.assertRaises(chart.ChartSpecError):
            self.parse({
                "status": "ok", "chart_type": "bar", "title": "T",
                "x_label": "X", "y_label": "Y", "categories": ["A", "B"],
                "series": [{"name": "S", "values": [10]}],
            })

    def test_valid_line_chart_requires_matching_x_and_series_values(self):
        spec = self.parse({
            "status": "ok", "chart_type": "line", "title": "Trend",
            "x_label": "Year", "y_label": "Value", "x": ["2020", "2021"],
            "series": [{"name": "A", "values": [10, 20]}],
        }, instruction="In 2020 value was 10; in 2021 value was 20")
        self.assertEqual(spec["chart_type"], "line")

    def test_line_series_length_mismatch_is_rejected(self):
        chart = self.load_chart_support()
        with self.assertRaises(chart.ChartSpecError):
            self.parse({
                "status": "ok", "chart_type": "line", "title": "T",
                "x_label": "X", "y_label": "Y", "x": ["2020", "2021"],
                "series": [{"name": "S", "values": [10]}],
            }, instruction="2020=10, 2021=20")

    def test_scatter_x_and_y_length_mismatch_is_rejected(self):
        chart = self.load_chart_support()
        with self.assertRaises(chart.ChartSpecError):
            self.parse({
                "status": "ok", "chart_type": "scatter", "title": "T",
                "x_label": "X", "y_label": "Y",
                "series": [{"name": "S", "x": [1, 2], "y": [3]}],
            }, instruction="(1,3), (2,4)")

    def test_pie_rejects_negative_values_and_zero_total(self):
        chart = self.load_chart_support()
        base = {"status": "ok", "chart_type": "pie", "title": "T", "labels": ["A", "B"]}
        for values in ([-1, 2], [0, 0]):
            with self.subTest(values=values), self.assertRaises(chart.ChartSpecError):
                self.parse({**base, "values": values}, instruction="A=-1, B=2")

    def test_non_finite_json_numbers_are_rejected(self):
        chart = self.load_chart_support()
        for token in ("NaN", "Infinity", "-Infinity"):
            raw = (
                '{"status":"ok","chart_type":"bar","title":"T",'
                '"x_label":"X","y_label":"Y","categories":["A"],'
                f'"series":[{{"name":"S","values":[{token}]}}]}}'
            )
            with self.subTest(token=token), self.assertRaises(chart.ChartSpecError):
                chart.parse_chart_spec(raw, instruction="A=10")

    def test_boolean_is_not_accepted_as_a_number(self):
        chart = self.load_chart_support()
        with self.assertRaises(chart.ChartSpecError):
            self.parse({
                "status": "ok", "chart_type": "bar", "title": "T",
                "x_label": "X", "y_label": "Y", "categories": ["A"],
                "series": [{"name": "S", "values": [True]}],
            }, instruction="A=1")

    def test_unsupported_chart_types_and_llm_code_fields_are_rejected(self):
        chart = self.load_chart_support()
        valid = {
            "status": "ok", "chart_type": "python", "title": "T",
            "x_label": "X", "y_label": "Y", "categories": ["A"],
            "series": [{"name": "S", "values": [10]}],
        }
        for unsupported in ("python", [], {}):
            with self.subTest(chart_type=unsupported), self.assertRaises(chart.ChartSpecError):
                self.parse({**valid, "chart_type": unsupported})
        valid["chart_type"] = "bar"
        valid["code"] = "import os; os.remove('data')"
        with self.assertRaises(chart.ChartSpecError):
            self.parse(valid)

    def test_needs_data_is_returned_as_a_non_renderable_result(self):
        chart = self.load_chart_support()
        result = chart.parse_chart_spec(
            '{"status":"needs_data","message":"Please provide values."}',
            instruction="Compare the three groups",
        )
        self.assertEqual(result["status"], "needs_data")

    def test_model_needs_data_result_is_translated_without_rendering(self):
        chart = self.load_chart_support()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "chart.png"
            result = chart.generate_chart_image(
                "Compare A=10 and B=20",
                str(output),
                lambda *args, **kwargs: '{"status":"needs_data","message":"Need another value."}',
            )
            self.assertEqual(result["status"], "needs_data")
            self.assertIn("More data is needed", result["message"])
            self.assertFalse(output.exists())

    def test_invalid_spec_retry_uses_json_mode_and_validation_feedback(self):
        chart = self.load_chart_support()
        responses = iter((
            '{"status":"ok","chart_type":"unsupported"}',
            '{"status":"needs_data","message":"Please provide values."}',
        ))
        calls = []

        def llm_call(prompt, **kwargs):
            calls.append((prompt, kwargs))
            return next(responses)

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "chart.png"
            result = chart.generate_chart_image("Compare A=10 and B=20", str(output), llm_call)
            self.assertEqual(result["status"], "needs_data")
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(call[1]["json_mode"] for call in calls))
            self.assertIn("previous JSON was invalid", calls[1][0])
            self.assertFalse(output.exists())

    def test_factual_values_must_come_from_the_user_instruction(self):
        chart = self.load_chart_support()
        spec = {
            "status": "ok", "chart_type": "bar", "title": "T",
            "x_label": "X", "y_label": "Y", "categories": ["A", "B"],
            "series": [{"name": "S", "values": [10, 99]}],
        }
        with self.assertRaises(chart.ChartSpecError):
            chart.parse_chart_spec(json.dumps(spec), instruction="A=10, B=20")

    def test_line_numeric_axis_labels_must_come_from_the_user_instruction(self):
        chart = self.load_chart_support()
        spec = {
            "status": "ok", "chart_type": "line", "title": "Trend",
            "x_label": "Year", "y_label": "Value", "x": ["1990", "1991"],
            "series": [{"name": "S", "values": [10, 20]}],
        }
        with self.assertRaises(chart.ChartSpecError):
            chart.parse_chart_spec(json.dumps(spec), instruction="2020=10, 2021=20")

    def test_illustrative_request_is_allowed_and_marked(self):
        chart = self.load_chart_support()
        spec = {
            "status": "ok", "chart_type": "bar", "title": "Example",
            "x_label": "X", "y_label": "Y", "categories": ["A", "B"],
            "series": [{"name": "S", "values": [10, 20]}],
        }
        result = chart.parse_chart_spec(
            json.dumps(spec), instruction="Use hypothetical illustrative data for A and B"
        )
        self.assertTrue(result["data_note"])

    def test_chinese_illustrative_permission_requires_explicit_data_authorization(self):
        chart = self.load_chart_support()
        permitted = (
            "请使用示例数据绘图",
            "可以生成一组假设数据",
            "允许使用示意数值",
            "没有真实数据，可以自行生成演示数据",
            "示例数据即可",
            "假设数据即可",
        )
        for instruction in permitted:
            with self.subTest(instruction=instruction):
                self.assertTrue(chart.is_illustrative_request(instruction))

    def test_negative_illustrative_wording_does_not_allow_fabricated_values(self):
        chart = self.load_chart_support()
        for instruction in (
            "不要使用示例数据",
            "不使用示例数据",
            "不采用假设数据",
            "别用示意数据",
            "请勿使用示例数据",
            "禁止使用演示数据",
            "不能生成假设数字",
            "不得采用示例数值",
            "避免使用假设数据",
            "不可生成假设数字",
            "Compare values, but do not use illustrative data",
            "No hypothetical numbers; use only my research data",
            "不要使用示例数据，请使用我提供的数值",
        ):
            with self.subTest(instruction=instruction):
                self.assertFalse(chart.is_illustrative_request(instruction))

    def test_example_and_hypothetical_mentions_without_data_permission_are_not_allowed(self):
        chart = self.load_chart_support()
        mentions = (
            "参考示例图的样式绘制",
            "按照示例格式绘制",
            "这是一个假设场景，但请使用我的真实数据",
            "请仿照示例图布局",
            "示例格式如下",
            "讨论一个假设案例",
            "Follow the example chart's style.",
            "Use the sample figure layout.",
            "This is a hypothetical scenario, but use my real data.",
            "Use the example formatting.",
        )
        for instruction in mentions:
            with self.subTest(instruction=instruction):
                self.assertFalse(chart.is_illustrative_request(instruction))

    def test_english_illustrative_permission_and_negation_semantics(self):
        chart = self.load_chart_support()
        permitted = (
            "Use illustrative data.",
            "Generate hypothetical values for this example.",
            "Sample data is fine.",
            "You may make up example values for demonstration.",
            "Use hypothetical numbers.",
        )
        denied = (
            "Do not use illustrative data.",
            "Don't generate hypothetical values.",
            "No sample data.",
            "Without example values.",
            "Never make up illustrative numbers.",
        )
        for instruction in permitted:
            with self.subTest(permitted=instruction):
                self.assertTrue(chart.is_illustrative_request(instruction))
        for instruction in denied:
            with self.subTest(denied=instruction):
                self.assertFalse(chart.is_illustrative_request(instruction))

    def test_separate_explicit_permission_clause_is_still_allowed(self):
        chart = self.load_chart_support()
        self.assertTrue(chart.is_illustrative_request(
            "不要用第一组示例数据；第二张图可以使用假设数据。"
        ))

    def test_negated_illustrative_request_needs_data_without_calling_llm_or_creating_png(self):
        chart = self.load_chart_support()
        calls = []

        def unexpected_llm_call(*args, **kwargs):
            calls.append((args, kwargs))
            return "must not be called"

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "chart.png"
            result = chart.generate_chart_image(
                "不使用示例数据，请画三个组的比较图", str(output), unexpected_llm_call, locale="zh"
            )
            self.assertEqual(result["status"], "needs_data")
            self.assertFalse(calls)
            self.assertFalse(output.exists())

    def test_explicit_illustrative_permission_enters_chart_spec_flow(self):
        chart = self.load_chart_support()
        calls = []
        response = json.dumps({
            "status": "ok", "chart_type": "bar", "title": "演示比较",
            "x_label": "组别", "y_label": "数值", "categories": ["A", "B", "C"],
            "series": [{"name": "示意组", "values": [10, 20, 30]}],
        })

        def llm_call(prompt, **kwargs):
            calls.append((prompt, kwargs))
            return response

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "chart.png"
            result = chart.generate_chart_image(
                "请使用假设数据画三个组的演示柱状图", str(output), llm_call, locale="zh"
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(len(calls), 1)
            self.assertTrue(calls[0][1]["json_mode"])
            self.assertEqual(result["spec"]["data_note"], "示例数据（假设）")
            self.assertTrue(chart.chart_png_is_valid(str(output)))

    def test_year_only_request_returns_needs_data_without_calling_llm(self):
        chart = self.load_chart_support()
        no_data_requests = (
            "Plot the trend from 2020 to 2024",
            "Use data from 2020 to 2024",
            "Draw a 3D chart with 2 series",
        )
        self.assertTrue(chart.has_usable_numeric_data("The average score was 3.14"))
        self.assertTrue(chart.has_usable_numeric_data("A=2024"))
        for index, instruction in enumerate(no_data_requests):
            with self.subTest(instruction=instruction), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / f"chart-{index}.png"
                calls = []

                def unexpected_llm_call(*args, **kwargs):
                    calls.append((args, kwargs))
                    return "should not be called"

                result = chart.generate_chart_image(instruction, str(output), unexpected_llm_call)
                self.assertEqual(result["status"], "needs_data")
                self.assertFalse(calls)
                self.assertFalse(output.exists())

    def test_prompt_requests_json_and_forbids_invented_factual_values(self):
        chart = self.load_chart_support()
        prompt = chart.build_chart_spec_prompt("Compare A=10 and B=20", locale="en")
        self.assertIn("bar", prompt)
        self.assertIn("scatter", prompt)
        self.assertIn("pie", prompt)
        self.assertIn("needs_data", prompt)
        self.assertIn("A=10 and B=20", prompt)
        self.assertRegex(prompt.lower(), r"never (?:invent|infer)")
        self.assertNotIn("python script", prompt.lower())
        self.assertNotIn("python code", prompt.lower())
        self.assertNotIn("Output only pure Python code", prompt)

    def test_renderer_writes_valid_bar_and_line_pngs(self):
        chart = self.load_chart_support()
        fixtures = [
            (
                "bar", "A=10 and B=20",
                {"status": "ok", "chart_type": "bar", "title": "Bars",
                 "x_label": "Category", "y_label": "Value", "categories": ["A", "B"],
                 "series": [{"name": "S", "values": [10, 20]}]},
            ),
            (
                "line", "2020=10 and 2021=20",
                {"status": "ok", "chart_type": "line", "title": "Trend",
                 "x_label": "Year", "y_label": "Value", "x": ["2020", "2021"],
                 "series": [{"name": "S", "values": [10, 20]}]},
            ),
            (
                "scatter", "x=1 y=4, x=2 y=6",
                {"status": "ok", "chart_type": "scatter", "title": "Relationship",
                 "x_label": "X", "y_label": "Y",
                 "series": [{"name": "S", "x": [1, 2], "y": [4, 6]}]},
            ),
            (
                "pie", "A=30, B=45, C=25",
                {"status": "ok", "chart_type": "pie", "title": "Composition",
                 "labels": ["A", "B", "C"], "values": [30, 45, 25]},
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for name, instruction, raw_spec in fixtures:
                with self.subTest(chart=name):
                    spec = chart.parse_chart_spec(json.dumps(raw_spec), instruction=instruction)
                    path = Path(tmp) / f"{name}.png"
                    chart.render_chart_spec(spec, str(path))
                    payload = path.read_bytes()
                    self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))
                    self.assertGreater(len(payload), 32)
                    width, height = struct.unpack(">II", payload[16:24])
                    self.assertGreater(width, 0)
                    self.assertGreater(height, 0)
                    self.assertLessEqual(width * height, chart.MAX_CHART_PIXELS)

    def test_illustrative_chart_is_rendered_with_a_visible_data_note(self):
        chart = self.load_chart_support()
        raw_spec = {
            "status": "ok", "chart_type": "bar", "title": "Illustration",
            "x_label": "Category", "y_label": "Value", "categories": ["A", "B"],
            "series": [{"name": "S", "values": [10, 20]}],
        }
        spec = chart.parse_chart_spec(
            json.dumps(raw_spec), instruction="Use hypothetical illustrative data for A and B"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "illustrative.png"
            chart.render_chart_spec(spec, str(path))
            self.assertTrue(chart.chart_png_is_valid(str(path)))

    def test_llm_failure_and_invalid_specs_do_not_create_chart_files(self):
        chart = self.load_chart_support()
        with tempfile.TemporaryDirectory() as tmp:
            for response, expected_status in (
                ("API call failed repeatedly: timeout", "error"),
                ('{"status":"ok","chart_type":"python"}', "error"),
            ):
                with self.subTest(expected_status=expected_status), tempfile.TemporaryDirectory(dir=tmp) as case_dir:
                    output = Path(case_dir) / "chart.png"
                    result = chart.generate_chart_image(
                        "Compare A=10 and B=20", str(output), lambda *args, **kwargs: response
                    )
                    self.assertEqual(result["status"], expected_status)
                    self.assertFalse(output.exists())

    def test_both_apps_have_no_llm_python_execution_path(self):
        for name in ("app.py", "app_zh.py"):
            with self.subTest(app=name):
                source = (ROOT / name).read_text(encoding="utf-8")
                self.assertFalse("_clean_python_code" in source, "legacy executable chart cleaner remains")
                self.assertFalse(re.search(r"\bcompile\s*\(\s*cleaned_code", source), "chart code is still compiled")
                self.assertFalse(re.search(r"\bexec\s*\(\s*cleaned_code", source), "chart code is still executed")
                self.assertTrue("generate_chart_image" in source, "shared structured chart flow is not used")
                self.assertFalse(re.search(r"(?:Output only|只输出).{0,30}Python code", source), "chart prompt still requests Python")
                chart_flow = source.split("def generate_chart_for_node", 1)[1].split("def build_chapter_prompt", 1)[0]
                self.assertNotRegex(chart_flow.lower(), r"\bpython\b", "chart generation still contains a Python-code instruction")
                self.assertLess(
                    chart_flow.index('if result["status"] != "ok":'),
                    chart_flow.index('load_json_file("drafts_charts.json", {})'),
                    "chart persistence must happen only after successful validation and rendering",
                )
                self.assertIn("generate_chart_for_node(nid, instruction)", source, "batch chart generation bypasses the shared safe flow")
        chart_source = (ROOT / "chart_support.py").read_text(encoding="utf-8")
        self.assertTrue("json_mode=True" in chart_source, "chart LLM call does not request JSON mode")
        self.assertFalse(re.search(r"(?<![.\w])(?:exec|eval|compile)\s*\(", chart_source), "chart helper executes code")


if __name__ == "__main__":
    unittest.main()
