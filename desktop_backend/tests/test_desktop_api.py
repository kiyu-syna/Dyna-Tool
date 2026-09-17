import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import desktop_backend.api as desktop_api
from application.tracking.profile_management_service import ProfileManagementService


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
        self.assertIn("content/dyna-batch-selection.js", isolated_world["js"])
        self.assertIn("content/dyna-batch-selection.css", isolated_world["css"])

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
        self.manual_publish = Mock()
        self.manual_publish.list_profiles.return_value = []
        self.manual_publish.list_jobs.return_value = {"jobs": [], "total": 0}
        self.manual_publish.readiness_snapshot.return_value = {
            "checks": [],
            "checking": False,
            "checking_profiles": [],
        }
        self.douyin_selections = Mock()
        self.douyin_selections.active.return_value = None
        self.local_profiles = Mock()
        self.local_profiles.snapshot.return_value = {}
        self.assistant = Mock()
        self.assistant.chat.return_value = {
            "reply": "Mọi Profile đang ổn.",
            "actions": [],
            "proposal_token": "",
            "proposal_expires_in": 0,
            "provider": "gemini",
            "model": "gemini-test",
        }
        self.assistant.generate_caption.return_value = {
            "caption": "Mô tả AI riêng #game",
            "provider": "gemini",
            "model": "gemini-test",
        }
        self.video_ai = Mock()
        self.video_ai.capabilities.return_value = {
            "ffmpeg": {"ready": True},
            "ffprobe": {"ready": True},
            "asr": {"ready": True, "models": ["small"]},
            "translation": {"ready": True},
        }
        self.video_ai.create_project.return_value = {
            "id": "a" * 32,
            "source_path": r"C:\Videos\sample.mp4",
            "status": "ready",
            "subtitles": [],
        }
        self.client = TestClient(
            desktop_api.create_app(
                "test-token",
                job_store=self.job_store,
                runtime=self.runtime,
                profile_service=self.profile_service,
                test_upload_service=self.test_uploads,
                extension_upload_service=self.extension_uploads,
                manual_publish_service=self.manual_publish,
                douyin_selection_service=self.douyin_selections,
                local_profile_setup_service=self.local_profiles,
                ai_assistant_service=self.assistant,
                video_ai_service=self.video_ai,
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

    @patch.object(desktop_api.config, "load_profile_configs")
    def test_assistant_chat_receives_only_sanitized_operational_context(self, load_profiles):
        load_profiles.return_value = {
            "1": {
                "id": "1",
                "name": "Kênh chính",
                "enabled": True,
                "tiktok": {"enabled": True},
                "youtube": {"enabled": False},
                "facebook": {"enabled": False},
            }
        }
        self.job_store.list_jobs.return_value = [
            {
                "profile_id": "1",
                "video_id": "secret-video",
                "status": "failed_upload",
                "file_path": r"C:\Private\secret.mp4",
                "caption": "private caption",
            }
        ]
        self.manual_publish.list_jobs.return_value = {
            "jobs": [{"status": "scheduled", "file_path": r"C:\Private\publish.mp4"}],
            "total": 1,
        }

        response = self.client.post(
            "/api/assistant/chat",
            headers=self.headers,
            json={"messages": [{"role": "user", "content": "Tình trạng Dyna?"}]},
        )

        self.assertEqual(response.status_code, 200)
        messages, operational_context = self.assistant.chat.call_args.args
        self.assertEqual(messages[-1]["content"], "Tình trạng Dyna?")
        serialized = json.dumps(operational_context, ensure_ascii=False)
        self.assertIn("Kênh chính", serialized)
        self.assertIn('"error_count": 1', serialized)
        self.assertNotIn("secret.mp4", serialized)
        self.assertNotIn("publish.mp4", serialized)
        self.assertNotIn("private caption", serialized)

    def test_assistant_action_requires_proposal_confirmation(self):
        self.assistant.consume_proposal.return_value = [
            {"type": "start_profile", "args": {"profile_id": "2"}}
        ]
        self.runtime.start_profile.return_value = {"profile_id": "2", "status": "starting"}

        direct = self.client.post(
            "/api/assistant/actions/confirm",
            headers=self.headers,
            json={"actions": [{"type": "start_profile", "args": {"profile_id": "2"}}]},
        )
        confirmed = self.client.post(
            "/api/assistant/actions/confirm",
            headers=self.headers,
            json={"proposal_token": "proposal-token-at-least-20-chars"},
        )

        self.assertEqual(direct.status_code, 422)
        self.assertEqual(confirmed.status_code, 200)
        self.assistant.consume_proposal.assert_called_once_with("proposal-token-at-least-20-chars")
        self.runtime.start_profile.assert_called_once_with("2")

    def test_assistant_generates_caption_with_the_same_dyna_ai_service(self):
        response = self.client.post(
            "/api/assistant/captions/generate",
            headers=self.headers,
            json={
                "original_description": "Mô tả gốc #game",
                "instruction": "Viết vui vẻ và giữ hashtag",
                "video_label": "76620001",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["caption"], "Mô tả AI riêng #game")
        self.assistant.generate_caption.assert_called_once_with(
            original_description="Mô tả gốc #game",
            instruction="Viết vui vẻ và giữ hashtag",
            video_label="76620001",
        )

    def test_video_ai_project_uses_selected_local_video(self):
        response = self.client.post(
            "/api/video-ai/projects",
            headers=self.headers,
            json={"source_path": r"C:\Videos\sample.mp4"},
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["project"]["status"], "ready")
        self.video_ai.create_project.assert_called_once_with(r"C:\Videos\sample.mp4")

    def test_video_ai_editor_autosave_accepts_large_font(self):
        project_id = "a" * 32
        self.video_ai.save_editor_state.return_value = {
            "id": project_id,
            "status": "ready",
            "stage": "Đã tự lưu chỉnh sửa",
        }
        response = self.client.put(
            f"/api/video-ai/projects/{project_id}/editor",
            headers=self.headers,
            json={
                "subtitles": [
                    {
                        "id": "line-1",
                        "start": 0,
                        "end": 2,
                        "text": "Nguồn",
                        "translated_text": "Bản dịch",
                    }
                ],
                "blur": {
                    "enabled": True,
                    "x": 0.05,
                    "y": 0.78,
                    "width": 0.9,
                    "height": 0.17,
                    "strength": 22,
                },
                "style": {
                    "font_name": "Arial",
                    "font_size": 260,
                    "margin_v": 54,
                    "outline": 2,
                    "primary_color": "&H00FFFFFF",
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["project"]["stage"], "Đã tự lưu chỉnh sửa")
        self.assertEqual(
            self.video_ai.save_editor_state.call_args.kwargs["style"]["font_size"],
            260,
        )

    def test_video_ai_starts_hard_subtitle_detection(self):
        project_id = "a" * 32
        self.video_ai.start_subtitle_detection.return_value = {
            "id": project_id,
            "status": "detecting",
            "stage": "Đang chuẩn bị tìm vùng phụ đề cứng",
        }
        response = self.client.post(
            f"/api/video-ai/projects/{project_id}/detect-subtitle-region",
            headers=self.headers,
            json={"sample_count": 24},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["project"]["status"], "detecting")
        self.video_ai.start_subtitle_detection.assert_called_once_with(
            project_id,
            sample_count=24,
        )

    def test_video_ai_starts_dubbing_with_selected_voice(self):
        project_id = "a" * 32
        self.video_ai.start_dubbing.return_value = {
            "id": project_id,
            "status": "dubbing",
            "dubbing_options": {
                "voice": "vi-VN-NamMinhNeural",
                "rate": 10,
                "volume": 120,
                "original_volume": 15,
            },
        }
        response = self.client.post(
            f"/api/video-ai/projects/{project_id}/dubbing",
            headers=self.headers,
            json={
                "enabled": True,
                "voice": "vi-VN-NamMinhNeural",
                "rate": 10,
                "volume": 120,
                "original_volume": 15,
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["project"]["status"], "dubbing")
        self.video_ai.start_dubbing.assert_called_once_with(
            project_id,
            provider="edge",
            voice="vi-VN-NamMinhNeural",
            style="tu_nhien",
            rate=10,
            volume=120,
            original_volume=15,
        )

    def test_video_ai_starts_vieneu_dubbing_with_style(self):
        project_id = "a" * 32
        self.video_ai.start_dubbing.return_value = {
            "id": project_id,
            "status": "dubbing",
            "dubbing_options": {
                "provider": "vieneu",
                "voice": "Trúc Ly",
                "style": "doc_truyen",
            },
        }
        response = self.client.post(
            f"/api/video-ai/projects/{project_id}/dubbing",
            headers=self.headers,
            json={
                "enabled": True,
                "provider": "vieneu",
                "voice": "Trúc Ly",
                "style": "doc_truyen",
            },
        )

        self.assertEqual(response.status_code, 202)
        self.video_ai.start_dubbing.assert_called_once_with(
            project_id,
            provider="vieneu",
            voice="Trúc Ly",
            style="doc_truyen",
            rate=0,
            volume=100,
            original_volume=18,
        )

    def test_video_ai_dubbing_preview_requires_token(self):
        project_id = "a" * 32
        audio = Path(self.profile_temp.name) / "dubbed.wav"
        audio.write_bytes(b"RIFF-test-dubbing-audio")
        self.video_ai.get_project.return_value = {
            "dubbing_options": {"audio_path": str(audio)}
        }

        unauthorized = self.client.get(
            f"/api/video-ai/projects/{project_id}/dubbing-audio",
        )
        response = self.client.get(
            f"/api/video-ai/projects/{project_id}/dubbing-audio?access_token=test-token",
        )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"RIFF-test-dubbing-audio")

    def test_video_ai_reports_and_prepares_vieneu_runtime(self):
        self.video_ai.tts_runtime_status.return_value = {
            "provider": "vieneu",
            "state": "downloaded",
            "ready": False,
            "progress": 100,
        }
        self.video_ai.start_tts_prepare.return_value = {
            "provider": "vieneu",
            "state": "loading",
            "ready": False,
            "progress": 98,
        }

        status = self.client.get(
            "/api/video-ai/tts/status?provider=vieneu",
            headers=self.headers,
        )
        prepare = self.client.post(
            "/api/video-ai/tts/prepare",
            headers=self.headers,
            json={"provider": "vieneu"},
        )

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["state"], "downloaded")
        self.assertEqual(prepare.status_code, 202)
        self.video_ai.start_tts_prepare.assert_called_once_with("vieneu")

    def test_video_ai_creates_and_serves_voice_preview(self):
        preview_id = "b" * 32
        audio = Path(self.profile_temp.name) / f"{preview_id}.wav"
        audio.write_bytes(b"RIFF-preview")
        self.video_ai.create_tts_preview.return_value = {
            "preview_id": preview_id,
            "provider": "vieneu",
            "voice": "Trúc Ly",
        }
        self.video_ai.tts_preview_path.return_value = audio

        created = self.client.post(
            "/api/video-ai/tts/preview",
            headers=self.headers,
            json={
                "provider": "vieneu",
                "voice": "Trúc Ly",
                "style": "tu_nhien",
                "text": "Xin chào [cười]",
            },
        )
        unauthorized = self.client.get(f"/api/video-ai/tts/previews/{preview_id}")
        served = self.client.get(
            f"/api/video-ai/tts/previews/{preview_id}?access_token=test-token"
        )

        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["voice"], "Trúc Ly")
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.content, b"RIFF-preview")

    def test_video_ai_preview_supports_authenticated_byte_ranges(self):
        source = Path(self.profile_temp.name) / "preview.mp4"
        source.write_bytes(b"0123456789")
        self.video_ai.get_project.return_value = {"source_path": str(source)}

        unauthorized = self.client.get(
            f"/api/video-ai/projects/{'a' * 32}/media",
        )
        response = self.client.get(
            f"/api/video-ai/projects/{'a' * 32}/media?access_token=test-token",
            headers={"Range": "bytes=2-5"},
        )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"2345")
        self.assertEqual(response.headers["accept-ranges"], "bytes")

    @patch.object(desktop_api, "_apply_runtime_settings")
    @patch.object(desktop_api, "_atomic_json_write")
    @patch.object(desktop_api.config, "load_settings", return_value={"UI_LANGUAGE": "vi"})
    def test_settings_accept_simplified_chinese_language(
        self,
        _load_settings,
        atomic_write,
        _apply_runtime_settings,
    ):
        response = self.client.put(
            "/api/settings",
            headers=self.headers,
            json={"settings": {"UI_LANGUAGE": "zh"}},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["settings"]["UI_LANGUAGE"], "zh")
        self.assertEqual(atomic_write.call_args.args[1]["UI_LANGUAGE"], "zh")

    @patch.object(desktop_api, "_apply_runtime_settings")
    @patch.object(desktop_api, "_atomic_json_write")
    @patch.object(desktop_api.config, "load_settings", return_value={"UI_LANGUAGE": "vi"})
    def test_settings_accept_traditional_chinese_language(
        self,
        _load_settings,
        atomic_write,
        _apply_runtime_settings,
    ):
        response = self.client.put(
            "/api/settings",
            headers=self.headers,
            json={"settings": {"UI_LANGUAGE": "zh-TW"}},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["settings"]["UI_LANGUAGE"], "zh-TW")
        self.assertEqual(atomic_write.call_args.args[1]["UI_LANGUAGE"], "zh-TW")

    def test_settings_reject_unknown_language(self):
        with patch.object(desktop_api.config, "load_settings", return_value={"UI_LANGUAGE": "vi"}):
            response = self.client.put(
                "/api/settings",
                headers=self.headers,
                json={"settings": {"UI_LANGUAGE": "fr"}},
            )

        self.assertEqual(response.status_code, 400)

    def test_logs_api_returns_structured_profile_entries(self):
        log_file = self.state_dir / "test-system.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(
            "2026-07-18 14:15:16 | WARNING | [Profile 1] Cần kiểm tra đăng nhập\n",
            encoding="utf-8",
        )
        self.profile_service.create("1", "Kênh thử nghiệm")

        with patch.object(desktop_api, "LOG_FILE", log_file):
            response = self.client.get("/api/logs?limit=100", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["entries"][0]["profile_id"], "1")
        self.assertEqual(payload["entries"][0]["time"], "14:15:16")
        self.assertEqual(payload["profiles"][0]["name"], "Kênh thử nghiệm")

    def test_logs_api_can_reset_current_and_rotated_logs(self):
        log_file = self.state_dir / "reset-system.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("2026-07-18 14:15:16 | INFO | old\n", encoding="utf-8")
        Path(f"{log_file}.1").write_text(
            "2026-07-17 14:15:16 | INFO | older\n",
            encoding="utf-8",
        )

        with patch.object(desktop_api, "LOG_FILE", log_file):
            response = self.client.delete("/api/logs", headers=self.headers)
            snapshot = self.client.get("/api/logs?limit=100", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(snapshot.status_code, 200)
        self.assertEqual(snapshot.json()["total"], 0)
        self.assertFalse(Path(f"{log_file}.1").exists())

    def test_queue_delete_endpoints_delegate_to_scoped_services(self):
        self.job_store.dismiss_job.return_value = {
            "profile_id": "1",
            "video_id": "douyin-1",
        }
        self.manual_publish.delete.return_value = {
            "profile_id": "2",
            "video_id": "local-1",
        }

        tracking = self.client.post(
            "/api/jobs/delete",
            headers=self.headers,
            json={"profile_id": "1", "video_id": "douyin-1"},
        )
        publisher = self.client.post(
            "/api/publisher/jobs/delete",
            headers=self.headers,
            json={"profile_id": "2", "video_id": "local-1"},
        )

        self.assertEqual(tracking.status_code, 200)
        self.assertEqual(publisher.status_code, 200)
        self.job_store.dismiss_job.assert_called_once()
        self.manual_publish.delete.assert_called_once_with("2", "local-1")

    def test_queue_reset_endpoints_clear_their_respective_queues(self):
        self.job_store.dismiss_jobs.return_value = {"reset_count": 12}
        self.manual_publish.reset_jobs.return_value = {"reset_count": 4}

        tracking = self.client.post("/api/jobs/reset", headers=self.headers)
        publisher = self.client.post("/api/publisher/jobs/reset", headers=self.headers)

        self.assertEqual(tracking.status_code, 200)
        self.assertEqual(tracking.json()["reset_count"], 12)
        self.assertEqual(publisher.status_code, 200)
        self.assertEqual(publisher.json()["reset_count"], 4)
        self.job_store.dismiss_jobs.assert_called_once()
        self.manual_publish.reset_jobs.assert_called_once_with()

    @patch.object(desktop_api, "install_browser_runtime")
    def test_browser_runtime_install_returns_managed_executable(self, install_runtime):
        runtime = Mock()
        runtime.to_dict.return_value = {
            "runtime_id": "iron-141",
            "executable_path": r"C:\Users\Tester\AppData\Local\Dyna\browser-runtimes\iron-141\chrome.exe",
            "valid": True,
        }
        install_runtime.return_value = runtime

        response = self.client.post(
            "/api/browser-runtimes/install",
            headers=self.headers,
            json={"source_executable": r"C:\source\chrome.exe"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["runtime"]["runtime_id"], "iron-141")
        install_runtime.assert_called_once_with(r"C:\source\chrome.exe", runtime_id="")

    @patch.object(desktop_api, "install_playwright_chromium_runtime")
    def test_default_chromium_runtime_can_be_installed(self, install_runtime):
        runtime = Mock()
        runtime.to_dict.return_value = {
            "runtime_id": "chromium-1223",
            "executable_path": r"C:\Dyna\chromium-1223\chrome.exe",
            "valid": True,
        }
        install_runtime.return_value = runtime

        response = self.client.post(
            "/api/browser-runtimes/install-playwright",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["runtime"]["runtime_id"], "chromium-1223")

    def test_local_profile_setup_can_start_and_finish(self):
        self.local_profiles.start.return_value = {
            "profile_id": "2",
            "status": "starting",
            "active": True,
        }
        self.local_profiles.finish.return_value = {
            "profile_id": "2",
            "status": "closing",
            "active": True,
        }
        self.local_profiles.open_existing.return_value = {
            "profile_id": "2",
            "status": "starting",
            "active": True,
        }

        started = self.client.post(
            "/api/browser-profiles/2/initialize",
            headers=self.headers,
            json={"executable_path": ""},
        )
        finished = self.client.post(
            "/api/browser-profiles/2/finish",
            headers=self.headers,
        )
        opened = self.client.post(
            "/api/browser-profiles/2/open",
            headers=self.headers,
        )

        self.assertEqual(started.status_code, 200)
        self.assertEqual(finished.status_code, 200)
        self.assertEqual(opened.status_code, 200)
        self.local_profiles.start.assert_called_once_with("2", executable_path="")
        self.local_profiles.finish.assert_called_once_with("2")
        self.local_profiles.open_existing.assert_called_once_with("2")

    def test_local_profile_check_can_repair_stale_locks(self):
        self.local_profiles.check.return_value = {
            "profile_id": "2",
            "status": "recovered",
            "code": "stale_locks_repaired",
            "ready": True,
            "message": "Đã cách ly 1 file khóa cũ; Profile sẵn sàng mở.",
            "suggested_action": "Có thể mở đăng nhập hoặc chạy Profile.",
            "locks": ["SingletonLock"],
            "pids": [],
            "repaired": True,
            "recovery_path": r"C:\profiles\2\.dyna-recovery\stale-locks\test",
            "checked_at": "2026-07-18T17:00:00",
        }

        response = self.client.post(
            "/api/browser-profiles/2/check",
            headers=self.headers,
            json={"repair_stale_locks": True},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["state"]["repaired"])
        self.local_profiles.check.assert_called_once_with(
            "2",
            repair_stale_locks=True,
        )

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

    def test_douyin_selection_can_start_in_dyna_and_complete_from_extension(self):
        selecting = {
            "id": "selection-1",
            "source_url": "https://www.douyin.com/user/source",
            "status": "selecting",
            "items": [],
            "selected_count": 0,
        }
        ready = {
            **selecting,
            "status": "ready",
            "selected_count": 1,
            "items": [{"video_id": "76620001"}],
        }
        self.douyin_selections.create.return_value = selecting
        self.douyin_selections.complete.return_value = ready

        created = self.client.post(
            "/api/publisher/douyin-selections",
            headers=self.headers,
            json={"source_url": selecting["source_url"]},
        )
        completed = self.client.post(
            "/api/extension/selections/selection-1/complete",
            headers=self.extension_headers,
            json={
                "items": [
                    {
                        "video_id": "76620001",
                        "source_url": "https://www.douyin.com/video/76620001",
                        "description": "Original caption",
                    }
                ]
            },
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(completed.status_code, 202)
        self.assertEqual(completed.json()["session"]["selected_count"], 1)
        selected_items = self.douyin_selections.complete.call_args.args[1]
        self.assertEqual(selected_items[0]["video_id"], "76620001")

    def test_douyin_selection_can_start_directly_from_extension(self):
        selecting = {
            "id": "selection-direct",
            "source_url": "https://www.douyin.com/user/source",
            "status": "selecting",
            "items": [],
            "selected_count": 0,
        }
        self.douyin_selections.create.return_value = selecting

        response = self.client.post(
            "/api/extension/selections",
            headers=self.extension_headers,
            json={"source_url": selecting["source_url"]},
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["session"]["id"], "selection-direct")
        self.douyin_selections.create.assert_called_once_with(selecting["source_url"])

    def test_douyin_selection_publish_keeps_the_user_batch_name(self):
        self.douyin_selections.submit.return_value = {
            "id": "selection-1",
            "status": "preparing",
            "batch_name": "Lô Ma Chao buổi tối",
        }

        response = self.client.post(
            "/api/publisher/douyin-selections/selection-1/publish",
            headers=self.headers,
            json={
                "batch_name": "Lô Ma Chao buổi tối",
                "items": [
                    {
                        "video_id": "76620001",
                        "caption": "Mô tả riêng #game",
                        "scheduled_at": "",
                    }
                ],
                "targets": [{"profile_id": "2", "platforms": ["tiktok"]}],
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            self.douyin_selections.submit.call_args.kwargs["batch_name"],
            "Lô Ma Chao buổi tối",
        )

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

    def test_publish_center_job_delegates_to_manual_service(self):
        self.manual_publish.submit.return_value = {
            "ok": True,
            "batch_id": "batch",
            "file_count": 1,
            "job_count": 1,
            "jobs": [],
        }

        response = self.client.post(
            "/api/publisher/jobs",
            headers=self.headers,
            json={
                "file_paths": [r"C:\Videos\clip.mp4"],
                "caption": "Caption local",
                "batch_name": "Lô video local",
                "targets": [{"profile_id": "2", "platforms": ["tiktok"]}],
            },
        )

        self.assertEqual(response.status_code, 202)
        publish_request = self.manual_publish.submit.call_args.args[0]
        self.assertEqual(publish_request.caption, "Caption local")
        self.assertEqual(publish_request.batch_name, "Lô video local")
        self.assertEqual(publish_request.targets[0].profile_id, "2")
        self.assertEqual(publish_request.targets[0].platforms, ("tiktok",))

    def test_publish_center_accepts_caption_and_schedule_per_video(self):
        self.manual_publish.submit.return_value = {
            "ok": True,
            "batch_id": "scheduled-batch",
            "file_count": 2,
            "job_count": 2,
            "scheduled_count": 1,
            "immediate_count": 1,
            "jobs": [],
        }

        response = self.client.post(
            "/api/publisher/jobs",
            headers=self.headers,
            json={
                "items": [
                    {"file_path": r"C:\Videos\a.mp4", "caption": "Caption A"},
                    {
                        "file_path": r"C:\Videos\b.mp4",
                        "caption": "Caption B",
                        "scheduled_at": "2099-01-01T02:00:00Z",
                    },
                ],
                "targets": [{"profile_id": "2", "platforms": ["youtube"]}],
            },
        )

        self.assertEqual(response.status_code, 202)
        publish_request = self.manual_publish.submit.call_args.args[0]
        self.assertEqual(publish_request.items[0].caption, "Caption A")
        self.assertEqual(publish_request.items[1].caption, "Caption B")
        self.assertEqual(publish_request.items[1].scheduled_at, "2099-01-01T02:00:00Z")

    def test_publish_center_can_start_automatic_ready_check(self):
        self.manual_publish.refresh_readiness.return_value = {
            "checks": [
                {
                    "profile_id": "2",
                    "platform": "tiktok",
                    "status": "checking",
                    "ready": False,
                    "message": "Đang kiểm tra",
                    "checked_at": "",
                    "expires_at": "",
                }
            ],
            "checking": True,
            "checking_profiles": ["2"],
        }

        response = self.client.post(
            "/api/publisher/readiness/refresh",
            headers=self.headers,
            json={
                "targets": [{"profile_id": "2", "platforms": ["tiktok", "youtube"]}],
                "force": True,
            },
        )

        self.assertEqual(response.status_code, 202)
        target = self.manual_publish.refresh_readiness.call_args.args[0][0]
        self.assertEqual(target.profile_id, "2")
        self.assertEqual(target.platforms, ("tiktok", "youtube"))
        self.assertTrue(self.manual_publish.refresh_readiness.call_args.kwargs["force"])

    @patch.object(desktop_api.config, "load_profile_configs")
    def test_profile_list_returns_operational_summary(self, load_profiles):
        load_profiles.return_value = {
            "2": {
                "id": "2",
                "name": "Profile 2",
                "enabled": True,
                "check_interval_minutes": 8,
                "douyin": {},
                "tracking_sources": [
                    {"platform": "douyin", "sec_uid": "source-a", "enabled": True},
                    {"platform": "douyin", "sec_uid": "source-b", "enabled": False},
                ],
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
                    "douyin": {},
                    "tracking_sources": [
                        {"platform": "douyin", "sec_uid": "abc", "enabled": True}
                    ],
                    "youtube": {"enabled": True, "channel_id": "channel"},
                }
            },
        )
        self.assertEqual(response.status_code, 200)
        saved = json.loads((self.profile_dir / "profile_7.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["youtube"]["channel_id"], "channel")
        self.assertEqual(saved["tracking_sources"][0]["sec_uid"], "abc")
        self.assertNotIn("sources", saved["douyin"])

    @patch("application.workflows.profile_worker.ProfileWorker")
    def test_profile_source_test_returns_first_four_valid_videos(self, worker_type):
        self.profile_service.create("10", "Source test")
        videos = []
        for index in range(5):
            video = Mock()
            video.aweme_id = f"video-{index}"
            video.share_url = f"https://www.tiktok.com/@jettvn/video/video-{index}"
            video.desc = "test"
            video.create_time = index
            video.like_count = 10
            video.duration_seconds = 12.0
            videos.append(video)
        monitor = Mock()
        monitor.fetch_latest_videos.return_value = videos
        worker_type.return_value._create_monitor.return_value = monitor

        response = self.client.post(
            "/api/profiles/10/sources/test",
            headers=self.headers,
            json={
                "source": {
                    "platform": "tiktok",
                    "profile_url": "https://www.tiktok.com/@jettvn",
                }
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(
            [video["video_id"] for video in response.json()["videos"]],
            ["video-0", "video-1", "video-2", "video-3"],
        )
        monitor.fetch_latest_videos.assert_called_once_with(pages_to_fetch=1)

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
