"""Privacy rendering uses server configuration, never real operator details."""
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class PrivacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="bidsy-privacy-")
        import init_db
        cls.init_db = init_db
        cls.original_db = init_db.DATABASE
        init_db.DATABASE = Path(cls.temp.name) / "test.db"
        cls.site = importlib.import_module("app")

    @classmethod
    def tearDownClass(cls):
        cls.init_db.DATABASE = cls.original_db
        cls.temp.cleanup()

    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "BIDSY_OPERATOR_NAME": "",
            "BIDSY_OPERATOR_EMAIL": "",
            "BIDSY_OPERATOR_ADDRESS": "",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = self.site.app.test_client()

    def configure(self):
        os.environ.update({
            "BIDSY_OPERATOR_NAME": "Тестовый оператор",
            "BIDSY_OPERATOR_EMAIL": "operator@example.com",
            "BIDSY_OPERATOR_ADDRESS": "Тестовый адрес",
        })

    def test_missing_configuration_is_unavailable(self):
        response = self.client.get("/privacy")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("{{", response.get_data(as_text=True))

    def test_partial_configuration_is_unavailable(self):
        self.configure()
        os.environ["BIDSY_OPERATOR_ADDRESS"] = "  "
        self.assertEqual(self.client.get("/privacy").status_code, 503)

    def test_configured_page_and_contacts(self):
        self.configure()
        response = self.client.get("/privacy")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        html = response.get_data(as_text=True)
        self.assertIn("Тестовый оператор", html)
        self.assertIn("Тестовый адрес", html)
        self.assertEqual(html.count('href="mailto:operator%40example.com"'), 2)
        self.assertNotIn("{{ operator.", html)

    def test_values_are_html_escaped(self):
        self.configure()
        os.environ["BIDSY_OPERATOR_NAME"] = '<script>alert("test")</script>'
        html = self.client.get("/privacy").get_data(as_text=True)
        self.assertNotIn('<script>alert("test")</script>', html)
        self.assertIn("&lt;script&gt;", html)

    def test_updated_environment_is_used_without_code_change(self):
        self.configure()
        self.client.get("/privacy")
        os.environ["BIDSY_OPERATOR_NAME"] = "Другой тестовый оператор"
        html = self.client.get("/privacy").get_data(as_text=True)
        self.assertIn("Другой тестовый оператор", html)


if __name__ == "__main__":
    unittest.main()
