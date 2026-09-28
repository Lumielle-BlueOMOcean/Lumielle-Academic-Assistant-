import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import llm_connection_support


ROOT = Path(__file__).resolve().parents[1]


def isolated_app(root: Path, source_name: str, profiles=None) -> Path:
    """Copy only repository source modules into a fresh, data-isolated AppTest tree."""
    for source in ROOT.glob("*.py"):
        shutil.copy2(source, root / source.name)
    if source_name != "app.py":
        shutil.copy2(ROOT / source_name, root / "app.py")
    if profiles is not None:
        data_dir = root / "data"
        data_dir.mkdir(exist_ok=True)
        (data_dir / "llm_profiles.json").write_text(
            json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return root / "app.py"


class LLMConnectionUITests(unittest.TestCase):
    CASES = (
        ("app.py", "🧪 Test Model Connection", "The API Key was rejected"),
        ("app_zh.py", "🧪 测试模型连接", "API Key 无效或认证失败"),
    )

    def test_bilingual_pages_render_fresh_default_and_preserve_existing_profiles(self):
        for source_name, test_button, _localized_failure in self.CASES:
            with self.subTest(app=source_name), tempfile.TemporaryDirectory() as temp_dir:
                app_path = isolated_app(Path(temp_dir), source_name)
                app = AppTest.from_file(str(app_path), default_timeout=30).run()

                self.assertFalse(app.exception)
                self.assertTrue(any("Fetch Model List" in item.label or "拉取模型列表" in item.label for item in app.button))
                self.assertTrue(any(test_button in item.label for item in app.button))
                self.assertTrue(any("v1.1.0" in item.value for item in app.caption))
                saved_defaults = json.loads((Path(temp_dir) / "data" / "llm_profiles.json").read_text(encoding="utf-8"))
                self.assertEqual(saved_defaults[0]["base_url"], "https://api.deepseek.com")
                self.assertEqual(saved_defaults[0]["model"], "deepseek-flash")

            fixture = [{
                "id": "existing",
                "name": "Existing profile",
                "base_url": "https://provider.example/v1",
                "api_key": "FAKE_ONLY_PROFILE_KEY",
                "model": "custom-chat-model",
            }]
            with self.subTest(app=source_name, existing="untouched"), tempfile.TemporaryDirectory() as temp_dir:
                app_root = Path(temp_dir)
                app_path = isolated_app(app_root, source_name, fixture)
                profile_path = app_root / "data" / "llm_profiles.json"
                before = profile_path.read_bytes()
                app = AppTest.from_file(str(app_path), default_timeout=30).run()
                self.assertFalse(app.exception)
                self.assertEqual(profile_path.read_bytes(), before)

    def test_new_configuration_can_test_then_reject_stale_pass_and_save_after_failure(self):
        models = ["deepseek-flash", "deepseek-v4-pro"]
        for source_name, test_button, localized_failure in self.CASES:
            with self.subTest(app=source_name), tempfile.TemporaryDirectory() as temp_dir:
                app_path = isolated_app(Path(temp_dir), source_name)
                app = AppTest.from_file(str(app_path), default_timeout=30).run()
                next(item for item in app.text_input if item.key == "n_api_key").set_value("FAKE_ONLY_TEST_KEY")
                app.run()

                with patch.object(
                    llm_connection_support,
                    "fetch_available_models",
                    return_value={"ok": True, "code": "ok", "models": models, "message": ""},
                ) as fetch:
                    next(item for item in app.button if item.key == "fetch_new_models").click()
                    app.run()
                    fetch.assert_called_once()

                with patch.object(
                    llm_connection_support,
                    "test_model_connection",
                    return_value={"ok": True, "code": "ok", "model": "deepseek-flash", "message": ""},
                ) as test:
                    next(item for item in app.button if item.key == "test_new_model_connection").click()
                    app.run()
                    test.assert_called_once_with(
                        "https://api.deepseek.com", "FAKE_ONLY_TEST_KEY", "deepseek-flash"
                    )
                self.assertFalse(app.exception)
                self.assertTrue(any("Model connection is working" in item.value or "模型连接正常" in item.value for item in app.success))

                key_field = next(item for item in app.text_input if item.key == "n_api_key")
                key_field.set_value("FAKE_CHANGED_TEST_KEY")
                app.run()
                self.assertFalse(any("Model connection is working" in item.value or "模型连接正常" in item.value for item in app.success))
                next(item for item in app.text_input if item.key == "n_api_key").set_value("FAKE_ONLY_TEST_KEY")
                app.run()
                self.assertTrue(any("Model connection is working" in item.value or "模型连接正常" in item.value for item in app.success))

                next(item for item in app.text_input if item.key == "n_base_url").set_value("https://changed.example/v1")
                app.run()
                self.assertFalse(any("Model connection is working" in item.value or "模型连接正常" in item.value for item in app.success))
                next(item for item in app.text_input if item.key == "n_base_url").set_value("https://api.deepseek.com")
                app.run()
                self.assertTrue(any("Model connection is working" in item.value or "模型连接正常" in item.value for item in app.success))

                next(item for item in app.selectbox if item.key == "n_model_select").set_value("deepseek-v4-pro")
                app.run()
                self.assertFalse(app.exception)
                self.assertFalse(any("Model connection is working" in item.value or "模型连接正常" in item.value for item in app.success))

                with patch.object(
                    llm_connection_support,
                    "test_model_connection",
                    return_value={"ok": False, "code": "authentication_failed", "model": "deepseek-v4-pro", "message": "not displayed"},
                ):
                    next(item for item in app.button if item.key == "test_new_model_connection").click()
                    app.run()
                self.assertFalse(app.exception)
                self.assertTrue(any(localized_failure in item.value for item in app.error))

                next(item for item in app.button if item.key == "save_new_llm_profile").click()
                app.run()
                saved = json.loads((Path(temp_dir) / "data" / "llm_profiles.json").read_text(encoding="utf-8"))
                self.assertEqual(saved[-1]["model"], "deepseek-v4-pro")
                self.assertTrue(any("connection test failed" in item.value.lower() or "连接测试未通过" in item.value for item in app.warning))

    def test_existing_profile_test_uses_edit_values_and_localizes_success(self):
        profile = [{
            "id": "existing",
            "name": "Existing profile",
            "base_url": "https://provider.example/v1",
            "api_key": "FAKE_ONLY_OLD_KEY",
            "model": "saved-model",
        }]
        for source_name, _test_button, _localized_failure in self.CASES:
            with self.subTest(app=source_name), tempfile.TemporaryDirectory() as temp_dir:
                app_path = isolated_app(Path(temp_dir), source_name, profile)
                app = AppTest.from_file(str(app_path), default_timeout=30).run()
                next(item for item in app.text_input if item.key == "eb_existing").set_value("https://edited.example/v1")
                next(item for item in app.text_input if item.key == "ek_existing").set_value("FAKE_ONLY_EDITED_KEY")
                app.run()

                with patch.object(
                    llm_connection_support,
                    "test_model_connection",
                    return_value={"ok": True, "code": "ok", "model": "saved-model", "message": ""},
                ) as test:
                    next(item for item in app.button if item.key == "etest_existing").click()
                    app.run()
                    test.assert_called_once_with(
                        "https://edited.example/v1", "FAKE_ONLY_EDITED_KEY", "saved-model"
                    )
                self.assertFalse(app.exception)
                self.assertTrue(any("模型连接正常" in item.value or "Model connection is working" in item.value for item in app.success))
                next(item for item in app.text_input if item.key == "ek_existing").set_value("FAKE_ONLY_CHANGED_KEY")
                app.run()
                self.assertFalse(any("模型连接正常" in item.value or "Model connection is working" in item.value for item in app.success))


if __name__ == "__main__":
    unittest.main()
