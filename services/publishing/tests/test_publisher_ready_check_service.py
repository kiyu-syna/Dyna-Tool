import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.profiles.profile_management_service import ProfileManagementService
from services.publishing.publisher_ready_check_service import PublisherReadyCheckService


class PublisherReadyCheckServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.profiles = ProfileManagementService(root / "profiles", root / "state")
        profile = self.profiles.create("2", "Creator")
        profile["tiktok"]["enabled"] = True
        profile["youtube"]["enabled"] = True
        profile["youtube"]["channel_id"] = "UC-ready"
        self.profiles.save("2", profile)
        self.service = PublisherReadyCheckService(self.profiles)

    def tearDown(self):
        self.service.stop()
        self.temp.cleanup()

    def test_ready_result_is_cached_until_expiry(self):
        result = {
            "tiktok": {
                "status": "ready",
                "ready": True,
                "message": "TikTok Studio sẵn sàng.",
            }
        }
        with patch.object(self.service, "_check_profile_platforms", return_value=result) as check:
            first = self.service.ensure_ready("2", ("tiktok",))
            second = self.service.ensure_ready("2", ("tiktok",))

        self.assertTrue(first["ready"])
        self.assertTrue(second["ready"])
        check.assert_called_once()
        state = self.service.snapshot()["checks"][0]
        self.assertEqual(state["status"], "ready")
        self.assertTrue(state["checked_at"].endswith("Z"))

    def test_failed_account_is_reported_with_platform_message(self):
        result = {
            "youtube": {
                "status": "login_required",
                "ready": False,
                "message": "YouTube đã hết phiên đăng nhập.",
            }
        }
        with patch.object(self.service, "_check_profile_platforms", return_value=result):
            checked = self.service.ensure_ready("2", ("youtube",))

        self.assertFalse(checked["ready"])
        self.assertIn("hết phiên", checked["message"])
        self.assertEqual(checked["checks"][0]["status"], "login_required")

    def test_disabled_platform_is_rejected_before_browser_check(self):
        with self.assertRaisesRegex(ValueError, "chưa bật nền tảng"):
            self.service.ensure_ready("2", ("facebook",))


if __name__ == "__main__":
    unittest.main()
