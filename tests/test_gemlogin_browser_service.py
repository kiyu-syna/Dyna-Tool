import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from services import gemlogin_browser_service as service


class FakePage:
    def __init__(self):
        self.waited = False

    def wait_for_load_state(self, state, timeout):
        self.waited = (state, timeout)


class FakePageInfo:
    def __init__(self, page):
        self.value = page

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeContext:
    def __init__(self, page):
        self.page = page
        self.timeout = None

    def expect_page(self, timeout):
        self.timeout = timeout
        return FakePageInfo(self.page)


class FakeSession:
    def __init__(self):
        self.calls = []
        self.detached = False

    def send(self, method, params):
        self.calls.append((method, params))
        return {"targetId": "target-1"}

    def detach(self):
        self.detached = True


class FakeBrowser:
    def __init__(self, session):
        self.session = session

    def new_browser_cdp_session(self):
        return self.session


class GemLoginBrowserServiceTests(unittest.TestCase):
    def setUp(self):
        service._DEBUG_ADDRESS_CACHE.clear()
        service._PROFILE_HEALTH.clear()
        service._PLAYWRIGHT_PATCH_CHECKED = False

    def test_playwright_patch_detaches_shared_worker_before_context_assertion(self):
        vulnerable_source = (
            "before\n"
            + service._SHARED_WORKER_VULNERABLE_CODE
            + "\nafter\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_path = Path(temp_dir) / "coreBundle.js"
            bundle_path.write_text(vulnerable_source, encoding="utf-8")

            patched = service._patch_playwright_shared_worker_crash(bundle_path)
            source = bundle_path.read_text(encoding="utf-8")

            self.assertTrue(patched)
            self.assertIn(service._SHARED_WORKER_PATCH_MARKER, source)
            self.assertLess(
                source.index('targetInfo.type === "shared_worker"'),
                source.index("assert(targetInfo.browserContextId"),
            )
            self.assertFalse(service._patch_playwright_shared_worker_crash(bundle_path))

    def test_error_classifier_distinguishes_cdp_from_permanent_failure(self):
        self.assertEqual(
            service.classify_automation_error(
                "BrowserType.connect_over_cdp: connect ECONNREFUSED 127.0.0.1:9222"
            ),
            "cdp",
        )
        self.assertEqual(
            service.classify_automation_error("Extension không tìm được video"),
            "permanent",
        )

    @patch("services.gemlogin_browser_service.restart_gemlogin_profile")
    def test_safe_operation_is_repeated_after_cdp_recovery(self, restart_profile):
        operation = Mock(
            side_effect=[
                RuntimeError("Connection closed while reading from the driver"),
                "ok",
            ]
        )

        result = service.run_with_gemlogin_recovery(
            operation,
            "2",
            "http://127.0.0.1:1010",
            attempts=2,
            retry_delay_seconds=0,
        )

        self.assertEqual(result, "ok")
        self.assertEqual(operation.call_count, 2)
        restart_profile.assert_called_once_with(
            "2",
            "http://127.0.0.1:1010",
            restart_delay_seconds=0.0,
        )

    @patch("services.gemlogin_browser_service.restart_gemlogin_profile")
    def test_permanent_failure_is_not_repeated(self, restart_profile):
        operation = Mock(side_effect=RuntimeError("Video không tồn tại"))

        with self.assertRaisesRegex(RuntimeError, "không tồn tại"):
            service.run_with_gemlogin_recovery(
                operation,
                "2",
                "http://127.0.0.1:1010",
                attempts=3,
                retry_delay_seconds=0,
            )

        operation.assert_called_once_with()
        restart_profile.assert_not_called()

    @patch("services.gemlogin_browser_service._is_cdp_reachable", return_value=True)
    @patch("services.gemlogin_browser_service.requests.get")
    def test_live_cached_address_does_not_restart_profile(self, get, _reachable):
        service._DEBUG_ADDRESS_CACHE["2"] = "127.0.0.1:9222"

        address = service.get_gemlogin_debug_address("2", "http://127.0.0.1:1010")

        self.assertEqual(address, "127.0.0.1:9222")
        get.assert_not_called()

    @patch("services.gemlogin_browser_service.requests.get")
    def test_start_result_is_cached(self, get):
        response = Mock()
        response.json.return_value = {
            "data": {"remote_debugging_address": "127.0.0.1:9333"}
        }
        get.return_value = response

        address = service.get_gemlogin_debug_address("2", "http://127.0.0.1:1010")

        self.assertEqual(address, "127.0.0.1:9333")
        self.assertEqual(service._DEBUG_ADDRESS_CACHE["2"], "127.0.0.1:9333")
        response.raise_for_status.assert_called_once_with()

    def test_page_is_created_as_background_target(self):
        page = FakePage()
        session = FakeSession()

        result = service.create_background_page(
            FakeBrowser(session),
            FakeContext(page),
            timeout_ms=1234,
        )

        self.assertIs(result, page)
        self.assertEqual(
            session.calls,
            [("Target.createTarget", {"url": "about:blank", "background": True})],
        )
        self.assertEqual(page.waited, ("commit", 1234))
        self.assertTrue(session.detached)

    @patch("services.gemlogin_browser_service.sync_playwright")
    def test_connection_uses_fresh_driver_when_first_attempt_fails(self, sync_playwright):
        first_manager = Mock()
        first_manager.start.return_value.chromium.connect_over_cdp.side_effect = RuntimeError(
            "driver closed"
        )
        second_browser = Mock()
        second_manager = Mock()
        second_manager.start.return_value.chromium.connect_over_cdp.return_value = second_browser
        sync_playwright.side_effect = [first_manager, second_manager]

        with service.connected_gemlogin_browser(
            "127.0.0.1:9333",
            retry_delay_seconds=0,
        ) as browser:
            self.assertIs(browser, second_browser)

        self.assertEqual(sync_playwright.call_count, 2)
        first_manager.start.return_value.stop.assert_called_once_with()
        second_manager.start.return_value.stop.assert_called_once_with()
        second_manager.start.return_value.chromium.connect_over_cdp.assert_called_once_with(
            "http://127.0.0.1:9333"
        )

    @patch("services.gemlogin_browser_service.sync_playwright")
    def test_browser_operations_are_serialized(self, sync_playwright):
        managers = [Mock(), Mock()]
        for manager in managers:
            manager.start.return_value.chromium.connect_over_cdp.return_value = Mock()
        sync_playwright.side_effect = managers

        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        errors = []

        def run_first():
            try:
                with service.connected_gemlogin_browser(
                    "127.0.0.1:9333",
                    attempts=1,
                ):
                    first_entered.set()
                    release_first.wait(timeout=2)
            except Exception as exc:
                errors.append(exc)

        def run_second():
            try:
                with service.connected_gemlogin_browser(
                    "127.0.0.1:9333",
                    attempts=1,
                ):
                    second_entered.set()
            except Exception as exc:
                errors.append(exc)

        first_thread = threading.Thread(target=run_first)
        second_thread = threading.Thread(target=run_second)
        first_thread.start()
        self.assertTrue(first_entered.wait(timeout=1))
        second_thread.start()

        self.assertFalse(second_entered.wait(timeout=0.1))
        self.assertEqual(sync_playwright.call_count, 1)

        release_first.set()
        first_thread.join(timeout=2)
        second_thread.join(timeout=2)

        self.assertFalse(errors)
        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertTrue(second_entered.is_set())
        self.assertEqual(sync_playwright.call_count, 2)

    def test_dead_endpoint_is_restarted_before_playwright_connects(self):
        manager = Mock()
        browser = Mock()
        manager.start.return_value.chromium.connect_over_cdp.return_value = browser

        with (
            patch(
                "services.gemlogin_browser_service.get_gemlogin_debug_address",
                return_value="127.0.0.1:9222",
            ),
            patch(
                "services.gemlogin_browser_service.restart_gemlogin_profile",
                return_value="127.0.0.1:9444",
            ) as restart_profile,
            patch(
                "services.gemlogin_browser_service._wait_for_cdp",
                side_effect=[False, True],
            ),
            patch(
                "services.gemlogin_browser_service._is_cdp_reachable",
                return_value=False,
            ),
            patch(
                "services.gemlogin_browser_service.sync_playwright",
                return_value=manager,
            ) as sync_playwright,
        ):
            with service.connected_gemlogin_profile(
                "2",
                "http://127.0.0.1:1010",
                retry_delay_seconds=0,
                cdp_ready_timeout_seconds=0,
            ) as connected_browser:
                self.assertIs(connected_browser, browser)

        restart_profile.assert_called_once_with(
            "2",
            "http://127.0.0.1:1010",
            restart_delay_seconds=0.0,
        )
        sync_playwright.assert_called_once_with()
        manager.start.return_value.chromium.connect_over_cdp.assert_called_once_with(
            "http://127.0.0.1:9444"
        )

    def test_econnrefused_restarts_profile_and_uses_new_endpoint(self):
        first_manager = Mock()
        first_manager.start.return_value.chromium.connect_over_cdp.side_effect = RuntimeError(
            "connect ECONNREFUSED 127.0.0.1:9222"
        )
        second_manager = Mock()
        second_browser = Mock()
        second_manager.start.return_value.chromium.connect_over_cdp.return_value = second_browser

        with (
            patch(
                "services.gemlogin_browser_service.get_gemlogin_debug_address",
                return_value="127.0.0.1:9222",
            ),
            patch(
                "services.gemlogin_browser_service.restart_gemlogin_profile",
                return_value="127.0.0.1:9444",
            ) as restart_profile,
            patch("services.gemlogin_browser_service._wait_for_cdp", return_value=True),
            patch(
                "services.gemlogin_browser_service._is_cdp_reachable",
                return_value=False,
            ),
            patch(
                "services.gemlogin_browser_service.sync_playwright",
                side_effect=[first_manager, second_manager],
            ),
        ):
            with service.connected_gemlogin_profile(
                "2",
                "http://127.0.0.1:1010",
                retry_delay_seconds=0,
            ) as connected_browser:
                self.assertIs(connected_browser, second_browser)

        restart_profile.assert_called_once()
        first_manager.start.return_value.chromium.connect_over_cdp.assert_called_once_with(
            "http://127.0.0.1:9222"
        )
        second_manager.start.return_value.chromium.connect_over_cdp.assert_called_once_with(
            "http://127.0.0.1:9444"
        )

    def test_live_endpoint_retries_driver_without_restarting_profile(self):
        first_manager = Mock()
        first_manager.start.return_value.chromium.connect_over_cdp.side_effect = RuntimeError(
            "Connection closed while reading from the driver"
        )
        second_manager = Mock()
        second_browser = Mock()
        second_manager.start.return_value.chromium.connect_over_cdp.return_value = second_browser

        with (
            patch(
                "services.gemlogin_browser_service.get_gemlogin_debug_address",
                return_value="127.0.0.1:9222",
            ),
            patch(
                "services.gemlogin_browser_service.restart_gemlogin_profile"
            ) as restart_profile,
            patch("services.gemlogin_browser_service._wait_for_cdp", return_value=True),
            patch(
                "services.gemlogin_browser_service._is_cdp_reachable",
                return_value=True,
            ),
            patch(
                "services.gemlogin_browser_service.sync_playwright",
                side_effect=[first_manager, second_manager],
            ),
        ):
            with service.connected_gemlogin_profile(
                "2",
                "http://127.0.0.1:1010",
                retry_delay_seconds=0,
            ) as connected_browser:
                self.assertIs(connected_browser, second_browser)

        restart_profile.assert_not_called()
        for manager in (first_manager, second_manager):
            manager.start.return_value.chromium.connect_over_cdp.assert_called_once_with(
                "http://127.0.0.1:9222"
            )

    @patch("services.gemlogin_browser_service.requests.get")
    def test_close_profile_uses_close_endpoint_and_clears_cache(self, get):
        response = Mock()
        response.json.return_value = {"success": True}
        get.return_value = response
        service._DEBUG_ADDRESS_CACHE["2"] = "127.0.0.1:9333"

        service.close_gemlogin_profile("2", "http://127.0.0.1:1010")

        get.assert_called_once_with(
            "http://127.0.0.1:1010/api/profiles/close/2",
            timeout=30,
        )
        response.raise_for_status.assert_called_once_with()
        self.assertNotIn("2", service._DEBUG_ADDRESS_CACHE)


if __name__ == "__main__":
    unittest.main()
