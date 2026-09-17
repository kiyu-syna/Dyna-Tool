import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from services.video_ai.video_ai_service import (
    VideoAiError,
    VideoAiService,
    _atempo_filters,
    _build_filter_graph,
    _detect_subtitle_region_from_frames,
    _directory_size,
    _normalized_blur,
    _normalized_dubbing,
    _translation_chunks,
    _write_ass,
)


class VideoAiDetectionTests(unittest.TestCase):
    def test_detects_repeated_hard_subtitle_region(self):
        import cv2
        import numpy as np

        frames = []
        for index in range(16):
            frame = np.full((360, 640, 3), (24 + index, 38, 54), dtype=np.uint8)
            text = f"Subtitle {index}"
            cv2.putText(
                frame,
                text,
                (165, 310),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.05,
                (0, 0, 0),
                6,
                cv2.LINE_AA,
            )
            cv2.putText(
                frame,
                text,
                (165, 310),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.05,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            frames.append(frame)

        result = _detect_subtitle_region_from_frames(frames)

        self.assertTrue(result["detected"])
        self.assertGreaterEqual(result["confidence"], 60)
        self.assertGreater(result["region"]["y"], 0.65)
        self.assertGreater(result["region"]["width"], 0.2)

    def test_does_not_detect_a_blank_video(self):
        import numpy as np

        frames = [np.zeros((360, 640, 3), dtype=np.uint8) for _ in range(10)]

        result = _detect_subtitle_region_from_frames(frames)

        self.assertFalse(result["detected"])
        self.assertIsNone(result["region"])


class VideoAiSubtitleTests(unittest.TestCase):
    def test_normalizes_dubbing_controls_and_voice(self):
        normalized = _normalized_dubbing(
            {
                "voice": "unknown",
                "rate": 80,
                "volume": 500,
                "original_volume": -10,
            }
        )

        self.assertEqual(normalized["voice"], "vi-VN-HoaiMyNeural")
        self.assertEqual(normalized["rate"], 50)
        self.assertEqual(normalized["volume"], 200)
        self.assertEqual(normalized["original_volume"], 0)

    def test_normalizes_vieneu_provider_voice_and_style(self):
        normalized = _normalized_dubbing(
            {
                "provider": "vieneu",
                "voice": "Trúc Ly",
                "style": "doc_truyen",
            }
        )

        self.assertEqual(normalized["provider"], "vieneu")
        self.assertEqual(normalized["voice"], "Trúc Ly")
        self.assertEqual(normalized["style"], "doc_truyen")

    def test_normalizes_generated_speed_warnings(self):
        normalized = _normalized_dubbing(
            {
                "max_speed": 1.82,
                "speed_warnings": [
                    {
                        "id": "line-1",
                        "speed": 1.82,
                        "slot_duration": 1.2,
                        "speech_duration": 2.184,
                    }
                ],
            }
        )

        self.assertEqual(normalized["max_speed"], 1.82)
        self.assertEqual(normalized["speed_warnings"][0]["id"], "line-1")
        self.assertEqual(normalized["speed_warnings"][0]["speed"], 1.82)

    def test_directory_size_ignores_missing_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model.bin").write_bytes(b"x" * 128)

            self.assertEqual(_directory_size(root), 128)
            self.assertEqual(_directory_size(root / "missing"), 0)

    def test_atempo_chain_supports_long_lines_needing_high_speed(self):
        filters = _atempo_filters(5.0)

        self.assertEqual(filters[:2], ["atempo=2", "atempo=2"])
        self.assertEqual(filters[-1], "atempo=1.250000")

    def test_writes_translated_ass_subtitle_track(self):
        subtitles = [
            {
                "id": "1",
                "start": 1.25,
                "end": 3.5,
                "text": "Xin chào",
                "translated_text": "Hello",
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ass = root / "captions.ass"

            self.assertEqual(
                _write_ass(
                    ass,
                    subtitles,
                    "translated",
                    width=1080,
                    height=1920,
                    style={},
                ),
                1,
            )

            self.assertIn("Hello", ass.read_text(encoding="utf-8-sig"))

    def test_positions_translated_subtitles_in_the_blur_region(self):
        subtitles = [
            {
                "id": "1",
                "start": 0,
                "end": 2,
                "text": "Nguồn",
                "translated_text": "Bản dịch",
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / "positioned.ass"
            _write_ass(
                ass,
                subtitles,
                "translated",
                width=1920,
                height=1080,
                style={"font_name": "Arial", "font_size": 42},
                subtitle_region={
                    "enabled": True,
                    "x": 0.1,
                    "y": 0.8,
                    "width": 0.8,
                    "height": 0.1,
                },
            )

            content = ass.read_text(encoding="utf-8-sig")
            self.assertIn("Style: Dyna,Arial,42", content)
            self.assertIn(",5,204,204,54,1", content)
            self.assertIn(r"{\an5\pos(960.0,918.0)}Bản dịch", content)

    def test_ass_accepts_font_sizes_above_the_previous_limit(self):
        subtitles = [
            {
                "id": "1",
                "start": 0,
                "end": 2,
                "text": "Nguồn",
                "translated_text": "Bản dịch",
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / "large-font.ass"
            _write_ass(
                ass,
                subtitles,
                "translated",
                width=2160,
                height=3840,
                style={"font_name": "Arial", "font_size": 260},
            )

            self.assertIn("Style: Dyna,Arial,260", ass.read_text(encoding="utf-8-sig"))

    def test_filter_graph_applies_blur_before_ass_subtitles(self):
        blur = _normalized_blur(
            {"enabled": True, "x": 0.1, "y": 0.8, "width": 0.8, "height": 0.15}
        )
        graph = _build_filter_graph(Path(r"C:\Temp\dyna.ass"), blur)

        self.assertIn("crop=w=iw*0.8:h=ih*0.15", graph)
        self.assertIn("luma_radius='max(0,min(22,min(w,h)/2-1))'", graph)
        self.assertIn("chroma_radius='max(0,min(11,min(cw,ch)/2-1))'", graph)
        self.assertIn("overlay=x=main_w*0.1:y=main_h*0.8", graph)
        self.assertIn("ass=filename='C\\:/Temp/dyna.ass'", graph)
        self.assertTrue(graph.endswith("[vout]"))

    def test_translation_chunks_respect_item_limit(self):
        chunks = _translation_chunks(
            [{"id": str(index), "text": f"Subtitle {index}"} for index in range(65)]
        )
        self.assertEqual([len(chunk) for chunk in chunks], [20, 20, 20, 5])


class VideoAiProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        source = root / "source.mp4"
        source.write_bytes(b"video-placeholder")
        self.source = source
        self.service = VideoAiService(
            project_root=root / "state",
            scratch_root=root / "scratch",
            video_validator=lambda *_args, **_kwargs: {
                "path": str(source),
                "file_size": source.stat().st_size,
                "duration_seconds": 10.0,
                "width": 1920,
                "height": 1080,
                "has_audio": True,
                "video_codec": "h264",
                "audio_codec": "aac",
            },
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_creates_and_updates_a_persistent_project(self):
        project = self.service.create_project(str(self.source))
        updated = self.service.save_subtitles(
            project["id"],
            [
                {
                    "id": "caption-1",
                    "start": 0,
                    "end": 2,
                    "text": "Nguồn",
                    "translated_text": "Target",
                }
            ],
        )

        self.assertEqual(updated["subtitles"][0]["translated_text"], "Target")
        self.assertEqual(
            self.service.get_project(project["id"])["subtitles"][0]["text"],
            "Nguồn",
        )

    def test_autosaves_editor_state_and_accepts_large_font_size(self):
        project = self.service.create_project(str(self.source))
        updated = self.service.save_editor_state(
            project["id"],
            subtitles=[
                {
                    "id": "caption-1",
                    "start": 0,
                    "end": 2,
                    "text": "Nguồn",
                    "translated_text": "Bản dịch",
                }
            ],
            blur={
                "enabled": True,
                "x": 0.1,
                "y": 0.75,
                "width": 0.8,
                "height": 0.18,
                "strength": 30,
            },
            style={"font_name": "Arial", "font_size": 260},
        )

        self.assertEqual(updated["stage"], "Đã tự lưu chỉnh sửa")
        self.assertEqual(updated["subtitles"][0]["translated_text"], "Bản dịch")
        self.assertEqual(updated["render_options"]["style"]["font_size"], 260)
        self.assertEqual(updated["render_options"]["blur"]["strength"], 30)

    def test_starts_subtitle_detection_as_a_background_job(self):
        project = self.service.create_project(str(self.source))
        with patch.object(self.service, "_spawn") as spawn:
            started = self.service.start_subtitle_detection(
                project["id"],
                sample_count=32,
            )

        self.assertEqual(started["status"], "detecting")
        self.assertEqual(started["subtitle_detection"]["requested_samples"], 32)
        spawn.assert_called_once()

    def test_starts_dubbing_with_selected_voice_and_audio_levels(self):
        project = self.service.create_project(str(self.source))
        self.service.save_subtitles(
            project["id"],
            [
                {
                    "id": "1",
                    "start": 0,
                    "end": 2,
                    "text": "Source",
                    "translated_text": "Xin chào",
                }
            ],
        )

        with patch.object(self.service, "_spawn") as spawn:
            started = self.service.start_dubbing(
                project["id"],
                voice="vi-VN-NamMinhNeural",
                rate=15,
                volume=125,
                original_volume=20,
            )

        self.assertEqual(started["status"], "dubbing")
        self.assertEqual(started["dubbing_options"]["voice"], "vi-VN-NamMinhNeural")
        self.assertEqual(started["dubbing_options"]["rate"], 15)
        self.assertEqual(started["dubbing_options"]["volume"], 125)
        self.assertEqual(started["dubbing_options"]["original_volume"], 20)
        spawn.assert_called_once()

    def test_starts_vieneu_dubbing_with_style(self):
        project = self.service.create_project(str(self.source))
        self.service.save_subtitles(
            project["id"],
            [
                {
                    "id": "1",
                    "start": 0,
                    "end": 2,
                    "text": "Source",
                    "translated_text": "Xin chào",
                }
            ],
        )

        with (
            patch("services.video_ai.video_ai_service.importlib.util.find_spec", return_value=object()),
            patch.object(self.service, "_spawn") as spawn,
        ):
            started = self.service.start_dubbing(
                project["id"],
                provider="vieneu",
                voice="Trúc Ly",
                style="doc_truyen",
            )

        self.assertEqual(started["dubbing_options"]["provider"], "vieneu")
        self.assertEqual(started["dubbing_options"]["voice"], "Trúc Ly")
        self.assertEqual(started["dubbing_options"]["style"], "doc_truyen")
        spawn.assert_called_once()

    def test_creates_vieneu_voice_preview_with_loaded_engine(self):
        class FakeEngine:
            def infer(self, text, **options):
                self.text = text
                self.options = options
                return b"audio"

            def save(self, _audio, output_path):
                Path(output_path).write_bytes(b"RIFF" + b"\0" * 256)

        engine = FakeEngine()
        self.service._vieneu_engine = engine

        result = self.service.create_tts_preview(
            provider="vieneu",
            voice="Trúc Ly",
            style="doc_truyen",
            text="Xin chào [cười]",
        )
        preview = self.service.tts_preview_path(result["preview_id"])

        self.assertEqual(result["voice"], "Trúc Ly")
        self.assertEqual(engine.options["style"], "doc_truyen")
        self.assertIn("[cười]", engine.text)
        self.assertTrue(preview.is_file())

    def test_reports_ready_vieneu_runtime_and_cache_location(self):
        self.service._vieneu_engine = object()

        status = self.service.tts_runtime_status("vieneu")

        self.assertEqual(status["state"], "ready")
        self.assertEqual(status["progress"], 100)
        self.assertTrue(status["cache_path"].endswith("video-ai-models\\huggingface"))

    def test_editing_translated_subtitles_invalidates_generated_dubbing(self):
        project = self.service.create_project(str(self.source))
        initial = [
            {
                "id": "1",
                "start": 0,
                "end": 2,
                "text": "Source",
                "translated_text": "Bản dịch cũ",
            }
        ]
        self.service.save_subtitles(project["id"], initial)

        def add_dubbing(current):
            current["dubbing_options"].update(
                {
                    "enabled": True,
                    "audio_path": str(Path(self.temp.name) / "dubbed.wav"),
                    "generated_at": "2026-07-29T00:00:00+00:00",
                    "line_count": 1,
                }
            )

        self.service._update(project["id"], add_dubbing)
        updated = self.service.save_subtitles(
            project["id"],
            [{**initial[0], "translated_text": "Bản dịch mới"}],
        )

        self.assertEqual(updated["dubbing_options"]["audio_path"], "")
        self.assertEqual(updated["dubbing_options"]["generated_at"], "")
        self.assertEqual(updated["dubbing_options"]["line_count"], 0)

    def test_project_reads_wait_until_an_active_write_finishes(self):
        project = self.service.create_project(str(self.source))
        started = threading.Event()
        finished = threading.Event()
        result: dict[str, object] = {}

        def read_project():
            started.set()
            result.update(self.service.get_project(project["id"]))
            finished.set()

        with self.service._lock:
            reader = threading.Thread(target=read_project)
            reader.start()
            self.assertTrue(started.wait(timeout=1))
            self.assertFalse(finished.wait(timeout=0.05))

        reader.join(timeout=1)
        self.assertTrue(finished.is_set())
        self.assertEqual(result["id"], project["id"])

    def test_translation_waits_and_retries_after_rate_limit(self):
        class RateLimitedError(RuntimeError):
            status_code = 429
            retry_after = 2

        class Translator:
            def __init__(self):
                self.calls = 0

            def translate_subtitles(self, subtitles, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RateLimitedError("Too many requests")
                return [
                    {"id": item["id"], "text": f"Translated {item['id']}"}
                    for item in subtitles
                ]

        translator = Translator()
        waits: list[float] = []
        self.service.translator = translator
        self.service._rate_limit_waiter = (
            lambda _cancel_event, seconds: waits.append(seconds) or False
        )
        project = self.service.create_project(str(self.source))
        self.service.save_subtitles(
            project["id"],
            [{"id": "1", "start": 0, "end": 2, "text": "Nguồn"}],
        )

        self.service._run_translation(project["id"], threading.Event())

        completed = self.service.get_project(project["id"])
        self.assertEqual(translator.calls, 2)
        self.assertEqual(waits, [1, 1, 1])
        self.assertEqual(completed["subtitles"][0]["translated_text"], "Translated 1")

    def test_rejects_invalid_subtitle_timing(self):
        project = self.service.create_project(str(self.source))
        with self.assertRaises(VideoAiError):
            self.service.save_subtitles(
                project["id"],
                [{"id": "1", "start": 3, "end": 2, "text": "Sai"}],
            )

    @unittest.skipUnless(
        shutil.which("ffmpeg") or Path("C:/ffmpeg/bin/ffmpeg.exe").is_file(),
        "FFmpeg is required for the render smoke test",
    )
    def test_renders_blurred_video_with_burned_subtitles(self):
        ffmpeg = shutil.which("ffmpeg") or "C:/ffmpeg/bin/ffmpeg.exe"
        source = Path(self.temp.name) / "render-source.mp4"
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x31527a:s=640x360:d=1:r=25",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=660:duration=1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(source),
            ],
            check=True,
            capture_output=True,
        )
        service = VideoAiService(
            project_root=Path(self.temp.name) / "render-state",
            scratch_root=Path(self.temp.name) / "render-scratch",
        )
        project = service.create_project(str(source))
        service.save_subtitles(
            project["id"],
            [
                {
                    "id": "1",
                    "start": 0.1,
                    "end": 0.9,
                    "text": "Phụ đề thử nghiệm",
                    "translated_text": "Dyna Video AI",
                }
            ],
        )
        service.start_render(
            project["id"],
            track="translated",
            blur={
                "enabled": True,
                "x": 0.05,
                "y": 0.72,
                "width": 0.9,
                "height": 0.2,
                "strength": 16,
            },
            style={"font_name": "Arial", "font_size": 34, "margin_v": 28, "outline": 2},
        )

        current = service.get_project(project["id"])
        for _ in range(200):
            if current["status"] != "rendering":
                break
            time.sleep(0.05)
            current = service.get_project(project["id"])

        self.assertEqual(current["status"], "completed", current.get("error"))
        self.assertGreater(Path(current["output_path"]).stat().st_size, 1024)
        self.assertEqual(current["subtitle_path"], "")
        self.assertFalse(Path(current["output_path"]).with_suffix(".srt").exists())

        dubbed = Path(self.temp.name) / "dubbed.wav"
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=330:duration=1",
                "-ac",
                "1",
                "-ar",
                "44100",
                "-c:a",
                "pcm_s16le",
                str(dubbed),
            ],
            check=True,
            capture_output=True,
        )

        def attach_dubbing(saved):
            saved["dubbing_options"].update(
                {
                    "enabled": True,
                    "voice": "vi-VN-HoaiMyNeural",
                    "rate": 0,
                    "volume": 120,
                    "original_volume": 15,
                    "audio_path": str(dubbed),
                    "generated_at": "2026-07-29T00:00:00+00:00",
                    "line_count": 1,
                }
            )

        service._update(project["id"], attach_dubbing)
        dubbed_output = Path(self.temp.name) / "render-with-dubbing.mp4"
        service.start_render(
            project["id"],
            track="translated",
            blur={"enabled": False},
            style={"font_name": "Arial", "font_size": 34},
            dubbing={
                "enabled": True,
                "voice": "vi-VN-HoaiMyNeural",
                "rate": 0,
                "volume": 120,
                "original_volume": 15,
            },
            output_path=str(dubbed_output),
        )

        current = service.get_project(project["id"])
        for _ in range(200):
            if current["status"] != "rendering":
                break
            time.sleep(0.05)
            current = service.get_project(project["id"])

        self.assertEqual(current["status"], "completed", current.get("error"))
        self.assertGreater(dubbed_output.stat().st_size, 1024)

    def test_original_volume_zero_is_preserved_across_save_and_render(self):
        project = self.service.create_project(str(self.source))
        self.service.save_subtitles(
            project["id"],
            [{"id": "1", "start": 0, "end": 2, "text": "Hi", "translated_text": "Chào"}],
        )
        # 1. Save editor state with original_volume = 0
        saved = self.service.save_editor_state(
            project["id"],
            subtitles=[{"id": "1", "start": 0, "end": 2, "text": "Hi", "translated_text": "Chào"}],
            dubbing={"enabled": False, "original_volume": 0},
        )
        self.assertEqual(saved["dubbing_options"]["original_volume"], 0)

        # 2. Re-fetch project to ensure disk persistence
        fetched = self.service.get_project(project["id"])
        self.assertEqual(fetched["dubbing_options"]["original_volume"], 0)

        # 3. Start render without dubbing parameter (should NOT reset original_volume to 18)
        output_file = Path(self.temp.name) / "test_out.mp4"
        with patch.object(self.service, "_spawn"):
            started = self.service.start_render(
                project["id"],
                track="translated",
                dubbing=None,
                output_path=str(output_file),
            )
        self.assertEqual(started["dubbing_options"]["original_volume"], 0)
        re_fetched = self.service.get_project(project["id"])
        self.assertEqual(re_fetched["dubbing_options"]["original_volume"], 0)
