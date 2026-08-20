import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.playwright_runtime import configure_packaged_playwright_driver


class PlaywrightRuntimeTests(unittest.TestCase):
    def test_packaged_driver_overrides_transport_paths(self):
        from playwright._impl import _driver, _transport

        original_driver = _driver.compute_driver_executable
        original_transport = _transport.compute_driver_executable
        original_node_path = os.environ.get("PLAYWRIGHT_NODEJS_PATH")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                driver_dir = Path(temp_dir)
                node_path = driver_dir / ("node.exe" if os.name == "nt" else "node")
                cli_path = driver_dir / "package" / "cli.js"
                cli_path.parent.mkdir(parents=True)
                node_path.write_bytes(b"node")
                cli_path.write_text("// driver", encoding="utf-8")

                with patch.dict(
                    os.environ,
                    {"DYNA_PLAYWRIGHT_DRIVER_DIR": str(driver_dir)},
                ):
                    resolved = configure_packaged_playwright_driver()

                self.assertEqual(resolved, (node_path.resolve(), cli_path.resolve()))
                self.assertEqual(
                    _transport.compute_driver_executable(),
                    (str(node_path.resolve()), str(cli_path.resolve())),
                )
        finally:
            _driver.compute_driver_executable = original_driver
            _transport.compute_driver_executable = original_transport
            if original_node_path is None:
                os.environ.pop("PLAYWRIGHT_NODEJS_PATH", None)
            else:
                os.environ["PLAYWRIGHT_NODEJS_PATH"] = original_node_path

    def test_missing_packaged_driver_fails_with_clear_message(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"DYNA_PLAYWRIGHT_DRIVER_DIR": temp_dir},
        ):
            with self.assertRaisesRegex(RuntimeError, "thiếu Playwright driver"):
                configure_packaged_playwright_driver()


if __name__ == "__main__":
    unittest.main()
