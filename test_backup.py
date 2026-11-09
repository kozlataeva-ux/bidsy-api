"""Isolated checks for authenticated full SQLite snapshots; no production DB access."""

import base64
import importlib.util
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


class DatabaseBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bidsy-backup-test-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic.sqlite3"
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT NOT NULL, full_name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL, phone TEXT,
                license_key TEXT UNIQUE, status TEXT,
                is_active BOOLEAN, created_at TEXT, future_field BLOB
            )""")
            db.execute("""INSERT INTO companies VALUES
                (1, 'Synthetic company', 'Synthetic Person', 'fake@example.invalid',
                 NULL, 'SYNTHETIC-KEY', 'approved', 0, '2000-01-01', X'0011FF')""")
            db.execute("INSERT INTO companies(id, company_name, full_name, email) VALUES (99, 'delete', 'delete', 'delete@example.invalid')")
            db.execute("DELETE FROM companies WHERE id = 99")
            db.execute("CREATE TABLE extra_table (value TEXT)")
            db.execute("INSERT INTO extra_table VALUES ('synthetic extra data')")
        self.source_before = self.path.read_bytes()
        fake_init = types.ModuleType("init_db")
        fake_init.DATABASE = self.path
        fake_init.generate_license_key = lambda: "SYNTHETIC-UNUSED"
        fake_init.init_db = lambda: None
        app_file = Path(__file__).with_name("app.py")
        spec = importlib.util.spec_from_file_location("isolated_backup_app", app_file)
        self.site = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"init_db": fake_init}):
            spec.loader.exec_module(self.site)
        self.client = self.site.app.test_client()
        self.env = patch.dict(os.environ, {"OQ_ADMIN_PASSWORD": "synthetic-test-password", "BIDSY_MAINTENANCE": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        credentials = base64.b64encode(b"admin:synthetic-test-password").decode("ascii")
        self.auth = {"Authorization": "Basic " + credentials}

    def test_requires_admin_and_disables_caching(self):
        for auth in ({}, {"Authorization": "Basic " + base64.b64encode(b"admin:wrong").decode("ascii")}):
            response = self.client.get("/api/admin/backup", headers=auth)
            self.assertEqual(response.status_code, 401)
            self.assertIn("Basic", response.headers["WWW-Authenticate"])
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertNotIn(b"SQLite format", response.data)

    def test_full_snapshot_is_consistent_and_source_is_unchanged(self):
        # Keep a committed change in WAL while the endpoint backs up the DB.
        with closing(sqlite3.connect(self.path)) as live:
            live.execute("PRAGMA journal_mode=WAL")
            live.execute("INSERT INTO extra_table VALUES ('committed in WAL')")
            live.commit()
            expected = list(live.iterdump())
            response = self.client.get("/api/admin/backup", headers=self.auth)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "application/vnd.sqlite3")
            self.assertIn('attachment; filename="bidsy-backup.sqlite3"', response.headers["Content-Disposition"])
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertTrue(response.data.startswith(b"SQLite format 3\x00"))
            with closing(sqlite3.connect(":memory:")) as restored:
                restored.deserialize(response.data)
                self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(list(restored.iterdump()), expected)
                self.assertEqual(restored.execute("SELECT is_active, license_key, future_field FROM companies").fetchone(), (0, "SYNTHETIC-KEY", b"\x00\x11\xff"))
                self.assertEqual(restored.execute("SELECT seq FROM sqlite_sequence WHERE name='companies'").fetchone()[0], 99)
            self.assertEqual(list(live.iterdump()), expected)
            self.assertEqual(self.path.read_bytes(), self.source_before)
        self.assertEqual(sorted(p.name for p in Path(self.temp.name).iterdir()), [self.path.name])


if __name__ == "__main__":
    unittest.main()
