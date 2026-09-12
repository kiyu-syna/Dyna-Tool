import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import services.runtime.tracking_runtime_service as runtime_module


class TrackingRuntimeServiceTests(unittest.TestCase):
    def setUp(self):
        self.logger_patch = patch.object(runtime_module, "logger")
        self.logger_patch.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.profile_dir = Path(self.temp_dir.name)
        self._write_profile("1", enabled=True)
        self._write_profile("2", enabled=False)
        with patch("application.workflows.video_job_store.VideoJobStore"):
            self.runtime = runtime_module.TrackingRuntimeService(self.profile_dir)

    def tearDown(self):
        self.temp_dir.cleanup()
        self.logger_patch.stop()

    def _write_profile(self, profile_id: str, *, enabled: bool) -> None:
        payload = {
            "id": profile_id,
            "name": f"Profile {profile_id}",
            "enabled": enabled,
            "douyin": {},
            "tracking_sources": [
                {
                    "platform": "douyin",
                    "sec_uid": f"source-{profile_id}",
                    "enabled": True,
                }
            ],
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
    def test_profiles_are_started_in_fixed_5_second_staggered_slots(self, thread_type):
        thread_type.return_value = Mock()
        self._write_profile("3", enabled=True)
        self._write_profile("4", enabled=True)
        self.runtime.start_profile("1")
        self.runtime.start_profile("3")
        self.runtime.start_profile("4")

        self.assertEqual(self.runtime._states["1"]["startup_delay_seconds"], 0)
        self.assertAlmostEqual(
            self.runtime._states["3"]["startup_delay_seconds"],
            5,
            delta=0.1,
        )
        self.assertAlmostEqual(
            self.runtime._states["4"]["startup_delay_seconds"],
            10,
            delta=0.1,
        )

    @patch.object(runtime_module.threading, "Thread")
    def test_tiktok_only_profile_can_start(self, thread_type):
        thread_type.return_value = Mock()
        payload = {
            "id": "3",
            "name": "TikTok source",
            "enabled": True,
            "tracking_sources": [
                {
                    "platform": "tiktok",
                    "profile_url": "https://www.tiktok.com/@jettvn",
                    "enabled": True,
                }
            ],
            "douyin": {},
        }
        (self.profile_dir / "profile_3.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

        state = self.runtime.start_profile("3")

        self.assertEqual(state["status"], "starting")
        thread_type.return_value.start.assert_called_once_with()

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
                "douyin": {},
                "tracking_sources": [
                    {"platform": "douyin", "sec_uid": "source-1", "enabled": True}
                ],
            },
            "2": {
                "id": "2",
                "enabled": False,
                "douyin": {},
                "tracking_sources": [
                    {"platform": "douyin", "sec_uid": "source-2", "enabled": True}
                ],
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
        profile["tracking_sources"][0]["enabled"] = False
        path.write_text(json.dumps(profile), encoding="utf-8")

        with self.assertRaises(ValueError):
            self.runtime.start_profile("3")

    def test_existing_state_is_compared_instead_of_rebaselined_after_restart(self):
        state_path = self.profile_dir / "existing_seen.json"
        state_path.write_text("{}", encoding="utf-8")
        state = SimpleNamespace(path=state_path, touch=Mock(), replace_seen=Mock())
        new_video = SimpleNamespace(aweme_id="new", create_time=200)
        monitor = SimpleNamespace(
            state=state,
            last_scan_succeeded=True,
            get_new_videos=Mock(return_value=[new_video]),
            build_start_baseline=Mock(),
        )
        worker = SimpleNamespace(min_likes=0, max_duration=300)

        videos, succeeded = self.runtime._collect_source_videos({}, worker, monitor)

        self.assertTrue(succeeded)
        self.assertEqual(videos, [new_video])
        monitor.get_new_videos.assert_called_once_with()
        monitor.build_start_baseline.assert_not_called()
        state.touch.assert_called_once_with()

    def test_new_source_skips_existing_videos_only_once(self):
        state_path = self.profile_dir / "new_seen.json"
        state = SimpleNamespace(path=state_path, touch=Mock(), replace_seen=Mock())
        monitor = SimpleNamespace(
            state=state,
            last_scan_succeeded=True,
            build_start_baseline=Mock(return_value=[SimpleNamespace(aweme_id="old")]),
        )
        worker = SimpleNamespace(min_likes=0, max_duration=300)

        videos, succeeded = self.runtime._collect_source_videos(
            {"initial_scan_mode": "skip_existing"},
            worker,
            monitor,
        )

        self.assertTrue(succeeded)
        self.assertEqual(videos, [])
        monitor.build_start_baseline.assert_called_once_with()

    def test_new_source_can_process_latest_existing_video(self):
        state_path = self.profile_dir / "new_seen.json"
        state = SimpleNamespace(path=state_path, touch=Mock(), replace_seen=Mock())
        older = SimpleNamespace(
            aweme_id="older",
            create_time=100,
            like_count=20,
            duration_seconds=10,
        )
        latest = SimpleNamespace(
            aweme_id="latest",
            create_time=200,
            like_count=20,
            duration_seconds=10,
        )
        monitor = SimpleNamespace(
            state=state,
            last_scan_succeeded=True,
            fetch_latest_videos=Mock(return_value=[older, latest]),
        )
        worker = SimpleNamespace(min_likes=1, max_duration=30)

        videos, succeeded = self.runtime._collect_source_videos(
            {"initial_scan_mode": "process_latest"},
            worker,
            monitor,
        )

        self.assertTrue(succeeded)
        self.assertEqual(videos, [latest])
        state.replace_seen.assert_called_once_with(["older"], 200)

    def test_source_health_moves_from_starting_to_degraded_to_running(self):
        self.runtime._states["1"] = {
            "status": "starting",
            "source_health": {},
            "stop_event": threading.Event(),
        }
        first = {
            "source_key": "first",
            "platform": "douyin",
            "sec_uid": "first-source",
        }
        second = {
            "source_key": "second",
            "platform": "douyin",
            "sec_uid": "second-source",
        }

        state = self.runtime._record_source_scan(
            "1", first, 2, succeeded=False, error="timeout"
        )
        self.assertEqual(state["status"], "starting")
        state = self.runtime._record_source_scan(
            "1", second, 2, succeeded=True
        )
        self.assertEqual(state["status"], "degraded")
        self.assertTrue(state["active"])
        self.assertEqual(state["failing_source_count"], 1)
        state = self.runtime._record_source_scan(
            "1", first, 2, succeeded=True
        )
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["healthy_source_count"], 2)
        self.assertEqual(state["failing_source_count"], 0)

    def test_monitor_keeps_scanning_while_video_is_processing(self):
        stop_event = threading.Event()
        processing_started = threading.Event()
        allow_processing_to_finish = threading.Event()
        state_path = self.profile_dir / "seen.json"
        state_path.write_text("{}", encoding="utf-8")
        video = SimpleNamespace(aweme_id="video-1", create_time=100)
        scan_count = 0

        def get_new_videos():
            nonlocal scan_count
            scan_count += 1
            if scan_count == 1:
                return [video]
            self.assertTrue(processing_started.wait(1))
            allow_processing_to_finish.set()
            stop_event.set()
            return []

        monitor = SimpleNamespace(
            state=SimpleNamespace(path=state_path, touch=Mock()),
            last_scan_succeeded=True,
            get_new_videos=Mock(side_effect=get_new_videos),
        )
        worker = Mock()
        worker.name = "Profile 1"
        worker._source_label.return_value = "Source 1"
        worker._create_monitor.return_value = monitor
        worker.get_pending_videos.return_value = []

        def process_video(*_args, **_kwargs):
            processing_started.set()
            self.assertTrue(allow_processing_to_finish.wait(2))
            return {"status": "completed", "results": {}}

        worker.process_video.side_effect = process_video
        source = {
            "platform": "douyin",
            "source_key": "source-1",
            "sec_uid": "source-1",
            "check_interval_minutes": 1,
        }
        clock = iter(range(10_000, 50_000, 4_000))

        with (
            patch.object(self.runtime, "_load_profile", return_value={"id": "1"}),
            patch.object(runtime_module, "get_tracking_sources", return_value=[source]),
            patch.object(runtime_module.time, "time", side_effect=lambda: next(clock)),
            patch("application.workflows.profile_worker.ProfileWorker", return_value=worker),
            patch("services.integrations.telegram_service.send_new_video_notification"),
        ):
            self.runtime._monitor_loop("1", stop_event)

        self.assertEqual(monitor.get_new_videos.call_count, 2)
        worker.process_video.assert_called_once()

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
