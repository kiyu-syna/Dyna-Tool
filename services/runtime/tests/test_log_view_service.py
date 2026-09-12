import logging
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path

from services.runtime.log_view_service import build_log_snapshot, parse_log_entries, reset_log_files
from application.tracking.profile_management_service import ProfileManagementService


class LogViewServiceTests(unittest.TestCase):
    def test_structured_lines_have_time_level_profile_and_vietnamese_message(self):
        entries = parse_log_entries(
            [
                "2026-07-18 13:01:02 | INFO | [Profile 1] Opening YouTube upload page: https://example.test",
                "2026-07-18 13:01:03 | ERROR | [Profile 2] YouTube Shorts upload failed: timeout",
                "Traceback (most recent call last):",
                "  File \"worker.py\", line 10, in run",
            ]
        )

        self.assertEqual(entries[0]["time"], "13:01:02")
        self.assertEqual(entries[0]["level"], "INFO")
        self.assertEqual(entries[0]["profile_id"], "1")
        self.assertIn("Đang mở trang đăng YouTube", entries[0]["message"])
        self.assertEqual(entries[1]["profile_id"], "2")
        self.assertEqual(len(entries), 2)

    def test_hides_python_traceback_but_keeps_the_main_error(self):
        entries = parse_log_entries(
            [
                "2026-07-18 13:01:03 | ERROR | [Profile 2] Lỗi đăng: Target page, context or browser has been closed",
                "Traceback (most recent call last):",
                '  File "worker.py", line 10, in run',
                "playwright._impl._errors.TargetClosedError: page closed",
            ]
        )

        self.assertEqual(len(entries), 1)
        self.assertIn("Trang hoặc trình duyệt đã bị đóng", entries[0]["message"])

    def test_runtime_messages_are_normalized_to_vietnamese(self):
        entries = parse_log_entries(
            [
                "2026-07-18 13:01:02 | INFO | [Maintenance] Đã hoàn tất xoay dữ liệu runtime: {}",
                "2026-07-18 13:01:03 | WARNING | [Tiến trình đăng] Bỏ qua youtube cho video 123 vì đã đăng thành công trước đó.",
                "2026-07-18 13:01:04 | INFO | [Điều phối] Profile 2, video 456 đang chờ tài nguyên upload (ưu tiên=3).",
            ]
        )

        self.assertEqual(entries[0]["message"], "[Bảo trì] Đã hoàn tất dọn dữ liệu vận hành: {}")
        self.assertEqual(
            entries[1]["message"],
            "[Tiến trình đăng] Bỏ qua youtube cho video 123 vì đã đăng thành công trước đó.",
        )
        self.assertEqual(
            entries[2]["message"],
            "[Điều phối] Profile 2, video 456 đang chờ tài nguyên đăng video (ưu tiên=3).",
        )

    def test_hides_verbose_douyin_runtime_and_response_lines(self):
        entries = parse_log_entries(
            [
                "2026-07-18 13:01:02 | INFO | [DOUYIN RUNTIME] profile=3 provider=local_chromium headless=True",
                "2026-07-18 13:01:03 | INFO | [DOUYIN RESPONSE] HTTP 200 URL=https://example.test/?token=secret",
                "2026-07-18 13:01:04 | INFO | [Profile 3] QUÉT DOUYIN THÀNH CÔNG: lấy 4 video hợp lệ.",
            ]
        )

        self.assertEqual(len(entries), 1)
        self.assertIn("QUÉT DOUYIN THÀNH CÔNG", entries[0]["message"])

    def test_snapshot_reads_rotated_file_and_configured_profile_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_file = root / "system.log"
            Path(f"{log_file}.1").write_text(
                "2026-07-18 12:00:00 | WARNING | [Profile 1] Cảnh báo cũ\n",
                encoding="utf-8",
            )
            log_file.write_text(
                "2026-07-18 13:00:00 | INFO | Hệ thống sẵn sàng\n",
                encoding="utf-8",
            )
            profiles = ProfileManagementService(root / "profiles", root / "state")
            profiles.create("1", "Kênh chính")

            snapshot = build_log_snapshot(log_file, limit=100, profiles=profiles)

            self.assertEqual(snapshot["file_count"], 2)
            self.assertEqual(snapshot["total"], 2)
            self.assertEqual(snapshot["profiles"], [{"id": "1", "name": "Kênh chính"}])
            self.assertEqual(snapshot["entries"][0]["profile_id"], "1")
            self.assertEqual(snapshot["entries"][1]["profile_id"], "")
            self.assertTrue(snapshot["updated_at"])

    def test_reset_clears_live_and_rotated_logs_without_breaking_new_writes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "system.log"
            rotated = Path(f"{log_file}.1")
            test_logger = logging.getLogger(f"dyna-test-reset-{id(self)}")
            test_logger.setLevel(logging.INFO)
            test_logger.propagate = False
            handler = RotatingFileHandler(log_file, maxBytes=1024, backupCount=2, encoding="utf-8")
            test_logger.addHandler(handler)
            try:
                test_logger.info("old live line")
                handler.flush()
                rotated.write_text("old rotated line\n", encoding="utf-8")

                result = reset_log_files(log_file)

                self.assertEqual(log_file.read_text(encoding="utf-8"), "")
                self.assertFalse(rotated.exists())
                self.assertEqual(result["removed_rotated"], 1)

                test_logger.info("new line")
                handler.flush()
                self.assertIn("new line", log_file.read_text(encoding="utf-8"))
            finally:
                test_logger.removeHandler(handler)
                handler.close()

    def test_cursor_returns_only_new_lines_and_resets_after_truncation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "system.log"
            log_file.write_text(
                "2026-07-18 13:00:00 | INFO | first\n",
                encoding="utf-8",
            )
            initial = build_log_snapshot(log_file, limit=100)

            with log_file.open("a", encoding="utf-8") as handle:
                handle.write("2026-07-18 13:00:01 | INFO | second\n")
            delta = build_log_snapshot(log_file, limit=100, cursor=initial["cursor"])

            self.assertFalse(delta["reset"])
            self.assertEqual([entry["message"] for entry in delta["entries"]], ["second"])

            log_file.write_text(
                "2026-07-18 13:00:02 | INFO | replacement\n",
                encoding="utf-8",
            )
            reset = build_log_snapshot(log_file, limit=100, cursor=delta["cursor"])
            self.assertTrue(reset["reset"])
            self.assertEqual(reset["entries"][0]["message"], "replacement")


if __name__ == "__main__":
    unittest.main()
