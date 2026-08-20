import json
import tempfile
import unittest
from pathlib import Path

from application.tracking.profile_management_service import ProfileManagementService


class ProfileManagementServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.service = ProfileManagementService(root / "profiles", root / "state")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _profile(self, profile_id: str, source_uid: str = "") -> dict:
        profile = self.service.create(profile_id, f"Profile {profile_id}")
        if source_uid:
            profile["tracking_sources"] = [
                {"platform": "douyin", "sec_uid": source_uid, "enabled": True}
            ]
        return profile

    def test_create_save_and_load_profile(self):
        profile = self._profile("1", "source-a")
        profile["youtube"].update(
            {"enabled": True, "channel_id": "channel", "preset": "slow", "crf": 16}
        )

        saved = self.service.save("1", profile)
        loaded = self.service.load("1")

        self.assertEqual(saved, loaded)
        self.assertEqual(loaded["tracking_sources"][0]["sec_uid"], "source-a")
        self.assertEqual(loaded["youtube"]["channel_id"], "channel")

    def test_caption_telegram_options_default_to_disabled_and_are_normalized(self):
        profile = self._profile("1")

        self.assertEqual(
            profile["caption_options"],
            {
                "tiktok_use_original_desc": False,
                "douyin_use_original_desc": False,
                "telegram_use_custom_caption": False,
                "telegram_pin_caption_message": False,
            },
        )

        profile["caption_options"] = {
            "telegram_use_custom_caption": 1,
            "telegram_pin_caption_message": "",
        }
        saved = self.service.save("1", profile)

        self.assertTrue(saved["caption_options"]["telegram_use_custom_caption"])
        self.assertFalse(saved["caption_options"]["telegram_pin_caption_message"])
        self.assertFalse(saved["caption_options"]["tiktok_use_original_desc"])
        self.assertFalse(saved["caption_options"]["douyin_use_original_desc"])

    def test_duplicate_source_in_another_profile_is_rejected(self):
        first = self._profile("1", "shared-source")
        self.service.save("1", first)
        second = self._profile("2", "shared-source")

        with self.assertRaisesRegex(ValueError, "Profile 1"):
            self.service.save("2", second)

    def test_duplicate_source_in_same_profile_is_rejected(self):
        profile = self._profile("1")
        profile["tracking_sources"] = [
            {"platform": "douyin", "sec_uid": "same", "enabled": True},
            {"platform": "douyin", "sec_uid": "same", "enabled": False},
        ]

        with self.assertRaisesRegex(ValueError, "bị trùng"):
            self.service.save("1", profile)

    def test_source_order_is_preserved_for_ui_numbering(self):
        profile = self._profile("1")
        profile["tracking_sources"] = [
            {"platform": "douyin", "sec_uid": "source-c", "display_name": "Third", "enabled": True},
            {"platform": "douyin", "sec_uid": "source-a", "display_name": "First", "enabled": True},
            {"platform": "douyin", "sec_uid": "source-b", "display_name": "Second", "enabled": False},
        ]

        saved = self.service.save("1", profile)
        labels = [row["label"] for row in self.service.list_seen_sources("1")]

        self.assertEqual(
            [source["sec_uid"] for source in saved["tracking_sources"]],
            ["source-c", "source-a", "source-b"],
        )
        self.assertEqual(labels, ["1. Third", "2. First", "3. Second"])

    def test_mixed_sources_are_saved_in_one_canonical_list(self):
        profile = self._profile("1")
        profile["tracking_sources"] = [
            {
                "platform": "tiktok",
                "profile_url": "https://www.tiktok.com/@jettvn",
                "display_name": "Jett",
                "enabled": True,
                "check_interval_minutes": 5,
            },
            {
                "platform": "douyin",
                "sec_uid": "douyin-sec",
                "display_name": "Douyin A",
                "enabled": True,
                "check_interval_minutes": 10,
            },
        ]

        saved = self.service.save("1", profile)
        seen_sources = self.service.list_seen_sources("1")

        self.assertEqual(
            [source["platform"] for source in saved["tracking_sources"]],
            ["tiktok", "douyin"],
        )
        self.assertEqual(saved["tracking_sources"][0]["unique_id"], "jettvn")
        self.assertNotIn("sources", saved["douyin"])
        self.assertEqual([source["platform"] for source in seen_sources], ["tiktok", "douyin"])

    def test_duplicate_tiktok_source_in_another_profile_is_rejected(self):
        first = self._profile("1")
        first["tracking_sources"] = [
            {"platform": "tiktok", "profile_url": "https://www.tiktok.com/@jettvn"}
        ]
        self.service.save("1", first)
        second = self._profile("2")
        second["tracking_sources"] = [
            {"platform": "tiktok", "unique_id": "jettvn"}
        ]

        with self.assertRaisesRegex(ValueError, "Profile 1"):
            self.service.save("2", second)

    def test_empty_tracking_sources_stays_empty(self):
        profile = self._profile("1", "old-douyin")
        saved = self.service.save("1", profile)
        saved["tracking_sources"] = []

        cleared = self.service.save("1", saved)

        self.assertEqual(cleared["tracking_sources"], [])
        self.assertNotIn("sources", cleared["douyin"])

    def test_seen_state_is_normalized_and_limited(self):
        profile = self._profile("1", "source-a")
        saved_profile = self.service.save("1", profile)
        source_key = self.service.list_seen_sources("1")[0]["source_key"]

        result = self.service.save_seen(
            "1",
            source_key,
            {
                "seen_ids": [str(index) for index in range(510)] + ["509"],
                "last_check": "2026-07-15T12:00:00",
                "last_video_create_time": "123",
            },
        )

        self.assertEqual(saved_profile["id"], "1")
        self.assertEqual(len(result["seen_ids"]), 500)
        self.assertEqual(result["seen_ids"][0], "10")
        self.assertEqual(result["last_video_create_time"], 123)

    def test_non_numeric_profile_id_is_rejected(self):
        with self.assertRaises(ValueError):
            self.service.create("abc", "Invalid")

    def test_browser_defaults_to_dyna_managed_local_chromium(self):
        profile = self._profile("1")

        self.assertEqual(profile["browser"]["provider"], "local_chromium")
        self.assertEqual(profile["browser"]["executable_path"], "")

    def test_local_browser_config_is_normalized_and_saved(self):
        profile = self._profile("1")
        profile["browser"] = {
            "provider": "local_chromium",
            "user_data_dir": r"C:\profiles\profiles\1",
            "executable_path": r"C:\browsers\iron\chrome.exe",
            "profile_directory": "Default",
            "background": True,
        }

        saved = self.service.save("1", profile)

        self.assertEqual(saved["browser"]["provider"], "local_chromium")
        self.assertEqual(saved["browser"]["launch_timeout_ms"], 60_000)

    def test_proxy_password_is_dpapi_encrypted_and_redacted_for_ui(self):
        profile = self._profile("1")
        profile["browser"]["proxy"] = {
            "enabled": True,
            "server": "proxy.example:8080",
            "username": "proxy-user",
            "password": "top-secret",
            "password_set": True,
            "bypass": " localhost, 127.0.0.1 ",
        }

        saved = self.service.save("1", profile)
        stored = json.loads(
            (self.service.profile_dir / "profile_1.json").read_text(encoding="utf-8")
        )
        public = self.service.redact_secrets(saved)

        self.assertEqual(saved["browser"]["proxy"]["password"], "top-secret")
        self.assertEqual(stored["browser"]["proxy"]["server"], "http://proxy.example:8080")
        self.assertTrue(stored["browser"]["proxy"]["password_encrypted"].startswith("dpapi:"))
        self.assertNotIn("top-secret", json.dumps(stored))
        self.assertEqual(public["browser"]["proxy"]["password"], "")
        self.assertTrue(public["browser"]["proxy"]["password_set"])
        self.assertNotIn("password_encrypted", public["browser"]["proxy"])

    def test_saving_redacted_proxy_preserves_existing_password(self):
        profile = self._profile("1")
        profile["browser"]["proxy"] = {
            "enabled": True,
            "server": "socks5://proxy.example:1080",
            "password": "keep-me",
            "password_set": True,
        }
        saved = self.service.save("1", profile)
        public = self.service.redact_secrets(saved)

        saved_again = self.service.save("1", public)

        self.assertEqual(saved_again["browser"]["proxy"]["password"], "keep-me")

    def test_redacted_proxy_can_clear_existing_password(self):
        profile = self._profile("1")
        profile["browser"]["proxy"] = {
            "enabled": True,
            "server": "http://proxy.example:8080",
            "password": "remove-me",
            "password_set": True,
        }
        saved = self.service.save("1", profile)
        public = self.service.redact_secrets(saved)
        public["browser"]["proxy"]["password_set"] = False

        saved_again = self.service.save("1", public)

        self.assertEqual(saved_again["browser"]["proxy"]["password"], "")

    def test_enabled_proxy_requires_server(self):
        profile = self._profile("1")
        profile["browser"]["proxy"] = {"enabled": True, "server": ""}

        with self.assertRaisesRegex(ValueError, "địa chỉ proxy"):
            self.service.save("1", profile)

    def test_original_gemlogin_profile_cannot_be_saved_as_local(self):
        profile = self._profile("1")
        profile["browser"] = {
            "provider": "local_chromium",
            "user_data_dir": r"C:\Users\Tester\.gemlogin\profile\profiles\1",
            "executable_path": r"C:\browsers\iron\chrome.exe",
        }

        with self.assertRaisesRegex(ValueError, "profile gốc"):
            self.service.save("1", profile)


if __name__ == "__main__":
    unittest.main()
