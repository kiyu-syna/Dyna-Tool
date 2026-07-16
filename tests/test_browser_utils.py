import os
import tempfile
import unittest
from unittest.mock import patch

from automation.browser_utils import (
    PLAYWRIGHT_REMOTE_FILE_LIMIT_BYTES,
    set_video_file_background,
)


class FakeCdpSession:
    def __init__(self):
        self.calls = []
        self.listeners = {}
        self.detached = False

    def on(self, event_name, callback):
        self.listeners[event_name] = callback

    def send(self, method, params=None):
        self.calls.append((method, params or {}))
        if method == "DOM.getDocument":
            return {"root": {"nodeId": 1}}
        if method == "DOM.querySelectorAll":
            return {"nodeIds": []}
        return {}

    def open_file_chooser(self, backend_node_id=42):
        self.listeners["Page.fileChooserOpened"](
            {"backendNodeId": backend_node_id, "mode": "selectSingle"}
        )

    def detach(self):
        self.detached = True


class FakeContext:
    def __init__(self, session):
        self.session = session

    def new_cdp_session(self, page):
        return self.session


class FakePage:
    def __init__(self, session):
        self.context = FakeContext(session)

    def wait_for_timeout(self, timeout_ms):
        return None

    def expect_file_chooser(self, timeout):
        raise AssertionError("Large files must not use Playwright file transfer")


class BrowserUtilsTests(unittest.TestCase):
    def test_small_video_with_trigger_also_uses_local_cdp_path(self):
        session = FakeCdpSession()
        page = FakePage(session)

        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = os.path.join(temp_dir, "small-video.mp4")
            with open(video_path, "wb") as file_handle:
                file_handle.write(b"video")

            def trigger_file_chooser():
                session.open_file_chooser()

            set_video_file_background(page, video_path, trigger_file_chooser)

        set_file_calls = [
            params
            for method, params in session.calls
            if method == "DOM.setFileInputFiles"
        ]
        self.assertEqual(len(set_file_calls), 1)
        self.assertEqual(set_file_calls[0]["files"], [os.path.abspath(video_path)])
        self.assertEqual(set_file_calls[0]["backendNodeId"], 42)
        self.assertTrue(session.detached)

    def test_large_video_uses_local_cdp_path_instead_of_playwright_transfer(self):
        session = FakeCdpSession()
        page = FakePage(session)

        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = os.path.join(temp_dir, "large-video.mp4")
            with open(video_path, "wb") as file_handle:
                file_handle.write(b"video")

            def trigger_file_chooser():
                session.open_file_chooser()

            with patch(
                "automation.browser_utils.os.path.getsize",
                return_value=PLAYWRIGHT_REMOTE_FILE_LIMIT_BYTES + 1,
            ):
                set_video_file_background(page, video_path, trigger_file_chooser)

        set_file_calls = [
            params
            for method, params in session.calls
            if method == "DOM.setFileInputFiles"
        ]
        self.assertEqual(len(set_file_calls), 1)
        self.assertEqual(set_file_calls[0]["files"], [os.path.abspath(video_path)])
        self.assertEqual(set_file_calls[0]["backendNodeId"], 42)
        self.assertTrue(session.detached)


if __name__ == "__main__":
    unittest.main()
