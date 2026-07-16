import threading
import time
import unittest
from unittest.mock import patch

from services.workload_coordinator import PriorityResourcePool, priority_for_job


class WorkloadCoordinatorTests(unittest.TestCase):
    def test_partial_upload_has_priority_over_new_job(self):
        partial = {"platforms": {"tiktok": {"status": "success"}}}
        self.assertLess(priority_for_job(partial), priority_for_job({}))

    def test_waiters_are_admitted_by_priority(self):
        pool = PriorityResourcePool("upload", 1)
        holder = pool.acquire(profile_id="0", video_id="holder")
        order = []

        def wait_for_slot(profile_id, priority):
            token = pool.acquire(
                profile_id=profile_id,
                video_id=f"video-{profile_id}",
                priority=priority,
            )
            order.append(profile_id)
            pool.release(token)

        with patch("services.workload_coordinator.config.load_settings", return_value={"MAX_CONCURRENT_UPLOADS": 1}):
            low = threading.Thread(target=wait_for_slot, args=("low", 100))
            high = threading.Thread(target=wait_for_slot, args=("high", 10))
            low.start()
            high.start()
            deadline = time.monotonic() + 2
            while pool.snapshot()["waiting_count"] < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            pool.release(holder)
            low.join(timeout=2)
            high.join(timeout=2)

        self.assertEqual(order, ["high", "low"])


if __name__ == "__main__":
    unittest.main()
