"""Isolated tests for the current site maintenance, registration, and licensing contract."""

import base64
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class SiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="bidsy-site-tests-")
        cls.db_override = patch.dict(os.environ, {
            "BIDSY_DATABASE_PATH": str(Path(cls.temp_dir.name) / "site.db"),
            "OQ_ADMIN_PASSWORD": "test-only-admin-password",
            "BIDSY_MAINTENANCE": "1",
        })
        cls.db_override.start()
        cls.init_db = importlib.import_module("init_db")
        cls.site = importlib.import_module("app")
        cls.original_db = cls.init_db.DATABASE
        cls.init_db.DATABASE = Path(cls.temp_dir.name) / "site.db"
        cls.site.DATABASE = cls.init_db.DATABASE
        cls.init_db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls.init_db.DATABASE = cls.original_db
        cls.db_override.stop()
        cls.temp_dir.cleanup()

    def setUp(self):
        self.client = self.site.app.test_client()
        with self.site.get_db() as connection:
            connection.execute("DELETE FROM companies")
            connection.execute("DELETE FROM sqlite_sequence WHERE name = 'companies'")
        self.admin = {
            "Authorization": "Basic " + base64.b64encode(
                b"admin:test-only-admin-password"
            ).decode("ascii")
        }

    def register(self, email="test@example.com"):
        return self.client.post("/register", json={
            "company_name": "Тестовая компания",
            "full_name": "Тестовый пользователь",
            "email": email,
            "phone": "+7 900 000-00-00",
        })

    def test_maintenance_preview_health_and_admin_auth(self):
        self.assertEqual(self.client.get("/").status_code, 503)
        self.assertEqual(self.client.get("/?preview=1").status_code, 200)
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/admin").status_code, 401)
        self.assertEqual(self.client.get("/admin", headers=self.admin).status_code, 200)
        self.assertEqual(
            self.client.post("/api/admin/approve/1").status_code, 401
        )

    def test_public_landing_without_preview(self):
        with patch.dict(os.environ):
            os.environ.pop("BIDSY_MAINTENANCE", None)
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Из PDF и Excel", response.get_data(as_text=True))
        self.assertIn('id="signup-form"', response.get_data(as_text=True))

    def test_registration_creates_pending_request_without_license(self):
        response = self.register()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json, {"status": "success"})
        with self.site.get_db() as connection:
            row = connection.execute(
                "SELECT status, license_key, email FROM companies"
            ).fetchone()
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["license_key"])
        self.assertEqual(row["email"], "test@example.com")

    def test_duplicate_and_invalid_registration(self):
        self.assertEqual(self.register().status_code, 201)
        self.assertEqual(self.register().status_code, 409)
        self.assertEqual(self.client.post("/register", json={"email": "x@y.ru"}).status_code, 400)
        self.assertEqual(self.client.post("/register", json=[]).status_code, 400)

    def test_approval_generates_key_and_license_validation(self):
        self.register()
        with patch.object(self.site, "send_license_email") as send_email:
            response = self.client.post("/api/admin/approve/1", headers=self.admin)
        self.assertEqual(response.status_code, 200)
        key = response.json["license_key"]
        self.assertRegex(key, r"^OQ-[A-Z0-9]{4}-[A-Z0-9]{4}$")
        send_email.assert_called_once()
        valid = self.client.post("/api/validate", json={"license_key": key.lower()})
        self.assertEqual(valid.json, {"status": "success", "company": "Тестовая компания"})
        self.assertEqual(self.client.post("/api/admin/approve/1", headers=self.admin).status_code, 409)

    def test_rejected_and_deactivated_keys_are_invalid(self):
        self.register()
        rejected = self.client.post("/api/admin/reject/1", headers=self.admin)
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(self.client.post("/api/admin/approve/1", headers=self.admin).status_code, 409)
        self.register("second@example.com")
        with patch.object(self.site, "send_license_email"):
            key = self.client.post("/api/admin/approve/2", headers=self.admin).json["license_key"]
        with self.site.get_db() as connection:
            connection.execute("UPDATE companies SET is_active = 0 WHERE license_key = ?", (key,))
        self.assertEqual(self.client.post("/api/validate", json={"license_key": key}).json["status"], "error")
        self.assertEqual(self.client.post("/api/validate", json={"license_key": "invalid"}).json["status"], "error")

    def test_mail_failure_rolls_back_approval(self):
        self.register()
        with patch.object(self.site, "send_license_email", side_effect=RuntimeError("mock failure")):
            response = self.client.post("/api/admin/approve/1", headers=self.admin)
        self.assertEqual(response.status_code, 502)
        with self.site.get_db() as connection:
            row = connection.execute("SELECT status, license_key FROM companies").fetchone()
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["license_key"])


if __name__ == "__main__":
    unittest.main()
