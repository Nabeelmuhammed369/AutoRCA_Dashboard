"""
tests/conftest.py — Shared fixtures for AutoRCA test suite
"""

import os
import sys
import pytest
import sqlite3

# ── Fix: add project root to sys.path so 'Core', 'Monitors' are importable ───
# This is required for CI (GitHub Actions) where the runner's working directory
# may not automatically include the repo root in Python's module search path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Log file fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def empty_log_file(tmp_path):
    f = tmp_path / "empty.log"
    f.write_text("")
    return str(f)


@pytest.fixture
def log_file_with_errors(tmp_path):
    f = tmp_path / "app.log"
    f.write_text(
        "2026-03-05 10:00:01 INFO  Application started\n"
        "2026-03-05 10:01:00 ERROR [Database] DB_CONN_FAIL: Connection refused by host\n"
        "2026-03-05 10:01:05 ERROR [Database] DEADLOCK detected on table users\n"
        "2026-03-05 10:02:10 ERROR [Network] Connection timed out after 30s\n"
        "2026-03-05 10:03:15 ERROR [API] Gateway returned 502 Bad Gateway\n"
        "2026-03-05 10:04:20 ERROR [Firewall] Access denied — rule violation\n"
        "2026-03-05 10:05:25 ERROR [ActiveDirectory] LDAP bind failed for user admin\n"
        "2026-03-05 10:06:30 ERROR NullPointerException in UserService.java:88\n"
        "2026-03-05 10:07:00 WARN  High memory usage detected\n"
        "2026-03-05 10:08:00 INFO  Health check passed\n"
    )
    return str(f)


@pytest.fixture
def log_file_with_critical(tmp_path):
    f = tmp_path / "critical.log"
    f.write_text(
        "2026-03-05 10:00:01 ERROR [Database] DB_CONN_FAIL: Connection refused\n"
        "2026-03-05 10:01:00 CRITICAL [Database] Complete database outage\n"
        "2026-03-05 10:02:00 CRITICAL [Network] Network partition detected\n"
        "2026-03-05 10:03:00 ERROR [API] Gateway timeout\n"
    )
    return str(f)


@pytest.fixture
def log_file_no_errors(tmp_path):
    f = tmp_path / "clean.log"
    f.write_text(
        "2026-03-05 10:00:01 INFO  Application started\n"
        "2026-03-05 10:01:00 INFO  Health check passed\n"
        "2026-03-05 10:02:00 WARN  Memory usage at 75%\n"
    )
    return str(f)


@pytest.fixture
def nonexistent_log_file(tmp_path):
    return str(tmp_path / "does_not_exist.log")


# ── Database fixtures — use context managers to prevent ResourceWarning ───────

def _make_db(tmp_path, filename, setup_sql):
    """Helper: create a SQLite DB, run setup SQL, ensure connection is closed."""
    db_path = str(tmp_path / filename)
    with sqlite3.connect(db_path) as conn:   # 'with' ensures conn.close() is called
        for statement in setup_sql:
            conn.execute(statement)
        conn.commit()
    return db_path


@pytest.fixture
def clean_db(tmp_path):
    """SQLite DB with a users table — all emails present."""
    return _make_db(tmp_path, "clean.db", [
        "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL)",
        "INSERT INTO users VALUES (1, 'alice@example.com')",
        "INSERT INTO users VALUES (2, 'bob@example.com')",
        "INSERT INTO users VALUES (3, 'carol@example.com')",
    ])


@pytest.fixture
def db_with_null_emails(tmp_path):
    """SQLite DB where 2 users have NULL emails."""
    return _make_db(tmp_path, "nulls.db", [
        "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)",
        "INSERT INTO users VALUES (1, 'alice@example.com')",
        "INSERT INTO users VALUES (2, NULL)",
        "INSERT INTO users VALUES (3, NULL)",
    ])


@pytest.fixture
def db_no_users_table(tmp_path):
    """SQLite DB that exists but has no users table."""
    return _make_db(tmp_path, "notable.db", [
        "CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)",
    ])


@pytest.fixture
def nonexistent_db(tmp_path):
    """A DB path that does not exist on disk."""
    return str(tmp_path / "missing.db")