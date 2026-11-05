"""Focused checks for registration, admin access, and license validation."""

import base64
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import app as site
import init_db


class SiteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_site_db = site.DATABASE
        self.original_init_db = init_db.DATABASE
        test_db = Path(self.temp_dir.name) / "database.db"
        site.DATABASE = test_db
        init_db.DATABASE = test_db
        init_db.init_db()
        self.client = site.app.test_client()

    def tearDown(self):
        site.DATABASE = self.original_site_db
        init_db.DATABASE = self.original_init_db
        self.temp_dir.cleanup()

    def register(self):
        return self.client.post(
            "/register",
            json={"company_name": "Тестовая компания", "email": "Test@Example.com", "phone": "+7 900 000-00-00"},
        )

    def test_landing_and_admin_auth(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/admin").status_code, 401)
        credentials = base64.b64encode(b"admin:OQsuperadmin2026").decode("ascii")
        response = self.client.get("/admin", headers={"Authorization": f"Basic {credentials}"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Заявки компаний", response.get_data(as_text=True))

    def test_register_and_validate(self):
        response = self.register()
        self.assertEqual(response.status_code, 201)
        key = response.json["license_key"]
        self.assertRegex(key, r"^OQ-[A-Z0-9]{4}(?:-[A-Z0-9]{4}){2}$")
        valid = self.client.post("/api/validate", json={"license_key": key})
        self.assertEqual(valid.json, {"status": "success", "company": "Тестовая компания"})
        with closing(sqlite3.connect(site.DATABASE)) as connection, connection:
            connection.execute("UPDATE companies SET is_active = 0 WHERE license_key = ?", (key,))
        inactive = self.client.post("/api/validate", json={"license_key": key})
        self.assertEqual(inactive.json, {"status": "error", "message": "Неверный ключ"})

    def test_duplicate_email_and_bad_input(self):
        self.assertEqual(self.register().status_code, 201)
        self.assertEqual(self.register().status_code, 409)
        self.assertEqual(self.client.post("/register", json={"email": "x@y.ru"}).status_code, 400)
        self.assertEqual(self.client.post("/api/validate", json={"license_key": "invalid"}).json["status"], "error")


if __name__ == "__main__":
    unittest.main()
