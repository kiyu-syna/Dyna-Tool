import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import desktop_backend.api as desktop_api
from services.profile_management_service import ProfileManagementService


class DesktopBackendStartupTests(unittest.IsolatedAsyncioTestCase):
    def test_extension_id_matches_manifest_public_key(self):
        manifest_path = (
            Path(desktop_api.PROJECT_ROOT)
            / "extensions"
            / "douyin_downloader_9.0.58"
            / "manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(base64.b64decode(manifest["key"])).digest()[:16]
        extension_id = "".join(
            chr(ord("a") + half_byte)
            for byte in digest
            for half_byte in (byte >> 4, byte & 0x0F)
        )

        self.assertEqual(extension_id, desktop_api.DYNA_EXTENSION_ID)
        douyin_scripts = [
            entry
            for entry in manifest["content_scripts"]
            if "*://*.douyin.com/*" in entry.get("matches", [])
        ]
        main_world = next(entry for entry in douyin_scripts if entry.get("world") == "MAIN")
        isolated_world = next(
            entry
            for entry in douyin_scripts
            if "content/dyna-original-integration.js" in entry.get("js", [])
        )
        self.assertIn("content/dyna-original-button-bridge.js", main_world["js"])
        self.assertNotEqual(isolated_world.get("world"), "MAIN")

    async def test_ready_signal_is_emitted_after_uvicorn_starts(self):
        server = object.__new__(desktop_api._ReadyServer)
        server._ready_line = "DYNA_API_READY test"
        events = []

        async def fake_startup(instance, sockets=None):
            events.append("listening")
            instance.started = True

        with patch.object(desktop_api.uvicorn.Server, "startup", fake_startup):
            with patch("builtins.print", side_effect=lambda *_args, **_kwargs: events.append("ready")):
                await server.startup(sockets=[])

        self.assertEqual(events, ["listening", "ready"])


class DesktopApiTests(unittest.TestCase):
    def setUp(self):
        self.profile_temp = tempfile.TemporaryDirectory()
        self.profile_dir = Path(self.profile_temp.name) / "profiles"
        self.state_dir = Path(self.profile_temp.name) / "state"
        self.profile_service = ProfileManagementService(self.profile_dir, self.state_dir)
        self.job_store = Mock()
        self.job_store.list_jobs.return_value = []
        self.runtime = Mock()
        self.runtime.active_profile_ids.return_value = set()
        self.runtime.snapshot.return_value = {
            "profiles": {},
            "active_profile_ids": [],
        }
        self.test_uploads = Mock()
        self.test_uploads.snapshot.return_value = {}
        self.extension_uploads = Mock()
        self.extension_uploads.list_profiles.return_value = []
        self.client = TestClient(
            desktop_api.create_app(
                "test-token",
                job_store=self.job_store,
                runtime=self.runtime,
                profile_service=self.profile_service,
                test_upload_service=self.test_uploads,
                extension_upload_service=self.extension_uploads,
            )
        )
        self.headers = {"X-Dyna-Token": "test-token"}
        self.extension_headers = {
            "X-Dyna-Extension-Id": desktop_api.DYNA_EXTENSION_ID,
            "Origin": desktop_api.DYNA_EXTENSION_ORIGIN,
        }

    def tearDown(self):
        self.profile_temp.cleanup()

    def test_api_requires_private_token(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 401)

    def test_health_reports_ready_backend(self):
        response = self.client.get("/api/health", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_extension_api_requires_known_extension_id(self):
        response = self.client.get("/api/extension/health")

        self.assertEqual(response.status_code, 401)

    def test_extension_health_and_profiles_do_not_require_desktop_token(self):
        self.extension_uploads.list_profiles.return_value = [
            {
                "id": "2",
                "name": "Profile 2",
                "available": True,
                "platforms": {"tiktok": True, "youtube": False, "facebook": False},
            }
        ]

        health = self.client.get("/api/extension/health", headers=self.extension_headers)
        profiles = self.client.get("/api/extension/profiles", headers=self.extension_headers)

        self.assertEqual(health.status_code, 200)
        self.assertEqual(profiles.status_code, 200)
        self.assertEqual(profiles.json()["profiles"][0]["id"], "2")

    def test_extension_cors_preflight_allows_known_origin(self):
        response = self.client.options(
            "/api/extension/health",
            headers={
                "Origin": desktop_api.DYNA_EXTENSION_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Dyna-Extension-Id",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            desktop_api.DYNA_EXTENSION_ORIGIN,
        )

    def test_extension_rejects_unknown_origin(self):
        response = self.client.get(
            "/api/extension/health",
            headers={
                "X-Dyna-Extension-Id": desktop_api.DYNA_EXTENSION_ID,
                "Origin": "https://www.douyin.com",
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_extension_job_handoff_delegates_to_upload_service(self):
        self.extension_uploads.submit.return_value = {
            "accepted": True,
            "duplicate": False,
            "job": {"profile_id": "2", "video_id": "7662", "status": "importing"},
        }

        response = self.client.post(
            "/api/extension/jobs",
            headers=self.extension_headers,
            json={
                "profile_id": "2",
                "video_id": "7662",
                "file_path": "C:\\Users\\Tester\\Downloads\\7662.mp4",
                "source_url": "https://www.douyin.com/video/7662",
                "description": "video",
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["accepted"])
        request = self.extension_uploads.submit.call_args.args[0]
        self.assertEqual(request.profile_id, "2")
        self.assertEqual(request.video_id, "7662")

    @patch.object(desktop_api.busy_mode_service, "set_busy_mode")
    @patch.object(desktop_api.busy_mode_service, "get_busy_mode_state")
    def test_busy_mode_can_be_read_and_toggled(self, get_busy_mode, set_busy_mode):
        get_busy_mode.return_value = {
            "busy": False,
            "source": "default",
            "updated_at": "",
        }
        set_busy_mode.return_value = {
            "busy": True,
            "source": "sidebar",
            "updated_at": "2026-07-16T13:00:00",
        }

        current = self.client.get("/api/busy-mode", headers=self.headers)
        changed = self.client.put(
            "/api/busy-mode",
            headers=self.headers,
            json={"busy": True},
        )

        self.assertFalse(current.json()["state"]["busy"])
        self.assertTrue(changed.json()["state"]["busy"])
        set_busy_mode.assert_called_once_with(True, source="sidebar")

    @patch.object(desktop_api.config, "load_profile_configs")
    def test_profile_list_returns_operational_summary(self, load_profiles):
        load_profiles.return_value = {
            "2": {
                "id": "2",
                "name": "Profile 2",
                "enabled": True,
                "check_interval_minutes": 8,
                "douyin": {
                    "sources": [
                        {"target_sec_uid": "source-a", "enabled": True},
                        {"target_sec_uid": "source-b", "enabled": False},
                    ]
                },
                "tiktok": {"enabled": True},
                "youtube": {"enabled": False},
                "facebook": {"enabled": True},
            }
        }
        response = self.client.get("/api/profiles", headers=self.headers)
        profile = response.json()["profiles"][0]
        self.assertEqual(profile["source_count"], 2)
        self.assertEqual(profile["enabled_source_count"], 1)
        self.assertEqual(profile["check_interval_minutes"], 8)
        self.assertEqual(profile["platforms"], {"tiktok": True, "youtube": False, "facebook": True})

    def test_profile_save_is_atomic_and_preserves_nested_config(self):
        response = self.client.put(
            "/api/profiles/7",
            headers=self.headers,
            json={
                "profile": {
                    "id": "7",
                    "name": "Editor",
                    "enabled": True,
                    "check_interval_minutes": 5,
                    "douyin": {"sources": [{"target_sec_uid": "abc", "enabled": True}]},
                    "youtube": {"enabled": True, "channel_id": "channel"},
                }
            },
        )
        self.assertEqual(response.status_code, 200)
        saved = json.loads((self.profile_dir / "profile_7.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["youtube"]["channel_id"], "channel")
        self.assertEqual(saved["douyin"]["sources"][0]["target_sec_uid"], "abc")

    def test_profile_create_and_delete_use_management_service(self):
        created = self.client.post(
            "/api/profiles",
            headers=self.headers,
            json={"id": "8", "name": "New Profile"},
        )
        deleted = self.client.delete("/api/profiles/8", headers=self.headers)

        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["profile"]["name"], "New Profile")
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse((self.profile_dir / "profile_8.json").exists())

    def test_active_profile_cannot_be_deleted(self):
        self.profile_service.create("9", "Active")
        self.runtime.active_profile_ids.return_value = {"9"}

        response = self.client.delete("/api/profiles/9", headers=self.headers)

        self.assertEqual(response.status_code, 409)

    def test_runtime_snapshot_comes_from_shared_service(self):
        self.runtime.snapshot.return_value = {
            "profiles": {"2": {"profile_id": "2", "status": "running", "active": True}},
            "active_profile_ids": ["2"],
        }

        response = self.client.get("/api/runtime", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["active_profile_ids"], ["2"])

    def test_start_and_stop_profile_delegate_to_runtime(self):
        self.runtime.start_profile.return_value = {
            "profile_id": "2",
            "status": "starting",
            "active": True,
        }
        self.runtime.stop_profile.return_value = {
            "profile_id": "2",
            "status": "stopping",
            "active": True,
        }

        started = self.client.post("/api/runtime/profiles/2/start", headers=self.headers)
        stopped = self.client.post("/api/runtime/profiles/2/stop", headers=self.headers)

        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["state"]["status"], "starting")
        self.assertEqual(stopped.status_code, 200)
        self.assertEqual(stopped.json()["state"]["status"], "stopping")
        self.runtime.start_profile.assert_called_once_with("2")
        self.runtime.stop_profile.assert_called_once_with("2")

    def test_start_missing_profile_returns_not_found(self):
        self.runtime.start_profile.side_effect = FileNotFoundError("missing")

        response = self.client.post("/api/runtime/profiles/404/start", headers=self.headers)

        self.assertEqual(response.status_code, 404)

    def test_shutdown_delegates_to_runtime_cleanup(self):
        self.runtime.shutdown.return_value = {
            "stopped": ["2"],
            "profiles": {},
            "active_profile_ids": [],
        }

        response = self.client.post("/api/runtime/shutdown", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stopped"], ["2"])
        self.runtime.shutdown.assert_called_once_with()

    @patch.object(desktop_api.auth_service, "get_current_user")
    @patch.object(desktop_api.auth_service, "is_logged_in")
    def test_auth_status_never_exposes_session_token(self, is_logged_in, get_current_user):
        is_logged_in.return_value = True
        get_current_user.return_value = {
            "username": "tester",
            "display_name": "Test User",
            "token": "secret-token",
        }

        response = self.client.get(
            "/api/auth/status?verify=false",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["authenticated"])
        self.assertEqual(response.json()["user"]["username"], "tester")
        self.assertNotIn("token", response.json()["user"])

    @patch.object(desktop_api.auth_service, "login")
    def test_login_delegates_without_exposing_token(self, login):
        login.return_value = {"username": "tester", "token": "secret-token"}

        response = self.client.post(
            "/api/auth/login",
            headers=self.headers,
            json={"username": "tester", "password": "password"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"], {"username": "tester"})
        login.assert_called_once_with("tester", "password")

    @patch.object(desktop_api.auth_service, "logout")
    def test_logout_stops_runtime_and_clears_session(self, logout):
        response = self.client.post("/api/auth/logout", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.runtime.shutdown.assert_called_once_with()
        logout.assert_called_once_with()

    @patch.object(desktop_api.license_service, "create_order")
    def test_invalid_license_plan_does_not_create_order(self, create_order):
        response = self.client.post(
            "/api/license/orders",
            headers=self.headers,
            json={"days": 10},
        )

        self.assertEqual(response.status_code, 400)
        create_order.assert_not_called()

    def test_test_upload_requires_explicit_confirmation(self):
        response = self.client.post(
            "/api/runtime/profiles/2/test-upload",
            headers=self.headers,
            json={"confirmed": False},
        )

        self.assertEqual(response.status_code, 400)
        self.test_uploads.start.assert_not_called()

    def test_test_upload_is_blocked_while_profile_is_running(self):
        self.runtime.active_profile_ids.return_value = {"2"}

        response = self.client.post(
            "/api/runtime/profiles/2/test-upload",
            headers=self.headers,
            json={"confirmed": True},
        )

        self.assertEqual(response.status_code, 409)
        self.test_uploads.start.assert_not_called()

    def test_confirmed_test_upload_delegates_to_background_service(self):
        self.test_uploads.start.return_value = {
            "profile_id": "2",
            "active": True,
            "status": "starting",
        }

        response = self.client.post(
            "/api/runtime/profiles/2/test-upload",
            headers=self.headers,
            json={"confirmed": True},
        )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["state"]["active"])
        self.test_uploads.start.assert_called_once_with("2")


if __name__ == "__main__":
    unittest.main()
