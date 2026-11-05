"""SQLite schema and license key generation."""

import secrets
import sqlite3
import string
from contextlib import closing
from pathlib import Path

DATABASE = Path("/tmp/database.db")
KEY_ALPHABET = string.ascii_uppercase + string.digits


def generate_license_key():
    return "OQ-" + "-".join(
        "".join(secrets.choice(KEY_ALPHABET) for _ in range(4)) for _ in range(2)
    )


def init_db():
    with closing(sqlite3.connect(DATABASE, timeout=10)) as connection, connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT NOT NULL,
                full_name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                phone TEXT,
                license_key TEXT UNIQUE DEFAULT NULL,
                status TEXT DEFAULT 'pending',
                is_active BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


if __name__ == "__main__":
    init_db()
    print(f"База данных готова: {DATABASE}")
