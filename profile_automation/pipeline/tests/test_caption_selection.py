import unittest
from types import SimpleNamespace

from profile_automation.pipeline import profile_worker


class CaptionSelectionTests(unittest.TestCase):
    def test_default_caption_is_used_without_telegram(self):
        video = SimpleNamespace(aweme_id="123", desc="Mô tả gốc")
        profile = {
            "default_caption": "Mô tả mặc định #valorant",
            "caption_options": {"douyin_use_original_desc": False},
        }

        caption = profile_worker.resolve_runtime_caption(
            profile,
            "2",
            video,
            source_platform="douyin",
        )

        self.assertEqual(caption, "Mô tả mặc định #valorant")

    def test_source_caption_is_used_when_enabled(self):
        video = SimpleNamespace(aweme_id="123", desc="Mô tả Douyin gốc")
        profile = {
            "default_caption": "Mô tả mặc định",
            "caption_options": {"douyin_use_original_desc": True},
        }

        caption = profile_worker.resolve_runtime_caption(
            profile,
            "2",
            video,
            source_platform="douyin",
        )

        self.assertEqual(caption, "Mô tả Douyin gốc")

    def test_source_caption_is_fallback_when_default_is_empty(self):
        video = SimpleNamespace(aweme_id="123", desc="Mô tả gốc")
        profile = {
            "default_caption": "",
            "caption_options": {"douyin_use_original_desc": False},
        }

        caption = profile_worker.resolve_runtime_caption(
            profile,
            "2",
            video,
            source_platform="douyin",
        )

        self.assertEqual(caption, "Mô tả gốc")


if __name__ == "__main__":
    unittest.main()
