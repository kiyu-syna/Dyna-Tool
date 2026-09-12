import unittest
from unittest.mock import Mock, patch

from profile_automation.uploaders.facebook_uploader import (
    _click_visible_text_button,
    _facebook_publish_confirmation_signal,
    _wait_for_facebook_publish_confirmation,
    _wait_for_reel_safe,
)


class FacebookUploaderTests(unittest.TestCase):
    def test_enabled_next_button_confirms_reel_is_ready(self):
        page = Mock()
        safe_messages = Mock()
        safe_messages.count.return_value = 0
        safe_messages.or_.return_value = safe_messages
        page.get_by_text.return_value = safe_messages

        next_button = Mock()
        next_button.is_visible.return_value = True
        next_button.get_attribute.return_value = None
        buttons = Mock()
        buttons.count.return_value = 1
        buttons.nth.return_value = next_button
        page.locator.return_value = buttons

        _wait_for_reel_safe(page, timeout_seconds=0.1)

        page.locator.assert_called_with('[role="button"][aria-label="Tiếp"]')

    def test_later_text_clicks_its_button_ancestor(self):
        page = Mock()
        text_element = Mock()
        text_element.is_visible.return_value = True
        button = Mock()
        button.get_attribute.return_value = None
        ancestor = Mock()
        ancestor.count.return_value = 1
        ancestor.first = button
        text_element.locator.return_value = ancestor
        matches = Mock()
        matches.count.return_value = 1
        matches.nth.return_value = text_element
        page.get_by_text.return_value = matches

        _click_visible_text_button(page, ("Lúc khác",), timeout_ms=100)

        page.get_by_text.assert_called_once_with("Lúc khác", exact=True)
        button.scroll_into_view_if_needed.assert_called_once_with()
        button.click.assert_called_once_with(force=True)

    def test_missing_required_text_button_fails(self):
        page = Mock()
        matches = Mock()
        matches.count.return_value = 0
        page.get_by_text.return_value = matches

        with self.assertRaisesRegex(TimeoutError, "Lúc khác"):
            _click_visible_text_button(page, ("Lúc khác",), timeout_ms=1)

    def test_optional_later_button_can_be_absent(self):
        page = Mock()
        matches = Mock()
        matches.count.return_value = 0
        page.get_by_text.return_value = matches

        clicked = _click_visible_text_button(
            page,
            ("Lúc khác",),
            timeout_ms=1,
            required=False,
        )

        self.assertFalse(clicked)

    def test_publish_confirmation_accepts_visible_success_text(self):
        page = Mock()

        def get_by_text(text, exact=False):
            item = Mock()
            item.is_visible.return_value = text == "View reel"
            matches = Mock()
            matches.count.return_value = 1
            matches.nth.return_value = item
            return matches

        page.get_by_text.side_effect = get_by_text
        page.url = "https://www.facebook.com/me"

        signal = _facebook_publish_confirmation_signal(page)

        self.assertEqual(signal, 'text="View reel"')

    def test_publish_confirmation_wait_polls_until_signal_appears(self):
        page = Mock()
        with (
            patch(
                "profile_automation.uploaders.facebook_uploader._facebook_publish_confirmation_signal",
                side_effect=["", 'text="Reel published"'],
            ) as confirmation,
            patch(
                "profile_automation.uploaders.facebook_uploader.time.monotonic",
                side_effect=[10, 10],
            ),
            patch("profile_automation.uploaders.facebook_uploader.time.sleep") as sleep,
        ):
            signal = _wait_for_facebook_publish_confirmation(page)

        self.assertEqual(signal, 'text="Reel published"')
        self.assertEqual(confirmation.call_count, 2)
        sleep.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main()
