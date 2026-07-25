import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.browser.browser_runtime_service import (
    BrowserRuntimeError,
    MANIFEST_NAME,
    install_browser_runtime,
    install_playwright_chromium_runtime,
    list_browser_runtimes,
    verify_browser_runtime,
)


class BrowserRuntimeServiceTests(unittest.TestCase):
    def _source_runtime(self, root: Path) -> Path:
        source = root / "source" / "Chrome-bin"
        version = source / "141.0.7390.125"
        version.mkdir(parents=True)
        (source / "chrome.exe").write_bytes(b"browser-executable")
        (source / "gemlogindriver.exe").write_bytes(b"not-needed")
        (version / "chrome.dll").write_bytes(b"chrome-dll")
        (version / "icudtl.dat").write_bytes(b"icu")
        (version / "resources.pak").write_bytes(b"resources")
        return source / "chrome.exe"

    def _flat_runtime(self, root: Path) -> Path:
        source = root / "chromium-1223" / "chrome-win64"
        source.mkdir(parents=True)
        (source / "chrome.exe").write_bytes(b"playwright-chromium")
        (source / "chrome.dll").write_bytes(b"chrome-dll")
        (source / "icudtl.dat").write_bytes(b"icu")
        (source / "resources.pak").write_bytes(b"resources")
        return source / "chrome.exe"

    def test_install_copies_and_verifies_runtime_without_gemlogin_driver(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source_runtime(root)
            destination = root / "managed"

            result = install_browser_runtime(
                source,
                runtime_id="iron-141",
                destination_root=destination,
            )

            executable = Path(result.executable_path)
            self.assertTrue(executable.is_file())
            self.assertFalse((executable.parent / "gemlogindriver.exe").exists())
            self.assertTrue((executable.parent / MANIFEST_NAME).is_file())
            self.assertTrue(verify_browser_runtime(executable).valid)

    def test_reinstall_same_source_reuses_valid_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source_runtime(root)
            destination = root / "managed"
            install_browser_runtime(source, runtime_id="iron-141", destination_root=destination)

            result = install_browser_runtime(
                source,
                runtime_id="iron-141",
                destination_root=destination,
            )

            self.assertTrue(result.reused)

    def test_managed_executable_is_recognized_without_copying_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source_runtime(root)
            destination = root / "managed"
            installed = install_browser_runtime(
                source,
                runtime_id="iron-141",
                destination_root=destination,
            )

            result = install_browser_runtime(
                installed.executable_path,
                destination_root=destination,
            )

            self.assertTrue(result.reused)
            self.assertEqual(result.runtime_id, "iron-141")

    def test_verify_detects_tampered_runtime_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source_runtime(root)
            result = install_browser_runtime(
                source,
                runtime_id="iron-141",
                destination_root=root / "managed",
            )
            manifest = json.loads(
                (Path(result.root_dir) / MANIFEST_NAME).read_text(encoding="utf-8")
            )
            protected_file = Path(result.root_dir) / manifest["files"][1]["path"]
            protected_file.write_bytes(b"tampered")

            with self.assertRaisesRegex(BrowserRuntimeError, "kích thước|SHA-256"):
                verify_browser_runtime(result.executable_path)

    def test_list_reports_installed_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source_runtime(root)
            destination = root / "managed"
            install_browser_runtime(source, runtime_id="iron-141", destination_root=destination)

            rows = list_browser_runtimes(destination)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["runtime_id"], "iron-141")
            self.assertTrue(rows[0]["valid"])

    def test_playwright_runtime_installs_flat_chromium_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._flat_runtime(root)
            with patch(
                "services.browser.browser_runtime_service._playwright_chromium_executable",
                return_value=source,
            ):
                result = install_playwright_chromium_runtime(
                    destination_root=root / "managed"
                )

            self.assertEqual(result.runtime_id, "chromium-1223")
            self.assertTrue(Path(result.executable_path).is_file())


if __name__ == "__main__":
    unittest.main()
