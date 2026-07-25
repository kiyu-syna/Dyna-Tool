import tempfile
import time
import unittest
from pathlib import Path

from services.browser.local_profile_setup_service import (
    LocalProfileSetupError,
    LocalProfileSetupService,
)
from services.profiles.profile_management_service import ProfileManagementService


class _FakePage:
    def __init__(self):
        self.urls = []

    def goto(self, url, **_kwargs):
        self.urls.append(url)


class _FakeContext:
    def __init__(self):
        self.pages = [_FakePage()]
        self._close_handler = None
        self.closed = False

    def new_page(self):
        page = _FakePage()
        self.pages.append(page)
        return page

    def on(self, event, handler):
        if event == "close":
            self._close_handler = handler

    def cookies(self):
        return [
            {"domain": ".facebook.com", "name": "c_user"},
            {"domain": ".tiktok.com", "name": "sessionid"},
        ]

    def close(self):
        self.closed = True
        if self._close_handler:
            self._close_handler(self)


class _FakeChromium:
    def __init__(self, context):
        self.context = context

    def launch_persistent_context(self, **kwargs):
        root = Path(kwargs["user_data_dir"])
        (root / "Default").mkdir(parents=True, exist_ok=True)
        (root / "Local State").write_text("{}", encoding="utf-8")
        (root / "Default" / "Preferences").write_text("{}", encoding="utf-8")
        return self.context


class _FakePlaywright:
    def __init__(self, context):
        self.chromium = _FakeChromium(context)
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FakeManager:
    def __init__(self, context):
        self.playwright = _FakePlaywright(context)

    def start(self):
        return self.playwright

    def __exit__(self, *_args):
        return None


class _FakeNativeProcess:
    def __init__(self, command):
        self.command = list(command)
        self.running = True
        user_data_arg = next(
            argument
            for argument in self.command
            if str(argument).startswith("--user-data-dir=")
        )
        profile_arg = next(
            argument
            for argument in self.command
            if str(argument).startswith("--profile-directory=")
        )
        root = Path(str(user_data_arg).split("=", 1)[1])
        profile_directory = str(profile_arg).split("=", 1)[1]
        (root / profile_directory).mkdir(parents=True, exist_ok=True)
        (root / "Local State").write_text("{}", encoding="utf-8")
        (root / profile_directory / "Preferences").write_text("{}", encoding="utf-8")

    def poll(self):
        return None if self.running else 0

    def terminate(self):
        self.running = False

    def wait(self, timeout=None):
        del timeout
        self.running = False
        return 0

    def kill(self):
        self.running = False


class LocalProfileSetupServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.profiles = ProfileManagementService(
            self.root / "configs",
            self.root / "state",
        )
        self.profiles.create("9", "Local Test")
        self.executable = self.root / "runtime" / "chrome.exe"
        self.executable.parent.mkdir()
        self.executable.write_bytes(b"fake-browser")

    def tearDown(self):
        self.temp.cleanup()

    def _wait_status(self, service, expected: str) -> dict:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = service.get("9") or {}
            if state.get("status") == expected:
                return state
            time.sleep(0.01)
        self.fail(f"Setup không đạt trạng thái {expected}: {service.get('9')}")

    def test_visible_login_setup_persists_local_browser_config(self):
        context = _FakeContext()
        manager = _FakeManager(context)
        service = LocalProfileSetupService(
            self.profiles,
            profile_root=self.root / "browser-profiles",
            runtime_root=self.root / "runtimes",
            playwright_factory=lambda: manager,
        )

        started = service.start("9", executable_path=str(self.executable))
        self.assertEqual(started["status"], "starting")
        self._wait_status(service, "running")
        service.finish("9")
        completed = self._wait_status(service, "completed")

        profile = self.profiles.load("9")
        self.assertEqual(profile["browser"]["provider"], "local_chromium")
        self.assertEqual(profile["browser"]["executable_path"], str(self.executable.resolve()))
        self.assertTrue(Path(profile["browser"]["user_data_dir"]).is_dir())
        self.assertTrue(completed["sessions"]["facebook"])
        self.assertTrue(completed["sessions"]["tiktok"])
        self.assertTrue(context.closed)
        self.assertTrue(manager.playwright.stopped)

    def test_existing_local_profile_is_not_overwritten(self):
        profile = self.profiles.load("9")
        existing = self.root / "existing-profile"
        existing.mkdir()
        profile["browser"] = {
            "provider": "local_chromium",
            "user_data_dir": str(existing),
            "executable_path": str(self.executable),
        }
        self.profiles.save("9", profile)
        service = LocalProfileSetupService(
            self.profiles,
            profile_root=self.root / "browser-profiles",
        )

        with self.assertRaisesRegex(LocalProfileSetupError, "đã có Local Chromium"):
            service.start("9", executable_path=str(self.executable))

    def test_existing_local_profile_can_be_opened_for_login_again(self):
        user_data = self.root / "existing-profile"
        (user_data / "Default").mkdir(parents=True)
        (user_data / "Local State").write_text(
            '{"autofill":{"states_data_dir":'
            '"C:\\\\Users\\\\Tester\\\\.gemlogin\\\\profile\\\\profiles\\\\existing-profile\\\\AutofillStates"}}',
            encoding="utf-8",
        )
        (user_data / "Default" / "Preferences").write_text("{}", encoding="utf-8")
        profile = self.profiles.load("9")
        profile["browser"] = {
            "provider": "local_chromium",
            "user_data_dir": str(user_data),
            "executable_path": str(self.executable),
            "profile_directory": "Default",
            "headless": True,
            "background": True,
        }
        self.profiles.save("9", profile)
        manager = _FakeManager(_FakeContext())
        service = LocalProfileSetupService(
            self.profiles,
            profile_root=self.root / "browser-profiles",
            playwright_factory=lambda: manager,
        )

        started = service.open_existing("9")
        self.assertEqual(started["mode"], "existing")
        self._wait_status(service, "running")
        service.finish("9")
        self._wait_status(service, "completed")

        saved = self.profiles.load("9")
        self.assertTrue(saved["browser"]["headless"])
        self.assertEqual(saved["browser"]["user_data_dir"], str(user_data.resolve()))
        self.assertEqual(len(list(user_data.glob("Local State.dyna-backup-*.json"))), 1)

    def test_native_login_uses_stock_browser_without_playwright_flags(self):
        launched = []

        def launch(command, **_kwargs):
            process = _FakeNativeProcess(command)
            launched.append(process)
            return process

        service = LocalProfileSetupService(
            self.profiles,
            profile_root=self.root / "browser-profiles",
            runtime_root=self.root / "runtimes",
            native_login=True,
            native_launcher=launch,
        )

        started = service.start("9", executable_path=str(self.executable))
        self.assertEqual(started["login_mode"], "native")
        running = self._wait_status(service, "running")
        self.assertEqual(running["fingerprint_mode"], "system")
        command = launched[0].command
        self.assertIn("https://studio.youtube.com/", command)
        self.assertFalse(
            any("--remote-debugging" in str(argument) for argument in command)
        )
        service.finish("9")
        completed = self._wait_status(service, "completed")

        saved = self.profiles.load("9")
        self.assertEqual(saved["browser"]["login_mode"], "native")
        self.assertEqual(saved["browser"]["fingerprint_mode"], "system")
        self.assertEqual(completed["browser_name"], "Google Chrome")
        self.assertFalse(launched[0].running)


if __name__ == "__main__":
    unittest.main()
