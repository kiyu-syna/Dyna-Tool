import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import services.tracking_runtime_service as runtime_module


class TrackingRuntimeServiceTests(unittest.TestCase):
    def setUp(self):
        self.logger_patch = patch.object(runtime_module, "logger")
        self.logger_patch.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.profile_dir = Path(self.temp_dir.name)
        self._write_profile("1", enabled=True)
        self._write_profile("2", enabled=False)
        with patch("profile_automation.pipeline.video_job_store.VideoJobStore"):
            self.runtime = runtime_module.TrackingRuntimeService(self.profile_dir)

    def tearDown(self):
        self.temp_dir.cleanup()
        self.logger_patch.stop()

    def _write_profile(self, profile_id: str, *, enabled: bool) -> None:
        payload = {
            "id": profile_id,
            "name": f"Profile {profile_id}",
            "enabled": enabled,
            "douyin": {
                "sources": [
                    {
                        "target_sec_uid": f"source-{profile_id}",
                        "enabled": True,
                    }
                ]
            },
        }
        path = self.profile_dir / f"profile_{profile_id}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_missing_profile_cannot_start(self):
        with self.assertRaises(FileNotFoundError):
            self.runtime.start_profile("404")

    @patch.object(runtime_module.threading, "Thread")
    def test_start_is_idempotent_while_profile_is_active(self, thread_type):
        thread = Mock()
        thread_type.return_value = thread

        first = self.runtime.start_profile("1")
        second = self.runtime.start_profile("1")

        self.assertEqual(first["status"], "starting")
        self.assertEqual(second["status"], "starting")
        self.assertEqual(thread_type.call_count, 1)
        thread.start.assert_called_once_with()

    @patch.object(runtime_module.threading, "Thread")
    def test_stop_signals_worker_and_closes_profile_browsers(self, thread_type):
        thread_type.return_value = Mock()
        self.runtime.start_profile("1")
        stop_event = self.runtime._states["1"]["stop_event"]

        with patch.object(self.runtime, "_close_profile_browsers_async") as close_browsers:
            state = self.runtime.stop_profile("1")

        self.assertIsInstance(stop_event, threading.Event)
        self.assertTrue(stop_event.is_set())
        self.assertEqual(state["status"], "stopping")
        close_browsers.assert_called_once_with("1")

    @patch.object(runtime_module.config, "load_profile_configs")
    @patch.object(runtime_module.threading, "Thread")
    def test_start_all_skips_disabled_profiles(self, thread_type, load_profiles):
        thread_type.return_value = Mock()
        load_profiles.return_value = {
            "1": {
                "id": "1",
                "enabled": True,
                "douyin": {"sources": [{"target_sec_uid": "source-1", "enabled": True}]},
            },
            "2": {
                "id": "2",
                "enabled": False,
                "douyin": {"sources": [{"target_sec_uid": "source-2", "enabled": True}]},
            },
        }

        result = self.runtime.start_all()

        self.assertEqual(result["started"], ["1"])
        self.assertEqual(result["active_profile_ids"], ["1"])
        self.assertEqual(thread_type.call_count, 1)

    def test_profile_without_enabled_sources_cannot_start(self):
        self._write_profile("3", enabled=True)
        path = self.profile_dir / "profile_3.json"
        profile = json.loads(path.read_text(encoding="utf-8"))
        profile["douyin"]["sources"][0]["enabled"] = False
        path.write_text(json.dumps(profile), encoding="utf-8")

        with self.assertRaises(ValueError):
            self.runtime.start_profile("3")

    @patch.object(runtime_module.threading, "Thread")
    def test_shutdown_stops_workers_and_closes_browsers_synchronously(self, thread_type):
        thread_type.return_value = Mock()
        self.runtime.start_profile("1")

        with patch.object(self.runtime, "_close_profile_browsers") as close_browsers:
            result = self.runtime.shutdown()

        self.assertEqual(result["stopped"], ["1"])
        self.assertTrue(self.runtime._states["1"]["stop_event"].is_set())
        close_browsers.assert_called_once_with("1")


if __name__ == "__main__":
    unittest.main()
