import tempfile
import unittest
from pathlib import Path

from services.profile_management_service import ProfileManagementService


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
            profile["douyin"]["sources"] = [
                {"target_sec_uid": source_uid, "enabled": True}
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
        self.assertEqual(loaded["douyin"]["target_sec_uid"], "source-a")
        self.assertEqual(loaded["youtube"]["channel_id"], "channel")

    def test_duplicate_source_in_another_profile_is_rejected(self):
        first = self._profile("1", "shared-source")
        self.service.save("1", first)
        second = self._profile("2", "shared-source")

        with self.assertRaisesRegex(ValueError, "Profile 1"):
            self.service.save("2", second)

    def test_duplicate_source_in_same_profile_is_rejected(self):
        profile = self._profile("1")
        profile["douyin"]["sources"] = [
            {"target_sec_uid": "same", "enabled": True},
            {"target_sec_uid": "same", "enabled": False},
        ]

        with self.assertRaisesRegex(ValueError, "bị trùng"):
            self.service.save("1", profile)

    def test_source_order_is_preserved_for_ui_numbering(self):
        profile = self._profile("1")
        profile["douyin"]["sources"] = [
            {"target_sec_uid": "source-c", "target_display_name": "Third", "enabled": True},
            {"target_sec_uid": "source-a", "target_display_name": "First", "enabled": True},
            {"target_sec_uid": "source-b", "target_display_name": "Second", "enabled": False},
        ]

        saved = self.service.save("1", profile)
        labels = [row["label"] for row in self.service.list_seen_sources("1")]

        self.assertEqual(
            [source["target_sec_uid"] for source in saved["douyin"]["sources"]],
            ["source-c", "source-a", "source-b"],
        )
        self.assertEqual(labels, ["1. Third", "2. First", "3. Second"])

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


if __name__ == "__main__":
    unittest.main()
