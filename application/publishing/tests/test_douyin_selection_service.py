import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from application.publishing.douyin_selection_service import DouyinSelectionService
from application.publishing.manual_publish_service import ManualPublishTarget


class DouyinSelectionServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.manual_publish = Mock()
        self.manual_publish.submit.return_value = {
            "ok": True,
            "batch_id": "batch",
            "file_count": 2,
            "job_count": 2,
        }
        self.downloaded_ids = []

        def downloader(item, destination):
            video_id = str(item["video_id"])
            if video_id == "10002":
                raise RuntimeError("video unavailable")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"video")
            self.downloaded_ids.append(video_id)
            return destination

        self.service = DouyinSelectionService(
            self.manual_publish,
            state_file=root / "state.json",
            media_root=root / "media",
            downloader=downloader,
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def videos():
        return [
            {
                "video_id": video_id,
                "source_url": f"https://www.douyin.com/video/{video_id}",
                "description": f"Caption {video_id}",
                "download_url": f"https://cdn.example/{video_id}.mp4",
                "content_type": "video",
            }
            for video_id in ("10001", "10002", "10003")
        ]

    def test_completes_selection_with_original_order_and_deduplicates_ids(self):
        session = self.service.create("https://www.douyin.com/user/source")
        items = self.videos()
        items.append(dict(items[0]))

        completed = self.service.complete(session["id"], items)

        self.assertEqual(completed["status"], "ready")
        self.assertEqual(completed["selected_count"], 3)
        self.assertEqual(
            [item["selected_order"] for item in completed["items"]],
            [1, 2, 3],
        )
        self.assertTrue(completed["focus_requested"])

    def test_failed_download_is_skipped_and_later_video_takes_its_schedule_slot(self):
        session = self.service.create("https://www.douyin.com/user/source")
        completed = self.service.complete(session["id"], self.videos())
        schedule = [
            "2099-01-01T01:00:00Z",
            "2099-01-01T02:00:00Z",
            "2099-01-01T03:00:00Z",
        ]

        self.service.submit(
            completed["id"],
            batch_name="Lô Douyin Ma Chao",
            items=[
                {
                    "video_id": item["video_id"],
                    "caption": item["description"],
                    "scheduled_at": schedule[index],
                }
                for index, item in enumerate(completed["items"])
            ],
            targets=(ManualPublishTarget("1", ("tiktok",)),),
        )

        deadline = time.monotonic() + 3
        result = self.service.get(completed["id"])
        while result and result["status"] == "preparing" and time.monotonic() < deadline:
            time.sleep(0.02)
            result = self.service.get(completed["id"])

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["published_count"], 2)
        self.assertEqual(result["failed_count"], 1)
        request = self.manual_publish.submit.call_args.args[0]
        self.assertEqual(request.batch_name, "Lô Douyin Ma Chao")
        self.assertEqual([item.video_id for item in request.items], ["10001", "10003"])
        self.assertEqual(
            [item.scheduled_at for item in request.items],
            schedule[:2],
        )


if __name__ == "__main__":
    unittest.main()
