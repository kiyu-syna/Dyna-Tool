import os
import unittest
from unittest.mock import Mock, patch

import core.config as config
import main
from services import telegram_service


class RuntimeBootstrapTests(unittest.TestCase):
    @patch("main.subprocess.Popen")
    @patch("main.os.path.exists", return_value=True)
    def test_default_desktop_launcher_uses_built_electron_app(self, _exists, popen):
        process = Mock()
        process.wait.return_value = 0
        popen.return_value = process

        exit_code = main.launch_desktop_app()

        self.assertEqual(exit_code, 0)
        command = popen.call_args.args[0]
        self.assertTrue(command[0].endswith("electron.exe"))
        self.assertTrue(command[1].endswith("desktop"))
        self.assertEqual(popen.call_args.kwargs["cwd"], command[1])

    @patch("main.threading.Thread")
    def test_primary_background_starts_only_one_telegram_listener(self, thread_class):
        thread = Mock()
        thread_class.return_value = thread

        started = main.start_primary_background_services()

        thread_class.assert_called_once_with(
            target=main.start_telegram_bot,
            daemon=True,
            name="telegram-listener",
        )
        thread.start.assert_called_once_with()
        self.assertEqual(started, [thread])
        self.assertNotIn("schedule_upload_loop", vars(main))

    @patch("profile_automation.pipeline.profile_scheduler.start_profile_scheduler_loop")
    @patch("main.ensure_telegram_listener_running")
    @patch("main.threading.Thread")
    def test_profile_pipeline_reuses_listener_and_starts_only_profile_scheduler(
        self,
        thread_class,
        ensure_listener,
        start_profile_scheduler_loop,
    ):
        thread = Mock()
        thread_class.return_value = thread

        started = main.start_profile_automation_services()

        ensure_listener.assert_called_once_with()
        thread_class.assert_called_once_with(
            target=start_profile_scheduler_loop,
            daemon=True,
            name="profile-scheduler",
        )
        thread.start.assert_called_once_with()
        self.assertEqual(started, [thread])

    @patch("services.telegram_service._pid_from_lock")
    def test_only_primary_bot_lock_claims_external_telegram_listener(self, pid_from_lock):
        pid_from_lock.return_value = None

        self.assertFalse(telegram_service._external_listener_running())

        pid_from_lock.assert_called_once_with(os.path.join(config.BASE_DIR, "bot.lock"))


if __name__ == "__main__":
    unittest.main()
