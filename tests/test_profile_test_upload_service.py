import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.profile_management_service import ProfileManagementService
from services.profile_test_upload_service import ProfileTestUploadService


class ProfileTestUploadServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profiles = ProfileManagementService(Path(self.temp.name) / "profiles")
        self.service = ProfileTestUploadService(self.profiles)

    def tearDown(self):
        self.temp.cleanup()

    def test_start_requires_an_enabled_source(self):
        self.profiles.create("2", "Empty")

        with self.assertRaisesRegex(ValueError, "nguồn Douyin"):
            self.service.start("2")

    def test_start_requires_an_enabled_platform(self):
        profile = self.profiles.create("2", "No platforms")
        profile["douyin"]["sources"] = [{"target_sec_uid": "source-a", "enabled": True}]
        self.profiles.save("2", profile)

        with self.assertRaisesRegex(ValueError, "nền tảng"):
            self.service.start("2")

    @patch("services.profile_test_upload_service.threading.Thread")
    def test_start_records_state_before_background_work(self, thread):
        profile = self.profiles.create("2", "Ready")
        profile["douyin"]["sources"] = [{"target_sec_uid": "source-a", "enabled": True}]
        profile["youtube"]["enabled"] = True
        self.profiles.save("2", profile)

        state = self.service.start("2")

        self.assertTrue(state["active"])
        self.assertEqual(state["platforms"], ["youtube"])
        thread.return_value.start.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
