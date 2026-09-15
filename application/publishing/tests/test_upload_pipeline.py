import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from application.publishing.upload_pipeline import UploadPipeline
from profile_automation.uploaders.base_uploader import UploadUnconfirmed


class _UnconfirmedUploader:
    def upload(self, _video_path, _video, _profile):
        raise UploadUnconfirmed("TikTok không xác nhận trong 45 giây.")


class UploadPipelineTests(unittest.TestCase):
    @patch("application.publishing.upload_pipeline.workload_slot", return_value=nullcontext())
    def test_unconfirmed_publish_is_reported_as_failure(self, _workload_slot):
        pipeline = UploadPipeline()
        pipeline.tiktok_uploader = _UnconfirmedUploader()
        statuses = []

        results = pipeline.run(
            "video.mp4",
            SimpleNamespace(aweme_id="video-1"),
            {
                "id": "3",
                "tiktok": {"enabled": True},
                "facebook": {"enabled": False},
                "youtube": {"enabled": False},
            },
            on_platform_status=lambda *args: statuses.append(args),
        )

        self.assertEqual(results, {"tiktok": False})
        self.assertEqual(statuses[-1][0:2], ("tiktok", "failed"))
        self.assertIn("không xác nhận", statuses[-1][2])


if __name__ == "__main__":
    unittest.main()
