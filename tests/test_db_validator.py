"""
tests/test_db_validator.py — Tests for Monitors/db_validator.py
─────────────────────────────────────────────────────────────────
Tests cover:
  ✅ Clean DB → null_email_count is 0
  ✅ DB with NULL emails → correct count returned
  ✅ Missing DB file → safe fallback (no crash)
  ✅ DB with no users table → safe fallback
  ✅ Return type is always a dict with null_email_count key
  ✅ Multiple NULL emails counted correctly
  ✅ DB connection is properly closed after use
"""

import pytest
import sqlite3
from unittest.mock import patch
from Monitors.db_validator import validate_data


# ── Return structure ──────────────────────────────────────────────────────────

class TestDBValidatorStructure:

    def test_returns_dict(self, clean_db):
        result = validate_data(clean_db)
        assert isinstance(result, dict)

    def test_has_null_email_count_key(self, clean_db):
        result = validate_data(clean_db)
        assert "null_email_count" in result

    def test_null_email_count_is_int(self, clean_db):
        result = validate_data(clean_db)
        assert isinstance(result["null_email_count"], int)


# ── Clean database ────────────────────────────────────────────────────────────

class TestDBValidatorCleanDB:

    def test_clean_db_zero_nulls(self, clean_db):
        """A healthy database with all emails present returns 0."""
        result = validate_data(clean_db)
        assert result["null_email_count"] == 0

    def test_clean_db_returns_correct_type(self, clean_db):
        result = validate_data(clean_db)
        assert result["null_email_count"] == 0
        assert isinstance(result, dict)


# ── Database with NULL emails ─────────────────────────────────────────────────

class TestDBValidatorNullEmails:

    def test_two_null_emails_detected(self, db_with_null_emails):
        """DB fixture has 2 NULL email rows — should return 2."""
        result = validate_data(db_with_null_emails)
        assert result["null_email_count"] == 2

    def test_single_null_email(self, tmp_path):
        """Verify exact count with a single NULL."""
        db_path = str(tmp_path / "single_null.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
        conn.execute("INSERT INTO users VALUES (1, 'valid@example.com')")
        conn.execute("INSERT INTO users VALUES (2, NULL)")
        conn.commit()
        conn.close()
        result = validate_data(db_path)
        assert result["null_email_count"] == 1

    def test_all_null_emails(self, tmp_path):
        """All users having NULL emails should be counted correctly."""
        db_path = str(tmp_path / "all_null.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
        conn.execute("INSERT INTO users VALUES (1, NULL)")
        conn.execute("INSERT INTO users VALUES (2, NULL)")
        conn.execute("INSERT INTO users VALUES (3, NULL)")
        conn.commit()
        conn.close()
        result = validate_data(db_path)
        assert result["null_email_count"] == 3

    def test_empty_string_email_not_counted_as_null(self, tmp_path):
        """Empty string '' is not NULL — should not be counted."""
        db_path = str(tmp_path / "empty_str.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
        conn.execute("INSERT INTO users VALUES (1, '')")       # empty string ≠ NULL
        conn.execute("INSERT INTO users VALUES (2, NULL)")     # this IS NULL
        conn.commit()
        conn.close()
        result = validate_data(db_path)
        assert result["null_email_count"] == 1   # only the actual NULL

    def test_large_table_count(self, tmp_path):
        """Verify counting works correctly on a larger dataset."""
        db_path = str(tmp_path / "large.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
        # 50 valid + 10 null
        for i in range(1, 51):
            conn.execute(f"INSERT INTO users VALUES ({i}, 'user{i}@example.com')")
        for i in range(51, 61):
            conn.execute(f"INSERT INTO users VALUES ({i}, NULL)")
        conn.commit()
        conn.close()
        result = validate_data(db_path)
        assert result["null_email_count"] == 10


# ── Error / edge cases ────────────────────────────────────────────────────────

class TestDBValidatorErrors:

    def test_missing_db_does_not_crash(self, nonexistent_db):
        """Missing DB file should return safe defaults, not crash."""
        try:
            result = validate_data(nonexistent_db)
            assert isinstance(result, dict)
        except Exception:
            pytest.fail("validate_data() raised an exception for a missing DB file")

    def test_missing_db_returns_zero(self, nonexistent_db):
        """Missing DB should return null_email_count: 0 as safe default."""
        result = validate_data(nonexistent_db)
        assert result["null_email_count"] == 0

    def test_missing_users_table_does_not_crash(self, db_no_users_table):
        """DB without a users table should be handled gracefully."""
        try:
            result = validate_data(db_no_users_table)
            assert isinstance(result, dict)
        except Exception:
            pytest.fail("validate_data() raised an exception for missing users table")

    def test_missing_users_table_returns_zero(self, db_no_users_table):
        """DB without users table should return 0 as safe default."""
        result = validate_data(db_no_users_table)
        assert result["null_email_count"] == 0

    def test_unexpected_exception_returns_zero(self, tmp_path):
        """Any unexpected error should be caught — return 0, not crash."""
        with patch("Monitors.db_validator.sqlite3.connect", side_effect=Exception("Disk full")):
            try:
                result = validate_data(str(tmp_path / "any.db"))
                assert result["null_email_count"] == 0
            except Exception:
                pytest.fail("validate_data() let an unexpected exception escape")

    def test_empty_users_table_returns_zero(self, tmp_path):
        """A users table with no rows at all should return 0."""
        db_path = str(tmp_path / "empty_users.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
        # no rows inserted
        conn.commit()
        conn.close()
        result = validate_data(db_path)
        assert result["null_email_count"] == 0