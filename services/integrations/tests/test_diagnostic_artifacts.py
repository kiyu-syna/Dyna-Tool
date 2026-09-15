import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.integrations.diagnostic_artifact_service import (
    browser_diagnostic_screenshot,
    list_browser_diagnostics,
    record_browser_diagnostic,
)


class _FakePage:
    url = "https://example.test/upload"

    def screenshot(self, *, path, full_page, timeout):
        Path(path).write_bytes(b"png")


class DiagnosticArtifactTests(unittest.TestCase):
    def test_diagnostic_is_grouped_by_profile_video_and_platform(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "services.integrations.diagnostic_artifact_service.config.BASE_DIR", directory
        ):
            record = record_browser_diagnostic(
                page=_FakePage(),
                profile_id="2",
                video_id="7662",
                platform="youtube",
                error="chooser timeout",
                last_response={"status": 500, "url": "https://example.test/api"},
                send_telegram=False,
            )

            metadata_path = Path(record["metadata_path"])
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertTrue(Path(record["screenshot_path"]).is_file())
            self.assertIn("profile_2", str(metadata_path))
            self.assertIn("video_7662", str(metadata_path))
            self.assertIn("youtube", str(metadata_path))
            self.assertEqual(payload["last_response"]["status"], 500)
            self.assertEqual(payload["url"], _FakePage.url)

            history = list_browser_diagnostics(limit=10)
            self.assertEqual(history[0]["event_id"], record["event_id"])
            self.assertTrue(history[0]["screenshot_available"])
            self.assertEqual(history[0]["screenshot_path"], "")

            screenshot = browser_diagnostic_screenshot(record["event_id"])
            self.assertIsNotNone(screenshot)
            self.assertTrue(screenshot["data_url"].startswith("data:image/png;base64,"))


if __name__ == "__main__":
    unittest.main()
