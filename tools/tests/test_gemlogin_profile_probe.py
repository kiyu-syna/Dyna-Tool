import json
import tempfile
import unittest
from pathlib import Path

from tools import gemlogin_profile_probe as probe


class GemLoginProfileProbeTests(unittest.TestCase):
    def make_profile(self, root: Path) -> Path:
        root.mkdir(parents=True)
        (root / "Default").mkdir()
        (root / "Local State").write_text("{}", encoding="utf-8")
        (root / "Default" / "Preferences").write_text("{}", encoding="utf-8")
        return root

    def test_validate_accepts_complete_copy_outside_gemlogin(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = self.make_profile(Path(temporary) / "profiles" / "1")
            probe.validate_profile_copy(profile)

    def test_validate_refuses_original_gemlogin_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = self.make_profile(
                Path(temporary) / ".gemlogin" / "profile" / "profiles" / "1"
            )
            with self.assertRaisesRegex(probe.ProbeError, "Từ chối"):
                probe.validate_profile_copy(profile)

    def test_rewrite_changes_only_copy_reference_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = self.make_profile(Path(temporary) / "profiles" / "1")
            local_state = {
                "autofill": {
                    "states_data_dir": (
                        r"C:\Users\Example\.gemlogin\profile\profiles\1"
                        r"\AutofillStates\version"
                    )
                },
                "unrelated": "keep-me",
            }
            (profile / "Local State").write_text(
                json.dumps(local_state), encoding="utf-8"
            )

            backup = probe.rewrite_original_references_in_copy(profile)

            self.assertIsNotNone(backup)
            self.assertTrue(backup.is_file())
            rewritten = json.loads((profile / "Local State").read_text(encoding="utf-8"))
            self.assertEqual(
                rewritten["autofill"]["states_data_dir"],
                str(profile) + r"\AutofillStates\version",
            )
            self.assertEqual(rewritten["unrelated"], "keep-me")
            self.assertEqual(probe.find_original_profile_references(rewritten), [])

    def test_sanitized_url_removes_query_and_fragment(self):
        self.assertEqual(
            probe.sanitized_url("https://example.com/path?token=secret#section"),
            "https://example.com/path",
        )


if __name__ == "__main__":
    unittest.main()
