import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.publishing.video_validation_service import (
    InvalidVideoFileError,
    validate_video_file,
)


class VideoValidationServiceTests(unittest.TestCase):
    def _make_file(self):
        handle = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        handle.write(b"video-data" * 300)
        handle.close()
        self.addCleanup(lambda: os.path.exists(handle.name) and os.remove(handle.name))
        return handle.name

    @patch("services.publishing.video_validation_service.resolve_ffprobe", return_value="ffprobe")
    @patch("services.publishing.video_validation_service.subprocess.run")
    def test_valid_video_requires_picture_audio_and_duration(self, run, _resolve):
        video_path = self._make_file()
        run.return_value = SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps({
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920},
                    {"codec_type": "audio", "codec_name": "aac"},
                ],
                "format": {"duration": "12.5"},
            }),
        )

        info = validate_video_file(video_path)

        self.assertEqual(info["duration_seconds"], 12.5)
        self.assertEqual((info["width"], info["height"]), (1080, 1920))
        self.assertTrue(info["has_audio"])

    @patch("services.publishing.video_validation_service.resolve_ffprobe", return_value="ffprobe")
    @patch("services.publishing.video_validation_service.subprocess.run")
    def test_video_without_audio_is_rejected(self, run, _resolve):
        video_path = self._make_file()
        run.return_value = SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps({
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920},
                ],
                "format": {"duration": "12.5"},
            }),
        )

        with self.assertRaisesRegex(InvalidVideoFileError, "âm thanh"):
            validate_video_file(video_path)


if __name__ == "__main__":
    unittest.main()
