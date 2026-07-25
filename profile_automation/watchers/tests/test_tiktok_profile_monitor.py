import tempfile
import unittest

from profile_automation.watchers.tiktok_profile_monitor import (
    TikTokProfileMonitor,
    _parse_tiktok_item,
    extract_tiktok_download_urls,
    parse_tiktok_item_list,
)


def make_item(
    item_id: str,
    *,
    create_time: int = 100,
    duration: int = 12,
    pinned=False,
    photo=False,
    likes: int = 10,
) -> dict:
    item = {
        "id": item_id,
        "createTime": create_time,
        "desc": f"video {item_id}",
        "isPinnedItem": pinned,
        "author": {
            "id": "author-id",
            "secUid": "author-sec-uid",
            "uniqueId": "jettvn",
            "nickname": "Jett",
        },
        "stats": {"diggCount": likes, "playCount": 1234},
        "video": {"duration": duration, "width": 576, "height": 1024},
    }
    if photo:
        item["imagePost"] = {"images": [{"imageURL": {"urlList": ["https://image"]}}]}
        item["video"] = {"duration": 0, "width": 0, "height": 0}
    return item


class TikTokItemParserTests(unittest.TestCase):
    def test_parses_regular_video(self):
        video = _parse_tiktok_item(make_item("7664538015968070932"))

        self.assertIsNotNone(video)
        self.assertEqual(video.aweme_id, "7664538015968070932")
        self.assertEqual(video.item_id, "7664538015968070932")
        self.assertEqual(video.duration_ms, 12000)
        self.assertEqual(
            video.share_url,
            "https://www.tiktok.com/@jettvn/video/7664538015968070932",
        )

    def test_skips_pinned_video_even_when_it_is_otherwise_valid(self):
        self.assertIsNone(_parse_tiktok_item(make_item("pinned", pinned=True)))

    def test_skips_photo_post(self):
        self.assertIsNone(_parse_tiktok_item(make_item("photo", photo=True)))

    def test_item_list_skips_pinned_photo_and_duplicate_ids(self):
        data = {
            "statusCode": 0,
            "itemList": [
                make_item("old-pinned", pinned=True),
                make_item("photo", photo=True),
                make_item("new-1"),
                make_item("new-1"),
                make_item("new-2"),
            ],
        }

        videos = parse_tiktok_item_list(data, limit=0)

        self.assertEqual([video.aweme_id for video in videos], ["new-1", "new-2"])

    def test_extracts_play_renditions_before_download_fallback(self):
        urls = extract_tiktok_download_urls(
            {
                "playAddr": "https://cdn.example/play.mp4",
                "PlayAddrStruct": {
                    "urlList": [
                        "https://cdn.example/play.mp4",
                        "https://backup.example/play.mp4",
                    ]
                },
                "bitrateInfo": [
                    {
                        "Bitrate": 1200000,
                        "CodecType": "h265_hvc1",
                        "PlayAddr": {"UrlList": ["https://cdn.example/h265.mp4"]},
                    },
                    {
                        "Bitrate": 900000,
                        "CodecType": "h264",
                        "PlayAddr": {"UrlList": ["https://cdn.example/h264.mp4"]},
                    },
                ],
                "downloadAddr": "https://cdn.example/watermarked.mp4",
            }
        )

        self.assertEqual(
            urls,
            [
                "https://cdn.example/play.mp4",
                "https://backup.example/play.mp4",
                "https://cdn.example/h264.mp4",
                "https://cdn.example/h265.mp4",
                "https://cdn.example/watermarked.mp4",
            ],
        )

    def test_parser_attaches_item_list_download_candidates(self):
        item = make_item("direct")
        item["video"]["playAddr"] = "https://cdn.example/direct.mp4"
        item["video"]["downloadAddr"] = "https://cdn.example/fallback.mp4"

        video = _parse_tiktok_item(item)

        self.assertEqual(video.download_url, "https://cdn.example/direct.mp4")
        self.assertEqual(
            video.download_urls,
            ["https://cdn.example/direct.mp4", "https://cdn.example/fallback.mp4"],
        )

    def test_first_party_play_redirect_is_preferred_over_signed_cdn(self):
        urls = extract_tiktok_download_urls(
            {
                "playAddr": "https://v19-webapp-prime.tiktok.com/video/file",
                "PlayAddrStruct": {
                    "urlList": [
                        "https://v16-webapp-prime.tiktok.com/video/file",
                        "https://www.tiktok.com/aweme/v1/play/?item_id=123",
                    ]
                },
            }
        )

        self.assertEqual(
            urls[0],
            "https://www.tiktok.com/aweme/v1/play/?item_id=123",
        )


class TikTokProfileMonitorStateTests(unittest.TestCase):
    def make_monitor(self, state_dir: str) -> TikTokProfileMonitor:
        return TikTokProfileMonitor(
            profile_id="1",
            unique_id="jettvn",
            sec_uid="author-sec-uid",
            gemlogin_profile_id="1",
            state_dir=state_dir,
            source_key="tiktok-source",
        )

    def test_first_run_builds_baseline_without_returning_videos(self):
        with tempfile.TemporaryDirectory() as state_dir:
            monitor = self.make_monitor(state_dir)
            monitor.fetch_latest_videos = lambda pages_to_fetch=1: [
                _parse_tiktok_item(make_item("baseline", create_time=100))
            ]

            self.assertEqual(monitor.get_new_videos(), [])
            self.assertIn("baseline", monitor.state.seen_ids)

    def test_next_run_returns_only_unseen_videos_oldest_first(self):
        with tempfile.TemporaryDirectory() as state_dir:
            monitor = self.make_monitor(state_dir)
            monitor.state.replace_seen(["seen"], 100)
            monitor.fetch_latest_videos = lambda pages_to_fetch=1: [
                _parse_tiktok_item(make_item("newer", create_time=300)),
                _parse_tiktok_item(make_item("seen", create_time=100)),
                _parse_tiktok_item(make_item("older", create_time=200)),
            ]

            videos = monitor.get_new_videos()

            self.assertEqual([video.aweme_id for video in videos], ["older", "newer"])

    def test_monitor_keeps_browser_profile_config(self):
        profile = {"browser": {"provider": "local_chromium"}}
        with tempfile.TemporaryDirectory() as state_dir:
            monitor = TikTokProfileMonitor(
                profile_id="1",
                unique_id="jettvn",
                gemlogin_profile_id="1",
                state_dir=state_dir,
                source_key="tiktok-source",
                profile_config=profile,
            )

        self.assertIs(monitor.profile_config, profile)

    def test_default_state_file_is_namespaced_for_tiktok(self):
        with tempfile.TemporaryDirectory() as state_dir:
            monitor = TikTokProfileMonitor(
                profile_id="1",
                unique_id="jettvn",
                sec_uid="author-sec-uid",
                gemlogin_profile_id="1",
                state_dir=state_dir,
            )

        self.assertIn("source_tiktok_", monitor.state.path.name)


if __name__ == "__main__":
    unittest.main()
