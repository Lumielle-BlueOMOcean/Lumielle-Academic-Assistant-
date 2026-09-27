import ast
import importlib.util
import re
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_project_module(test_case, name):
    path = ROOT / f"{name}.py"
    test_case.assertTrue(path.is_file(), f"Expected project module {path.name} to exist")
    spec = importlib.util.spec_from_file_location(name, path)
    test_case.assertIsNotNone(spec)
    test_case.assertIsNotNone(spec.loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_app_function(test_case, app_name, function_name, namespace):
    source = (ROOT / app_name).read_text(encoding="utf-8")
    module = ast.parse(source)
    function = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    compiled = compile(ast.Module(body=[function], type_ignores=[]), app_name, "exec")
    code = next(item for item in compiled.co_consts if isinstance(item, types.CodeType))
    loaded = types.FunctionType(code, namespace)
    loaded.__defaults__ = tuple(ast.literal_eval(value) for value in function.args.defaults) or None
    return loaded


class ChapterMemoryTests(unittest.TestCase):
    def test_shared_memory_module_exists(self):
        self.assertTrue((ROOT / "memory_support.py").is_file())

    def _chapter(self, size):
        half = size // 2
        body = "x" * (half - len("BEGIN_SENTINEL") - 1)
        tail = "y" * (size - half - len("MIDDLE_SENTINEL") - len("FINAL_CHAPTER_FACT_SENTINEL") - 3)
        chapter = f"BEGIN_SENTINEL {body} MIDDLE_SENTINEL {tail} FINAL_CHAPTER_FACT_SENTINEL"
        self.assertGreater(len(chapter), size - 10)
        return chapter

    def _exercise_complete_chapter(self, size):
        memory_support = load_project_module(self, "memory_support")
        chapter = self._chapter(size)
        calls = []
        sentinels = ("BEGIN_SENTINEL", "MIDDLE_SENTINEL", "FINAL_CHAPTER_FACT_SENTINEL")

        def fake_llm(prompt, **kwargs):
            calls.append(prompt)
            found = [sentinel for sentinel in sentinels if sentinel in prompt]
            if "[MEMORY SYNTHESIS]" in prompt:
                return "Compact chapter memory: " + "; ".join(found)
            return "Chunk facts: " + "; ".join(found)

        result = memory_support.build_chapter_memory(chapter, fake_llm, max_chars=5500)
        chunk_prompts = [prompt for prompt in calls if "[CHAPTER CHUNK " in prompt]
        self.assertGreater(len(chunk_prompts), 1)
        self.assertEqual(
            [int(re.search(r"CHAPTER CHUNK (\d+)/(\d+)", prompt).group(1)) for prompt in chunk_prompts],
            list(range(1, len(chunk_prompts) + 1)),
        )
        extracted_chunks = [prompt.split("[CHAPTER TEXT]\n", 1)[1] for prompt in chunk_prompts]
        self.assertEqual("".join(extracted_chunks), chapter)
        self.assertIn("BEGIN_SENTINEL", chunk_prompts[0])
        self.assertTrue(any("MIDDLE_SENTINEL" in prompt for prompt in chunk_prompts))
        self.assertIn("FINAL_CHAPTER_FACT_SENTINEL", chunk_prompts[-1])
        self.assertIn("FINAL_CHAPTER_FACT_SENTINEL", result)
        self.assertIn("FINAL_CHAPTER_FACT_SENTINEL", calls[-1])
        return len(chunk_prompts)

    def test_ten_thousand_character_chapter_processes_first_middle_and_tail(self):
        self._exercise_complete_chapter(10_000)

    def test_fifty_thousand_character_chapter_processes_first_middle_and_tail(self):
        self._exercise_complete_chapter(50_000)

    def test_short_chapter_uses_one_extraction_chunk_and_synthesis(self):
        memory_support = load_project_module(self, "memory_support")
        prompts = []

        def fake_llm(prompt, **kwargs):
            prompts.append(prompt)
            return "Short chapter memory with its key fact."

        memory_support.build_chapter_memory("A short complete chapter.", fake_llm)
        self.assertEqual(sum("[CHAPTER CHUNK " in prompt for prompt in prompts), 1)
        self.assertEqual(sum("[MEMORY SYNTHESIS]" in prompt for prompt in prompts), 1)

    def test_failed_chunk_retry_preserves_old_memory_and_stops_before_later_chunks(self):
        memory_support = load_project_module(self, "memory_support")
        old_memories = {"chapter-2": "KEEP_OLD_MEMORY"}
        calls = []

        def fake_llm(prompt, **kwargs):
            calls.append(prompt)
            match = re.search(r"CHAPTER CHUNK (\d+)/(\d+)", prompt)
            if match and match.group(1) == "2":
                return "API call failed repeatedly: unavailable"
            return "Extracted complete facts."

        with self.assertRaises(memory_support.LLMOutputError):
            memory_support.update_chapter_memory(
                old_memories,
                "chapter-2",
                "A" * 16_000,
                fake_llm,
                max_chars=5_500,
            )
        self.assertEqual(old_memories, {"chapter-2": "KEEP_OLD_MEMORY"})
        self.assertEqual(sum("CHAPTER CHUNK 2/" in prompt for prompt in calls), 2)
        self.assertFalse(any("CHAPTER CHUNK 3/" in prompt for prompt in calls))
        self.assertFalse(any("[MEMORY SYNTHESIS]" in prompt for prompt in calls))

    def test_completed_memory_is_consumed_by_context_sandwich(self):
        memory = "FINAL_CHAPTER_FACT_SENTINEL"
        tree = [
            ({"id": "first", "title": "Introduction"}, 0),
            ({"id": "current", "title": "Methods"}, 0),
            ({"id": "last", "title": "Conclusion"}, 0),
        ]
        values = {
            "prompts.json": {"global_topic": "Topic", "target_word_count": 1000},
            "logic_tree.json": [],
            "drafts_summary.json": {"first": memory},
        }
        namespace = {
            "load_json_file": lambda name, default=None: values.get(name, default),
            "flatten_tree_nodes": lambda _tree: tree,
            "tree_to_compact_outline": lambda _tree: ["Introduction", "Methods", "Conclusion"],
            "purge_thinking_text": lambda text: text,
        }
        for app_name in ("app.py", "app_zh.py"):
            with self.subTest(app=app_name):
                build_context_sandwich = load_app_function(
                    self, app_name, "build_context_sandwich", namespace
                )
                sandwich = build_context_sandwich("current")
                self.assertIn(memory, sandwich["upstream"])

    def test_both_apps_call_the_shared_memory_pipeline(self):
        for app_name in ("app.py", "app_zh.py"):
            source = (ROOT / app_name).read_text(encoding="utf-8")
            with self.subTest(app=app_name):
                app_tree = ast.parse(source)
                imported = {
                    alias.name
                    for node in app_tree.body
                    if isinstance(node, ast.ImportFrom) and node.module == "memory_support"
                    for alias in node.names
                }
                calls = [
                    node for node in ast.walk(app_tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "update_chapter_memory"
                ]
                self.assertIn("update_chapter_memory", imported)
                self.assertEqual(len(calls), 3)


class VersionTests(unittest.TestCase):
    def test_canonical_version_and_unreleased_readme_entry(self):
        version = load_project_module(self, "version")
        self.assertEqual(version.__version__, "1.0.2")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("v1.0.2 — Unreleased", readme)


if __name__ == "__main__":
    unittest.main()
