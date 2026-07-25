import tempfile
import unittest

from profile_automation.watchers.douyin_profile_monitor import (
    DouyinProfileMonitor,
    _analyze_aweme_payload,
    _is_photo_aweme,
    _parse_aweme,
)


def _aweme(
    aweme_id: str,
    *,
    aweme_type: int = 0,
    media_type: int = 4,
    duration: int = 7_700,
) -> dict:
    return {
        "aweme_id": aweme_id,
        "aweme_type": aweme_type,
        "media_type": media_type,
        "duration": duration,
        "desc": "sample",
        "create_time": 1_700_000_000,
        "author": {"uid": "author", "nickname": "Author"},
        "statistics": {"digg_count": 10, "play_count": 20},
        "status": {"is_delete": 0, "private_status": 0},
        "music": {"duration": 180},
        "video": {
            "duration": duration,
            "play_addr": {
                "url_list": ["https://example.test/video.mp4"],
            },
        },
    }


class DouyinProfileMonitorTests(unittest.TestCase):
    def test_type_68_photo_post_is_skipped_before_music_duration_fallback(self):
        item = _aweme(
            "photo-68",
            aweme_type=68,
            media_type=2,
            duration=0,
        )
        item["video"]["play_addr"] = {
            "uri": "https://example.test/background.mp3",
            "url_list": ["https://example.test/background.mp3"],
        }

        self.assertTrue(_is_photo_aweme(item))
        self.assertIsNone(_parse_aweme(item))

    def test_media_type_2_zero_duration_photo_is_skipped_without_aweme_type(self):
        item = _aweme(
            "photo-media",
            aweme_type=0,
            media_type=2,
            duration=0,
        )

        self.assertTrue(_is_photo_aweme(item))
        self.assertIsNone(_parse_aweme(item))

    def test_legacy_image_post_info_is_still_skipped(self):
        item = _aweme("photo-legacy")
        item["image_post_info"] = {"images": [{"url_list": ["https://image"]}]}

        self.assertTrue(_is_photo_aweme(item))
        self.assertIsNone(_parse_aweme(item))

    def test_regular_video_is_preserved(self):
        item = _aweme("video")

        self.assertFalse(_is_photo_aweme(item))
        video = _parse_aweme(item)

        self.assertIsNotNone(video)
        self.assertEqual(video.aweme_id, "video")
        self.assertEqual(video.duration_ms, 7_700)

    def test_analysis_counts_new_photo_shapes_as_image_posts(self):
        data = {
            "status_code": 0,
            "aweme_list": [
                _aweme("video"),
                _aweme("photo-68", aweme_type=68, media_type=2, duration=0),
                _aweme("photo-media", aweme_type=0, media_type=2, duration=0),
            ],
        }

        analysis = _analyze_aweme_payload(data)

        self.assertEqual(analysis["valid_video_count"], 1)
        self.assertEqual(analysis["image_post_count"], 2)
        self.assertEqual(analysis["invalid_duration_count"], 0)

    def test_monitor_keeps_browser_profile_config(self):
        profile = {
            "id": "1",
            "browser": {
                "provider": "local_chromium",
                "user_data_dir": r"C:\profiles\profiles\1",
            },
        }
        with tempfile.TemporaryDirectory() as state_dir:
            monitor = DouyinProfileMonitor(
                profile_id="1",
                sec_uid="source",
                gemlogin_profile_id="1",
                state_dir=state_dir,
                source_key="source-key",
                profile_config=profile,
            )

        self.assertIs(monitor.profile_config, profile)


if __name__ == "__main__":
    unittest.main()
