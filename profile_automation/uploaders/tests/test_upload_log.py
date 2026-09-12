import unittest
from unittest.mock import patch

from profile_automation.uploaders import upload_log


class UploadLogTests(unittest.TestCase):
    def test_section_heading_is_uppercase_and_contains_profile_video_and_rules(self):
        with patch.object(upload_log, "logger") as mocked_logger:
            upload_log.log_upload_section(
                "TikTok",
                "3",
                "7681",
                "Bắt đầu",
            )

        template, *args = mocked_logger.info.call_args.args
        rendered = template % tuple(args)
        self.assertIn("-------------------- BẮT ĐẦU ĐĂNG TIKTOK", rendered)
        self.assertIn("[Profile 3]", rendered)
        self.assertIn("VIDEO 7681", rendered)

    def test_failed_section_uses_requested_error_level(self):
        with patch.object(upload_log, "logger") as mocked_logger:
            upload_log.log_upload_section(
                "Facebook",
                "2",
                "99",
                "Kết thúc",
                status="Thất bại",
                level="error",
            )

        mocked_logger.error.assert_called_once()
        template, *args = mocked_logger.error.call_args.args
        self.assertIn("KẾT THÚC ĐĂNG FACEBOOK | THẤT BẠI", template % tuple(args))


if __name__ == "__main__":
    unittest.main()
