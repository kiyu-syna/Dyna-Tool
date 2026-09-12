import unittest

from services.runtime.resource_monitor_service import ProfileResourceMonitor


class ProfileResourceMonitorTests(unittest.TestCase):
    def test_debug_address_port_is_parsed(self):
        self.assertEqual(ProfileResourceMonitor._port("127.0.0.1:9222"), 9222)
        self.assertEqual(ProfileResourceMonitor._port("http://127.0.0.1:9333"), 9333)

    def test_all_configured_gemlogin_ids_are_attributed_to_profile(self):
        ids = ProfileResourceMonitor._gemlogin_ids(
            "2",
            {
                "douyin": {"gemlogin_profile_id": "20"},
                "tiktok": {"gemlogin_profile_id": "21"},
                "youtube": {"gemlogin_profile_id": "22"},
                "facebook": {"gemlogin_profile_id": "23"},
            },
        )
        self.assertEqual(ids, ["2", "20", "21", "22", "23"])


if __name__ == "__main__":
    unittest.main()
