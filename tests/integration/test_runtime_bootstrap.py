import unittest
from unittest.mock import Mock, patch

import main


class RuntimeBootstrapTests(unittest.TestCase):
    @patch("main.subprocess.Popen")
    @patch("main.os.path.exists", return_value=True)
    def test_default_desktop_launcher_uses_built_electron_app(self, _exists, popen):
        process = Mock()
        process.wait.return_value = 0
        popen.return_value = process

        exit_code = main.launch_desktop_app()

        self.assertEqual(exit_code, 0)
        command = popen.call_args.args[0]
        self.assertTrue(command[0].endswith("electron.exe"))
        self.assertTrue(command[1].endswith("desktop"))
        self.assertEqual(popen.call_args.kwargs["cwd"], command[1])

    @patch("main.os.path.exists", return_value=False)
    def test_desktop_launcher_reports_missing_electron(self, _exists):
        with self.assertRaisesRegex(RuntimeError, "Chưa cài Electron"):
            main.launch_desktop_app()

    @patch("main.os.path.exists", side_effect=[True, False])
    def test_desktop_launcher_reports_missing_frontend_build(self, _exists):
        with self.assertRaisesRegex(RuntimeError, "chưa được build"):
            main.launch_desktop_app()


if __name__ == "__main__":
    unittest.main()
