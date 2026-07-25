import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

import services.browser.local_chromium_browser_service as service


class _FakeContext:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, context):
        self.context = context
        self.calls = []

    def launch_persistent_context(self, **kwargs):
        self.calls.append(kwargs)
        return self.context


class _FakePlaywright:
    def __init__(self, context):
        self.chromium = _FakeChromium(context)
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FakeManager:
    def __init__(self, playwright):
        self.playwright = playwright
        self.started = 0

    def start(self):
        self.started += 1
        return self.playwright

    def __exit__(self, *_args):
        return None


class LocalChromiumBrowserServiceTests(unittest.TestCase):
    def _profile_copy(self, root: Path) -> tuple[dict, Path, Path]:
        user_data_dir = root / "profile-copy"
        default_dir = user_data_dir / "Default"
        default_dir.mkdir(parents=True)
        (user_data_dir / "Local State").write_text("{}", encoding="utf-8")
        (default_dir / "Preferences").write_text("{}", encoding="utf-8")
        executable = root / "chrome.exe"
        executable.write_bytes(b"fake")
        profile = {
            "browser": {
                "provider": "local_chromium",
                "user_data_dir": str(user_data_dir),
                "executable_path": str(executable),
                "profile_directory": "Default",
            }
        }
        return profile, user_data_dir, executable

    def test_resolve_accepts_complete_copied_user_data_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            profile, user_data_dir, executable = self._profile_copy(Path(directory))

            config = service.resolve_local_chromium_config(profile)

            self.assertEqual(config.user_data_dir, user_data_dir.resolve())
            self.assertEqual(config.executable_path, executable.resolve())

    def test_launch_options_include_per_profile_authenticated_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            profile, _, _ = self._profile_copy(Path(directory))
            profile["browser"]["proxy"] = {
                "enabled": True,
                "server": "http://proxy.example:8080",
                "username": "proxy-user",
                "password": "proxy-password",
                "bypass": "localhost,127.0.0.1",
            }

            config = service.resolve_local_chromium_config(profile)
            options = service.local_chromium_launch_options(config)

            self.assertEqual(
                options["proxy"],
                {
                    "server": "http://proxy.example:8080",
                    "username": "proxy-user",
                    "password": "proxy-password",
                    "bypass": "localhost,127.0.0.1",
                },
            )

    def test_safe_mode_keeps_proxy_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            profile, _, _ = self._profile_copy(Path(directory))
            profile["browser"]["proxy"] = {
                "enabled": True,
                "server": "socks5://proxy.example:1080",
            }

            config = service.resolve_local_chromium_config(profile)
            options = service.local_chromium_launch_options(config, safe_mode=True)

            self.assertEqual(
                options["proxy"],
                {"server": "socks5://proxy.example:1080"},
            )

    def test_resource_saving_mode_limits_cpu_without_disabling_gpu(self):
        with tempfile.TemporaryDirectory() as directory:
            profile, _, _ = self._profile_copy(Path(directory))
            config = service.resolve_local_chromium_config(profile)

            options = service.local_chromium_launch_options(
                config,
                resource_saving=True,
            )

            self.assertIn("--disable-extensions", options["args"])
            self.assertIn("--mute-audio", options["args"])
            self.assertIn("--renderer-process-limit=2", options["args"])
            self.assertIn("--force-prefers-reduced-motion", options["args"])
            self.assertNotIn("--disable-gpu", options["args"])
            self.assertNotIn("ignore_default_args", options)

    def test_crash_output_is_summarized_in_vietnamese(self):
        noisy_error = """BrowserType.launch_persistent_context: Target page, context or browser has been closed
Browser logs:
<launched> pid=43536
<process did exit: exitCode=2147483651, signal=null>"""

        summary = service.summarize_local_chromium_error(noisy_error)

        self.assertIn("0x80000003", summary)
        self.assertIn("Bản sao profile", summary)
        self.assertNotIn("Browser logs", summary)

    def test_original_gemlogin_profile_is_refused(self):
        profile = {
            "browser": {
                "provider": "local_chromium",
                "user_data_dir": r"C:\Users\Tester\.gemlogin\profile\profiles\1",
                "executable_path": r"C:\browser\chrome.exe",
            }
        }

        with self.assertRaisesRegex(service.LocalChromiumError, "profile gốc"):
            service.resolve_local_chromium_config(profile)

    def test_gemlogin_copy_refuses_mismatched_browser_major(self):
        with tempfile.TemporaryDirectory() as directory:
            profile, user_data_dir, _ = self._profile_copy(Path(directory))
            (user_data_dir / "key.txt").write_text("gemlogin", encoding="ascii")
            (user_data_dir / "Last Browser").write_bytes(
                r"C:\Users\Tester\.gemlogin\browser\141\Chrome-bin\chrome.exe".encode(
                    "utf-16-le"
                )
            )
            (user_data_dir / "Last Version").write_text(
                "141.0.7390.125",
                encoding="ascii",
            )

            with (
                patch.object(service, "_executable_major_version", return_value=148),
                self.assertRaisesRegex(service.LocalChromiumError, "yêu cầu Iron/Chromium 141"),
            ):
                service.resolve_local_chromium_config(profile)

    def test_copy_with_original_absolute_reference_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            profile, user_data_dir, _ = self._profile_copy(Path(directory))
            (user_data_dir / "Local State").write_text(
                json.dumps(
                    {
                        "autofill": {
                            "states_data_dir": r"C:\Users\Tester\.gemlogin\profile\profiles\1\AutofillStates"
                        }
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(service.LocalChromiumError, "tham chiếu"):
                service.resolve_local_chromium_config(profile)

    def test_prepare_copy_rewrites_only_matching_original_reference_with_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            profile, user_data_dir, _ = self._profile_copy(Path(directory))
            original_reference = (
                r"C:\Users\Tester\.gemlogin\profile\profiles\profile-copy\AutofillStates"
            )
            (user_data_dir / "Local State").write_text(
                json.dumps({"autofill": {"states_data_dir": original_reference}}),
                encoding="utf-8",
            )

            result = service.prepare_local_chromium_copy(profile)

            self.assertEqual(result["changed"], 1)
            self.assertTrue(Path(result["backup_path"]).is_file())
            rewritten = json.loads((user_data_dir / "Local State").read_text(encoding="utf-8"))
            self.assertEqual(
                rewritten["autofill"]["states_data_dir"],
                str(user_data_dir.resolve() / "AutofillStates"),
            )
            service.resolve_local_chromium_config(profile)

    def test_stale_lock_is_quarantined_and_profile_becomes_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            profile, user_data_dir, _ = self._profile_copy(Path(directory))
            stale_lock = user_data_dir / "SingletonLock"
            stale_lock.write_text("stale", encoding="utf-8")

            result = service.inspect_local_chromium_profile(
                profile,
                repair_stale_locks=True,
            )

            self.assertTrue(result["ready"])
            self.assertEqual(result["status"], "recovered")
            self.assertTrue(result["repaired"])
            self.assertFalse(stale_lock.exists())
            self.assertTrue((Path(result["recovery_path"]) / "SingletonLock").is_file())

    def test_live_browser_process_is_never_treated_as_a_stale_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            profile, user_data_dir, _ = self._profile_copy(Path(directory))
            stale_lock = user_data_dir / "SingletonLock"
            stale_lock.write_text("owned", encoding="utf-8")
            process = Mock(pid=4321)

            with patch.object(service, "_browser_processes_using", return_value=[process]):
                result = service.inspect_local_chromium_profile(
                    profile,
                    repair_stale_locks=True,
                )

            self.assertFalse(result["ready"])
            self.assertEqual(result["status"], "in_use")
            self.assertEqual(result["pids"], [4321])
            self.assertTrue(stale_lock.is_file())

    def test_launch_crash_retries_in_safe_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            profile, _, _ = self._profile_copy(Path(directory))
            profile["browser"].update({"headless": True, "background": True})
            first_chromium = Mock()
            first_chromium.launch_persistent_context.side_effect = RuntimeError(
                "Target page, context or browser has been closed; exitCode=2147483651"
            )
            first_playwright = Mock(chromium=first_chromium)
            first_manager = Mock()
            first_manager.start.return_value = first_playwright

            context = _FakeContext()
            second_chromium = Mock()
            second_chromium.launch_persistent_context.return_value = context
            second_playwright = Mock(chromium=second_chromium)
            second_manager = Mock()
            second_manager.start.return_value = second_playwright

            with (
                patch.object(service, "_cross_process_profile_lock", side_effect=lambda *_: nullcontext()),
                patch.object(service, "sync_playwright", side_effect=[first_manager, second_manager]),
                service.connected_local_chromium_profile(profile, attempts=2, retry_delay_seconds=0),
            ):
                pass

            safe_options = second_chromium.launch_persistent_context.call_args.kwargs
            self.assertFalse(safe_options["headless"])
            self.assertNotIn("ignore_default_args", safe_options)
            self.assertIn("--disable-extensions", safe_options["args"])
            self.assertIn("--disable-gpu", safe_options["args"])
            self.assertEqual(service.get_local_chromium_health(profile)["status"], "recovered")

    def test_caller_error_is_not_mistaken_for_launch_failure_or_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            profile, _, _ = self._profile_copy(Path(directory))
            config = service.resolve_local_chromium_config(profile)
            context = _FakeContext()
            playwright = _FakePlaywright(context)
            manager = _FakeManager(playwright)

            with (
                patch.object(service, "resolve_local_chromium_config", return_value=config),
                patch.object(service, "_cross_process_profile_lock", side_effect=lambda *_: nullcontext()),
                patch.object(service, "ensure_local_profile_unlocked"),
                patch.object(service, "sync_playwright", return_value=manager),
            ):
                with self.assertRaisesRegex(RuntimeError, "inside operation"):
                    with service.connected_local_chromium_profile(profile, attempts=2):
                        raise RuntimeError("inside operation")

            self.assertEqual(manager.started, 1)
            self.assertTrue(context.closed)
            self.assertTrue(playwright.stopped)


if __name__ == "__main__":
    unittest.main()
