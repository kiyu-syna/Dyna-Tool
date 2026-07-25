import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

import services.browser.browser_profile_service as service


class BrowserProfileServiceTests(unittest.TestCase):
    def test_profile_without_browser_defaults_to_local_chromium(self):
        self.assertEqual(service.browser_provider({"id": "2"}), "local_chromium")

    def test_local_profile_routes_to_persistent_context_service(self):
        browser = object()

        @contextmanager
        def fake_local(*_args, **_kwargs):
            yield browser

        profile = {"browser": {"provider": "local_chromium"}}
        with patch.object(service, "connected_local_chromium_profile", fake_local):
            with service.connected_browser_profile(
                "2", "http://127.0.0.1:1010", profile_config=profile
            ) as connected:
                self.assertIs(connected, browser)

    def test_background_page_uses_context_for_local_browser(self):
        browser = type("LocalBrowser", (), {"is_local_persistent": True})()
        context = object()
        page = object()
        with patch.object(service, "create_local_background_page", return_value=page) as create:
            self.assertIs(service.create_background_page(browser, context), page)
        create.assert_called_once_with(context)

    def test_lightweight_scan_page_blocks_heavy_resources_and_reduces_motion(self):
        page = Mock()
        page.route = Mock()

        configured = service.configure_lightweight_scan_page(page)

        self.assertTrue(configured)
        route_handler = page.route.call_args.args[1]
        image_route = Mock()
        route_handler(
            image_route,
            type("Request", (), {"resource_type": "image"})(),
        )
        image_route.abort.assert_called_once_with()
        image_route.continue_.assert_not_called()

        script_route = Mock()
        route_handler(
            script_route,
            type("Request", (), {"resource_type": "script"})(),
        )
        script_route.continue_.assert_called_once_with()
        page.emulate_media.assert_called_once_with(reduced_motion="reduce")
        page.add_init_script.assert_called_once()

    def test_reuse_scope_opens_local_browser_once_for_a_batch(self):
        browser = object()
        opens = []
        closes = []

        @contextmanager
        def fake_local(*_args, **_kwargs):
            opens.append(True)
            try:
                yield browser
            finally:
                closes.append(True)

        profile = {"browser": {"provider": "local_chromium", "user_data_dir": "profile-a"}}
        with patch.object(service, "connected_local_chromium_profile", fake_local):
            with service.browser_reuse_scope():
                with service.connected_browser_profile("2", "unused", profile_config=profile) as first:
                    self.assertIs(first, browser)
                with service.connected_browser_profile("2", "unused", profile_config=profile) as second:
                    self.assertIs(second, browser)
                self.assertEqual(len(closes), 0)

        self.assertEqual(len(opens), 1)
        self.assertEqual(len(closes), 1)


if __name__ == "__main__":
    unittest.main()
