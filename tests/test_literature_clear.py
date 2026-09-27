import importlib.util
import json
import shutil
import sys
import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


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


class LiteratureClearTests(unittest.TestCase):
    def test_shared_literature_clear_module_exists(self):
        self.assertTrue((ROOT / "literature_support.py").is_file())

    def test_records_only_clear_keeps_imported_source_file(self):
        support = load_project_module(self, "literature_support")
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_dir = Path(temp_dir) / "raw_files"
            raw_dir.mkdir()
            source = raw_dir / "paper.pdf"
            source.write_bytes(b"paper")
            saved = []

            report = support.clear_literature_library(
                [{"file_path": str(source)}], raw_dir, lambda records: saved.append(records) or True
            )

            self.assertEqual(saved, [[]])
            self.assertTrue(report["records_cleared"])
            self.assertTrue(source.is_file())
            self.assertEqual(report["deleted"], 0)

    def test_explicit_clear_deletes_imported_source_file(self):
        support = load_project_module(self, "literature_support")
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_dir = Path(temp_dir) / "raw_files"
            raw_dir.mkdir()
            source = raw_dir / "paper.pdf"
            source.write_bytes(b"paper")

            report = support.clear_literature_library(
                [{"file_path": str(source)}], raw_dir, lambda records: True, delete_source_files=True
            )

            self.assertTrue(report["records_cleared"])
            self.assertFalse(source.exists())
            self.assertEqual((report["deleted"], report["failed"]), (1, 0))

    def test_outside_path_is_not_deleted_and_is_reported(self):
        support = load_project_module(self, "literature_support")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_dir = root / "raw_files"
            raw_dir.mkdir()
            outside = root / "keep.txt"
            outside.write_text("KEEP", encoding="utf-8")

            report = support.clear_literature_library(
                [{"file_path": str(outside)}], raw_dir, lambda records: True, delete_source_files=True
            )

            self.assertEqual(outside.read_text(encoding="utf-8"), "KEEP")
            self.assertEqual(report["deleted"], 0)
            self.assertEqual(report["failed"], 1)

    def test_missing_source_file_does_not_crash_or_count_as_failure(self):
        support = load_project_module(self, "literature_support")
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_dir = Path(temp_dir) / "raw_files"
            raw_dir.mkdir()
            report = support.clear_literature_library(
                [{"file_path": str(raw_dir / "already-missing.pdf")}],
                raw_dir,
                lambda records: True,
                delete_source_files=True,
            )
            self.assertTrue(report["records_cleared"])
            self.assertEqual((report["deleted"], report["failed"]), (0, 0))

    def test_delete_failure_is_reported_without_aborting_record_clear(self):
        support = load_project_module(self, "literature_support")
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_dir = Path(temp_dir) / "raw_files"
            raw_dir.mkdir()
            source = raw_dir / "paper.pdf"
            source.write_bytes(b"paper")
            with patch("pathlib.Path.unlink", side_effect=PermissionError("read only")):
                report = support.clear_literature_library(
                    [{"file_path": str(source)}], raw_dir, lambda records: True, delete_source_files=True
                )
            self.assertTrue(report["records_cleared"])
            self.assertTrue(source.is_file())
            self.assertEqual((report["deleted"], report["failed"]), (0, 1))

    def test_symlink_escape_is_never_followed_or_deleted(self):
        support = load_project_module(self, "literature_support")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_dir = root / "raw_files"
            raw_dir.mkdir()
            outside = root / "outside.pdf"
            outside.write_bytes(b"KEEP")
            link = raw_dir / "linked.pdf"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")

            report = support.clear_literature_library(
                [{"file_path": str(link)}], raw_dir, lambda records: True, delete_source_files=True
            )

            self.assertTrue(link.is_symlink())
            self.assertEqual(outside.read_bytes(), b"KEEP")
            self.assertEqual(report["failed"], 1)

    def test_failed_record_clear_keeps_source_files(self):
        support = load_project_module(self, "literature_support")
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_dir = Path(temp_dir) / "raw_files"
            raw_dir.mkdir()
            source = raw_dir / "paper.pdf"
            source.write_bytes(b"paper")

            report = support.clear_literature_library(
                [{"file_path": str(source)}], raw_dir, lambda records: False, delete_source_files=True
            )

            self.assertFalse(report["records_cleared"])
            self.assertTrue(source.is_file())
            self.assertEqual((report["deleted"], report["failed"]), (0, 0))

    def test_both_apps_use_same_clear_logic_and_localized_labels(self):
        for app_name, label in (
            ("app.py", "Delete imported literature source files as well"),
            ("app_zh.py", "同时删除已导入的文献原始文件"),
        ):
            source = (ROOT / app_name).read_text(encoding="utf-8")
            with self.subTest(app=app_name):
                app_tree = ast.parse(source)
                imported = {
                    alias.name
                    for node in app_tree.body
                    if isinstance(node, ast.ImportFrom) and node.module == "literature_support"
                    for alias in node.names
                }
                calls = [
                    node for node in ast.walk(app_tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "clear_literature_library"
                ]
                self.assertIn("clear_literature_library", imported)
                self.assertEqual(len(calls), 1)
                self.assertIn(label, source)

    def test_global_clear_reports_kept_files_and_partial_deletion_after_rerun(self):
        modules = (
            "version.py", "memory_support.py", "literature_support.py",
            "document_support.py", "writing_support.py", "chart_support.py", "outline_support.py",
        )
        cases = (
            ("app.py", "app.py", "📚 Literature Library Records", "Execute Clear", "Literature records cleared; imported source files were kept.", "0 source file(s) deleted, 1 could not be deleted."),
            ("app_zh.py", "app_zh.py", "📚 文献库记录", "执行清除", "文献库记录已清空；已保留导入的原始文件。", "已删除 0 个原始文件，1 个文件未能删除。"),
        )
        repo = ROOT
        for app_source, app_filename, literature_option, clear_button, kept_message, failure_message in cases:
            for delete_sources in (False, True):
                with self.subTest(app=app_source, delete_sources=delete_sources), tempfile.TemporaryDirectory() as temp_dir:
                    app_root = Path(temp_dir)
                    shutil.copy2(repo / app_source, app_root / app_filename)
                    if app_filename != "app.py":
                        shutil.copy2(repo / app_source, app_root / "app.py")
                    for name in modules:
                        shutil.copy2(repo / name, app_root / name)
                    raw_dir = app_root / "data" / "raw_files"
                    raw_dir.mkdir(parents=True)
                    raw_source = raw_dir / "paper.pdf"
                    raw_source.write_bytes(b"paper")
                    indexed_source = app_root / "outside.pdf" if delete_sources else raw_source
                    if delete_sources:
                        indexed_source.write_bytes(b"outside")
                    (app_root / "data" / "literatures.json").write_text(
                        json.dumps([{"id": "paper", "file_path": str(indexed_source)}]),
                        encoding="utf-8",
                    )

                    app = AppTest.from_file(str(app_root / "app.py"), default_timeout=30).run()
                    next(item for item in app.multiselect if item.key == "clear_sel").set_value([literature_option])
                    app.run()
                    next(item for item in app.checkbox if item.key == "clear_literature_sources").set_value(delete_sources)
                    next(item for item in app.checkbox if item.key == "clear_confirm").set_value(True)
                    app.run()
                    next(item for item in app.button if clear_button in item.label).click()
                    app.run()

                    self.assertFalse(app.exception)
                    self.assertEqual(
                        json.loads((app_root / "data" / "literatures.json").read_text(encoding="utf-8")),
                        [],
                    )
                    messages = "\n".join(
                        item.value
                        for collection in (app.success, app.warning, app.error)
                        for item in collection
                    )
                    if delete_sources:
                        self.assertIn(failure_message, messages)
                        self.assertTrue(indexed_source.exists())
                        self.assertTrue(raw_source.exists())
                    else:
                        self.assertIn(kept_message, messages)
                        self.assertTrue(raw_source.exists())


if __name__ == "__main__":
    unittest.main()
