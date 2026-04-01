"""
tests/conftest.py — Shared fixtures for AutoRCA test suite
"""

import os
import sqlite3
import sys
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

# ── Fix: add project root to sys.path so 'Core', 'Monitors' are importable ───
# This is required for CI (GitHub Actions) where the runner's working directory
# may not automatically include the repo root in Python's module search path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ══════════════════════════════════════════════════════════════════════════════
# AUTH FIXTURES — shared by test_auth.py
# ══════════════════════════════════════════════════════════════════════════════


def _make_sb_mock():
    """
    Build a fully chainable Supabase mock.

    The SELECT chain (select → eq → single → order → range → ilike → execute)
    all share the same `chain` object so callers can stub
    `chain.execute.return_value` for query results.

    INSERT / UPDATE / DELETE are intentionally NOT chained back to `chain`
    so that per-test stubs on, e.g., `sb.table().insert().execute()` do not
    accidentally overwrite the SELECT execute return value.
    """
    sb = MagicMock()
    chain = MagicMock()
    chain.execute.return_value = MagicMock(data=[])
    sb.table.return_value = chain
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.single.return_value = chain
    chain.order.return_value = chain
    chain.range.return_value = chain
    chain.ilike.return_value = chain
    # insert / update / delete get independent auto-mock chains
    return sb


@pytest.fixture()
def sb():
    """Fresh chainable Supabase mock for each test."""
    return _make_sb_mock()


@pytest_asyncio.fixture()
async def client(sb):
    """
    AsyncClient wired to the FastAPI app with Supabase patched out.
    AUTORCA_API_KEY is patched to "" so all requests are in dev mode.
    Yields (ac, sb) — the same sb instance used by the fixture.
    """
    import api_server
    import auth as auth_module

    with (
        patch.object(auth_module, "_get_sb", return_value=sb),
        patch.object(api_server, "_sb", sb),
        patch.object(api_server, "AUTORCA_API_KEY", ""),
    ):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=api_server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac, sb


# ── Auth value fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def valid_raw_key():
    """A freshly generated valid autorca_live_* key for use in tests."""
    from auth import _generate_raw_key

    return _generate_raw_key()


@pytest.fixture
def valid_register_payload():
    """A complete valid registration request body."""
    return {
        "org_name": "Acme DevOps",
        "email": "admin@acme.com",
        "plan": "free",
        "account_type": "biz",
    }


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
        "2026-03-05 10:00:01 INFO  Application started\n2026-03-05 10:01:00 INFO  Health check passed\n2026-03-05 10:02:00 WARN  Memory usage at 75%\n"
    )
    return str(f)


@pytest.fixture
def nonexistent_log_file(tmp_path):
    return str(tmp_path / "does_not_exist.log")


# ── Database fixtures — use context managers to prevent ResourceWarning ───────


def _make_db(tmp_path, filename, setup_sql):
    """Helper: create a SQLite DB, run setup SQL, ensure connection is closed."""
    db_path = str(tmp_path / filename)
    with sqlite3.connect(db_path) as conn:  # 'with' ensures conn.close() is called
        for statement in setup_sql:
            conn.execute(statement)
        conn.commit()
    return db_path


@pytest.fixture
def clean_db(tmp_path):
    """SQLite DB with a users table — all emails present."""
    return _make_db(
        tmp_path,
        "clean.db",
        [
            "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL)",
            "INSERT INTO users VALUES (1, 'alice@example.com')",
            "INSERT INTO users VALUES (2, 'bob@example.com')",
            "INSERT INTO users VALUES (3, 'carol@example.com')",
        ],
    )


@pytest.fixture
def db_with_null_emails(tmp_path):
    """SQLite DB where 2 users have NULL emails."""
    return _make_db(
        tmp_path,
        "nulls.db",
        [
            "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)",
            "INSERT INTO users VALUES (1, 'alice@example.com')",
            "INSERT INTO users VALUES (2, NULL)",
            "INSERT INTO users VALUES (3, NULL)",
        ],
    )


@pytest.fixture
def db_no_users_table(tmp_path):
    """SQLite DB that exists but has no users table."""
    return _make_db(
        tmp_path,
        "notable.db",
        [
            "CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)",
        ],
    )


@pytest.fixture
def nonexistent_db(tmp_path):
    """A DB path that does not exist on disk."""
    return str(tmp_path / "missing.db")
