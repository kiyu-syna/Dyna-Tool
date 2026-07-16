import unittest

from profile_automation.douyin_sources import (
    get_douyin_sources,
    serialize_douyin_sources,
)


class DouyinSourceIntervalTests(unittest.TestCase):
    def test_profile_interval_overrides_every_legacy_source_interval(self):
        profile = {
            "check_interval_minutes": 7,
            "douyin": {
                "sources": [
                    {"target_sec_uid": "source-a", "check_interval_minutes": 5},
                    {"target_sec_uid": "source-b", "check_interval_minutes": 45},
                ]
            },
        }

        sources = get_douyin_sources(profile)

        self.assertEqual([source["check_interval_minutes"] for source in sources], [7, 7])

    def test_missing_profile_interval_migrates_from_first_source(self):
        profile = {
            "douyin": {
                "sources": [
                    {"target_sec_uid": "source-a", "check_interval_minutes": 9},
                    {"target_sec_uid": "source-b", "check_interval_minutes": 30},
                ]
            },
        }

        sources = get_douyin_sources(profile)

        self.assertEqual([source["check_interval_minutes"] for source in sources], [9, 9])

    def test_serialized_sources_do_not_duplicate_common_interval(self):
        serialized = serialize_douyin_sources([
            {
                "target_sec_uid": "source-a",
                "target_display_name": "Nguồn A",
                "check_interval_minutes": 10,
                "enabled": True,
            }
        ])

        self.assertNotIn("check_interval_minutes", serialized[0])


if __name__ == "__main__":
    unittest.main()
