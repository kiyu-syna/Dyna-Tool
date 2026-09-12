import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from profile_automation.uploaders import tiktok_uploader


class TikTokUploaderLoggingTests(unittest.TestCase):
    def test_confirmation_signal_accepts_visible_success_text(self):
        page = Mock()

        def get_by_text(text, exact=False):
            locator = Mock()
            locator.first.is_visible.return_value = text == "Manage posts"
            return locator

        page.get_by_text.side_effect = get_by_text
        page.url = tiktok_uploader.UPLOAD_URL

        signal = tiktok_uploader._publish_confirmation_signal(page)

        self.assertEqual(signal, 'text="Manage posts"')

    def test_confirmation_signal_accepts_content_page_url(self):
        page = Mock()
        page.get_by_text.side_effect = RuntimeError("not visible")
        page.url = "https://www.tiktok.com/tiktokstudio/content"

        signal = tiktok_uploader._publish_confirmation_signal(page)

        self.assertEqual(signal, 'url="https://www.tiktok.com/tiktokstudio/content"')

    def test_confirmation_wait_polls_until_signal_appears(self):
        page = Mock()
        with (
            patch.object(
                tiktok_uploader,
                "_publish_confirmation_signal",
                side_effect=["", 'text="Video uploaded"'],
            ) as confirmation,
            patch.object(tiktok_uploader.time, "monotonic", side_effect=[10, 10]),
            patch.object(tiktok_uploader.time, "sleep") as sleep,
        ):
            signal = tiktok_uploader._wait_for_publish_confirmation(
                page,
                timeout_seconds=45,
            )

        self.assertEqual(signal, 'text="Video uploaded"')
        self.assertEqual(confirmation.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_post_click_failure_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            video_path = Path(directory) / "video.mp4"
            video_path.write_bytes(b"video")
            video = SimpleNamespace(aweme_id="video-1", desc="caption")
            profile = {
                "id": "3",
                "tiktok": {"gemlogin_profile_id": "3", "enabled": True},
                "browser": {"provider": "local_chromium"},
            }
            context = Mock()
            browser = Mock(contexts=[context])
            page = Mock()
            page.url = tiktok_uploader.UPLOAD_URL
            page.is_closed.return_value = False
            caption_box = Mock()
            page.wait_for_selector.return_value = caption_box
            content_check = Mock()
            content_check.first.is_visible.return_value = True
            page.get_by_text.return_value.or_.return_value = content_check
            post_button = Mock()
            post_button.is_enabled.return_value = True
            post_button.click.side_effect = RuntimeError("post click failed")
            page.locator.return_value.last = post_button

            with (
                patch.object(
                    tiktok_uploader,
                    "connected_gemlogin_profile",
                    return_value=nullcontext(browser),
                ),
                patch.object(tiktok_uploader, "create_background_page", return_value=page),
                patch.object(tiktok_uploader, "attach_response_trace", return_value={}),
                patch.object(tiktok_uploader, "is_captcha_present", return_value=False),
                patch.object(tiktok_uploader, "set_video_file_background"),
                patch.object(tiktok_uploader, "_type_caption_with_tiktok_hashtags"),
                patch.object(tiktok_uploader, "record_browser_diagnostic"),
                patch.object(tiktok_uploader, "browser_profile_label", return_value="Chrome 3"),
                patch.object(tiktok_uploader.time, "sleep"),
            ):
                result = tiktok_uploader.TikTokUploader().upload(
                    str(video_path),
                    video,
                    profile,
                )

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
