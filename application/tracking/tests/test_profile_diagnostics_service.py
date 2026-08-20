import unittest
from unittest.mock import patch

import application.tracking.profile_diagnostics_service as diagnostics


class ProfileDiagnosticsServiceTests(unittest.TestCase):
    @patch("services.publishing.video_validation_service.resolve_ffprobe", return_value="ffprobe.exe")
    @patch("application.workflows.profile_worker.check_douyin_direct_download")
    @patch.object(diagnostics.config, "load_profile_configs")
    @patch.object(diagnostics.config, "load_settings")
    def test_diagnostics_return_structured_profile_results(
        self,
        load_settings,
        load_profiles,
        direct_check,
        _resolve_ffprobe,
    ):
        load_settings.return_value = {
            "API_URL": "http://127.0.0.1:1010",
            "TELEGRAM_BOT_TOKEN": "token",
            "TELEGRAM_CHAT_ID": "chat",
        }
        load_profiles.return_value = {
            "2": {
                "id": "2",
                "name": "Editor",
                "douyin": {
                    "gemlogin_profile_id": "22",
                },
                "tracking_sources": [
                    {"platform": "douyin", "sec_uid": "source", "enabled": True}
                ],
                "tiktok": {"enabled": False},
                "youtube": {"enabled": True},
            }
        }
        direct_check.return_value = {"ok": True, "message": "ready"}

        with patch(
            "services.integrations.telegram_service.telegram_connection_status",
            return_value={"configured": True, "linked": True},
        ):
            result = diagnostics.run_profile_diagnostics()

        self.assertTrue(result["telegram"]["ok"])
        self.assertTrue(result["ffprobe"]["ok"])
        self.assertEqual(result["profiles"][0]["gemlogin_profile_id"], "22")
        self.assertEqual(result["profiles"][0]["platforms"], ["youtube"])
        direct_check.assert_called_once_with(
            "22",
            api_url="http://127.0.0.1:1010",
            profile_config=load_profiles.return_value["2"],
        )

    @patch("services.publishing.video_validation_service.resolve_ffprobe", return_value="ffprobe.exe")
    @patch.object(diagnostics.config, "load_profile_configs")
    @patch.object(diagnostics.config, "load_settings", return_value={})
    def test_profile_without_source_does_not_open_gemlogin(
        self,
        _load_settings,
        load_profiles,
        _resolve_ffprobe,
    ):
        load_profiles.return_value = {
            "5": {"id": "5", "douyin": {}, "tracking_sources": []}
        }

        with patch("application.workflows.profile_worker.check_douyin_direct_download") as direct_check:
            result = diagnostics.run_profile_diagnostics("5")

        self.assertFalse(result["profiles"][0]["ok"])
        self.assertIn("Không có nguồn", result["profiles"][0]["message"])
        direct_check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
