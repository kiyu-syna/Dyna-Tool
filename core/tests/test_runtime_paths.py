import tempfile
import unittest
from pathlib import Path

from core.runtime_paths import (
    logs_dir,
    profile_state_dir,
    state_dir,
    temp_dir,
)


class RuntimePathTests(unittest.TestCase):
    def test_paths_share_one_runtime_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(state_dir(root), root / "runtime" / "state")
            self.assertEqual(logs_dir(root), root / "runtime" / "logs")
            self.assertEqual(temp_dir(root), root / "runtime" / "tmp")
            self.assertEqual(
                profile_state_dir(root),
                root / "runtime" / "state" / "profile-automation",
            )
if __name__ == "__main__":
    unittest.main()
