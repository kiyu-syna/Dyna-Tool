import importlib
import unittest


class AppImportTests(unittest.TestCase):
    def test_main_imports_all_routers(self):
        module = importlib.import_module("app.main")

        self.assertEqual(module.app.title, "Dyna Telegram API")


if __name__ == "__main__":
    unittest.main()
