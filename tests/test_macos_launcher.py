import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = {
    "zh": ROOT / "packaging/macos/一键启动.command",
    "en": ROOT / "packaging/macos/Launch.command",
}


class MacOSLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="Lumielle launcher test ")
        self.package = Path(self.temp_dir.name) / "微光 package with spaces"
        self.package.mkdir()
        (self.package / ".uv").mkdir()
        (self.package / ".python/3.12/bin").mkdir(parents=True)
        (self.package / "fake-bin").mkdir()
        (self.package / "requirements.txt").write_text("# test fixture\n", encoding="utf-8")
        (self.package / "app.py").write_text("# test fixture\n", encoding="utf-8")

        self.log = self.package / "calls.log"
        self._write_executable(
            self.package / ".uv/uv",
            """#!/bin/bash
if [ "$1" = "--version" ]; then
    echo 'uv test stub'
elif [ "$1" = "venv" ]; then
    mkdir -p venv/bin
    cat > venv/bin/activate <<'ACTIVATE'
PATH="$(pwd)/fake-bin:$PATH"
export PATH
deactivate() { :; }
ACTIVATE
else
    exit 2
fi
""",
        )
        self._write_executable(
            self.package / ".python/3.12/bin/python3.12",
            "#!/bin/bash\necho 'Python 3.12.0'\n",
        )
        self._write_executable(
            self.package / "fake-bin/python",
            """#!/bin/bash
args="$*"
if [[ "$args" == *"-m pip install"* && "$args" == *"pypi.tuna.tsinghua.edu.cn"* ]]; then
    [[ "$args" == *"--timeout 15 --retries 1"* ]] || exit 24
    echo PRIMARY >> "$LUMIELLE_TEST_LOG"
    [ "${LUMIELLE_FAIL_PRIMARY:-0}" = "1" ] && exit 21
    exit 0
fi
if [[ "$args" == *"-m pip install"* && "$args" == *"pypi.org/simple"* ]]; then
    [[ "$args" == *"--timeout 15 --retries 1"* ]] || exit 24
    echo FALLBACK >> "$LUMIELLE_TEST_LOG"
    [ "${LUMIELLE_FAIL_FALLBACK:-0}" = "1" ] && exit 22
    exit 0
fi
if [[ "$args" == *"-m streamlit run app.py"* ]]; then
    echo APP >> "$LUMIELLE_TEST_LOG"
    exit 0
fi
echo "UNEXPECTED: $args" >> "$LUMIELLE_TEST_LOG"
exit 23
""",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _write_executable(path, content):
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def _run_launcher(self, locale, *, fail_primary=False, fail_fallback=False, stamp=False):
        launcher = LAUNCHERS[locale]
        self.assertTrue(launcher.is_file(), f"missing canonical launcher: {launcher}")
        if self.log.exists():
            self.log.unlink()
        installed_stamp = self.package / ".deps_installed"
        if installed_stamp.exists():
            installed_stamp.unlink()
        shutil.rmtree(self.package / "venv", ignore_errors=True)
        target = self.package / launcher.name
        target.write_bytes(launcher.read_bytes())
        target.chmod(0o755)
        if stamp:
            (self.package / ".deps_installed").touch()
        env = os.environ.copy()
        env.update(
            LUMIELLE_TEST_LOG=str(self.log),
            LUMIELLE_FAIL_PRIMARY="1" if fail_primary else "0",
            LUMIELLE_FAIL_FALLBACK="1" if fail_fallback else "0",
        )
        result = subprocess.run(
            [str(target)],
            input="x",
            text=True,
            capture_output=True,
            env=env,
            timeout=20,
            check=False,
        )
        calls = self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []
        return result, calls

    def test_preferred_index_success_starts_without_fallback_for_both_locales(self):
        for locale in LAUNCHERS:
            with self.subTest(locale=locale):
                result, calls = self._run_launcher(locale)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(calls, ["PRIMARY", "APP"])
                self.assertTrue((self.package / ".deps_installed").is_file())

    def test_official_index_fallback_starts_after_primary_failure_for_both_locales(self):
        for locale in LAUNCHERS:
            with self.subTest(locale=locale):
                result, calls = self._run_launcher(locale, fail_primary=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(calls, ["PRIMARY", "FALLBACK", "APP"])
                self.assertTrue((self.package / ".deps_installed").is_file())

    def test_both_index_failures_stop_before_streamlit_and_show_localized_failure(self):
        expected = {
            "zh": ("依赖安装失败", "正在自动切换备用源"),
            "en": ("Dependency installation failed", "Retrying with the official Python package index"),
        }
        for locale, messages in expected.items():
            with self.subTest(locale=locale):
                result, calls = self._run_launcher(locale, fail_primary=True, fail_fallback=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, ["PRIMARY", "FALLBACK"])
                self.assertFalse((self.package / ".deps_installed").exists())
                for message in messages:
                    self.assertIn(message, result.stdout)

    def test_existing_dependency_stamp_skips_both_indexes(self):
        for locale in LAUNCHERS:
            with self.subTest(locale=locale):
                result, calls = self._run_launcher(locale, stamp=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(calls, ["APP"])

    def test_release_notes_describe_automatic_macos_index_fallback(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("automatically retries the official Python package index", readme)
        self.assertIn("自动切换至官方 Python 软件源", readme)


if __name__ == "__main__":
    unittest.main()
