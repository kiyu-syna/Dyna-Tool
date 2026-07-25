import unittest
from unittest.mock import Mock

from profile_automation.uploaders.youtube_uploader import (
    YOUTUBE_CHECK_COMPLETE,
    YOUTUBE_CHECK_COPYRIGHT,
    YOUTUBE_CHECK_PENDING,
    _classify_youtube_check_status,
    _select_public_visibility,
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
