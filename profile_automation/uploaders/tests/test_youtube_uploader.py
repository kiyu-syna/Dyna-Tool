import unittest
from unittest.mock import Mock, patch

from profile_automation.uploaders.youtube_uploader import (
    YOUTUBE_CHECK_COMPLETE,
    YOUTUBE_CHECK_COPYRIGHT,
    YOUTUBE_CHECK_PENDING,
    _classify_youtube_check_status,
    _record_page_error_before_close,
    _select_public_visibility,
    _wait_for_youtube_publish_confirmation,
    _youtube_publish_confirmation_signal,
    _youtube_upload_textbox,
)


class YouTubeUploaderTests(unittest.TestCase):
    def test_copyright_claim_is_classified_for_cancellation(self):
        result = _classify_youtube_check_status(
            ["Đã kiểm tra xong. Phát hiện nội dung được xác nhận quyền sở hữu."]
        )

        self.assertEqual(result, YOUTUBE_CHECK_COPYRIGHT)

    def test_completed_check_without_copyright_can_continue(self):
        result = _classify_youtube_check_status(
            ["Đã kiểm tra xong. Không phát hiện vấn đề nào."]
        )

        self.assertEqual(result, YOUTUBE_CHECK_COMPLETE)

    def test_upload_progress_is_not_mistaken_for_completed_check(self):
        result = _classify_youtube_check_status(["Đang kiểm tra 5% ... Còn 10 phút"])

        self.assertEqual(result, YOUTUBE_CHECK_PENDING)

    def test_publish_confirmation_accepts_visible_success_text(self):
        page = Mock()

        def get_by_text(text, exact=False):
            item = Mock()
            item.is_visible.return_value = text == "Video published"
            matches = Mock()
            matches.count.return_value = 1
            matches.nth.return_value = item
            return matches

        page.get_by_text.side_effect = get_by_text
        page.url = "https://studio.youtube.com/channel/test/videos/upload"

        signal = _youtube_publish_confirmation_signal(page)

        self.assertEqual(signal, 'text="Video published"')

    def test_publish_confirmation_accepts_current_vietnamese_success_heading(self):
        page = Mock()

        def get_by_text(text, exact=False):
            item = Mock()
            item.is_visible.return_value = text == "Đã đăng video"
            matches = Mock()
            matches.count.return_value = 1
            matches.nth.return_value = item
            return matches

        page.get_by_text.side_effect = get_by_text
        page.url = "https://studio.youtube.com/channel/test/videos/upload"

        signal = _youtube_publish_confirmation_signal(page)

        self.assertEqual(signal, 'text="Đã đăng video"')

    def test_publish_confirmation_wait_polls_until_signal_appears(self):
        page = Mock()
        with (
            patch(
                "profile_automation.uploaders.youtube_uploader._youtube_publish_confirmation_signal",
                side_effect=["", 'text="Đã xuất bản video"'],
            ) as confirmation,
            patch(
                "profile_automation.uploaders.youtube_uploader.time.monotonic",
                side_effect=[10, 10],
            ),
            patch("profile_automation.uploaders.youtube_uploader.time.sleep") as sleep,
        ):
            signal = _wait_for_youtube_publish_confirmation(page)

        self.assertEqual(signal, 'text="Đã xuất bản video"')
        self.assertEqual(confirmation.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_upload_textbox_is_scoped_to_the_visible_upload_dialog(self):
        page = Mock()
        matches = Mock()
        expected = Mock()
        matches.last = expected
        page.locator.return_value = matches

        result = _youtube_upload_textbox(page, required=True)

        self.assertIs(result, expected)
        page.locator.assert_called_once_with(
            'ytcp-uploads-dialog:visible div#textbox[contenteditable="true"][aria-required="true"]'
        )

    @patch("profile_automation.uploaders.youtube_uploader.record_browser_diagnostic")
    def test_page_error_is_recorded_before_cleanup(self, record):
        state = {"recorded": False}
        page = Mock()

        with self.assertRaisesRegex(RuntimeError, "textbox timeout"):
            with _record_page_error_before_close(
                page,
                profile_id="3",
                video_id="video-1",
                url="https://studio.youtube.com/upload",
                last_response={"status": 200},
                state=state,
            ):
                raise RuntimeError("textbox timeout")

        self.assertTrue(state["recorded"])
        self.assertIs(record.call_args.kwargs["page"], page)
        self.assertEqual(str(record.call_args.kwargs["error"]), "textbox timeout")

    @staticmethod
    def _public_radio(initially_checked: bool):
        state = {"checked": initially_checked}
        container = Mock()
        container.click.side_effect = lambda **_kwargs: state.update(checked=True)
        radio = Mock()
        radio.is_visible.return_value = True
        radio.get_attribute.side_effect = (
            lambda name: "true" if name == "aria-checked" and state["checked"] else "false"
        )
        radio.locator.return_value = container
        radios = Mock()
        radios.count.return_value = 1
        radios.nth.return_value = radio
        return radio, container, radios

    def test_selects_exact_public_radio_and_confirms_checked_state(self):
        page = Mock()
        radio, container, radios = self._public_radio(initially_checked=False)
        page.locator.return_value = radios

        _select_public_visibility(page, timeout_seconds=1)

        page.locator.assert_called_once_with(
            'tp-yt-paper-radio-button[name="PUBLIC"][aria-disabled="false"]'
        )
        radio.locator.assert_called_once_with("#radioContainer")
        container.click.assert_called_once_with(force=True)

    def test_already_public_does_not_click_again(self):
        page = Mock()
        _radio, container, radios = self._public_radio(initially_checked=True)
        page.locator.return_value = radios

        _select_public_visibility(page, timeout_seconds=1)

        container.click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
