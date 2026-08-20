import unittest

from application.tracking.sources import (
    get_tracking_sources,
    normalize_tiktok_unique_id,
    tracking_source_key,
    tracking_source_label,
)


class TrackingSourcesTests(unittest.TestCase):
    def test_douyin_source_key_matches_existing_state_files(self):
        profile = {
            "check_interval_minutes": 15,
            "tracking_sources": [
                {
                    "platform": "douyin",
                    "sec_uid": "douyin-sec",
                    "display_name": "Douyin A",
                    "enabled": True,
                }
            ],
        }

        sources = get_tracking_sources(profile)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["platform"], "douyin")
        self.assertEqual(
            sources[0]["source_key"],
            tracking_source_key("douyin", "douyin-sec"),
        )

    def test_mixed_sources_normalize_tiktok_url_and_per_source_interval(self):
        profile = {
            "check_interval_minutes": 30,
            "tracking_sources": [
                {
                    "platform": "tiktok",
                    "profile_url": "https://www.tiktok.com/@jettvn",
                    "display_name": "Jett",
                    "check_interval_minutes": 5,
                },
                {
                    "platform": "douyin",
                    "sec_uid": "douyin-sec",
                    "check_interval_minutes": 10,
                },
            ],
        }

        sources = get_tracking_sources(profile)

        self.assertEqual([source["platform"] for source in sources], ["tiktok", "douyin"])
        self.assertEqual(sources[0]["unique_id"], "jettvn")
        self.assertEqual(sources[0]["check_interval_minutes"], 5)
        self.assertEqual(sources[1]["check_interval_minutes"], 10)
        self.assertEqual(tracking_source_label(sources[0]), "Jett")

    def test_disabled_source_is_only_returned_when_requested(self):
        profile = {
            "tracking_sources": [
                {"platform": "tiktok", "unique_id": "disabled", "enabled": False}
            ]
        }

        self.assertEqual(get_tracking_sources(profile), [])
        self.assertEqual(len(get_tracking_sources(profile, include_disabled=True)), 1)

    def test_tiktok_username_accepts_profile_url_or_handle(self):
        self.assertEqual(normalize_tiktok_unique_id("@jettvn"), "jettvn")
        self.assertEqual(
            normalize_tiktok_unique_id("https://www.tiktok.com/@jettvn?lang=vi"),
            "jettvn",
        )


if __name__ == "__main__":
    unittest.main()
