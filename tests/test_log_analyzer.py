"""
tests/test_log_analyzer.py — Tests for Monitors/log_analyzer.py
─────────────────────────────────────────────────────────────────
Tests cover:
  ✅ Returns correct structure (total_errors, categories, critical_issues)
  ✅ All 6 categories detected correctly
  ✅ CRITICAL lines tracked in critical_issues
  ✅ Empty log file → zero counts
  ✅ Log file with no errors → zero counts
  ✅ Missing log file → safe fallback (no crash)
  ✅ Mixed content → only ERROR/CRITICAL counted
  ✅ DB_CONN_FAIL and DEADLOCK → categorised as Database
  ✅ total_errors matches sum of all categories
"""

import pytest

from Monitors.log_analyzer import analyze_logs

# ── Return structure ──────────────────────────────────────────────────────────


class TestLogAnalyzerStructure:
    def test_returns_dict(self, empty_log_file):
        result = analyze_logs(empty_log_file)
        assert isinstance(result, dict)

    def test_has_total_errors_key(self, empty_log_file):
        result = analyze_logs(empty_log_file)
        assert "total_errors" in result

    def test_has_categories_key(self, empty_log_file):
        result = analyze_logs(empty_log_file)
        assert "categories" in result

    def test_has_critical_issues_key(self, empty_log_file):
        result = analyze_logs(empty_log_file)
        assert "critical_issues" in result

    def test_categories_has_all_6_keys(self, empty_log_file):
        result = analyze_logs(empty_log_file)
        expected = {
            "Database",
            "Network",
            "API/Gateway",
            "Security/Firewall",
            "ActiveDirectory",
            "Application",
        }
        assert set(result["categories"].keys()) == expected

    def test_critical_issues_is_list(self, empty_log_file):
        result = analyze_logs(empty_log_file)
        assert isinstance(result["critical_issues"], list)

    def test_total_errors_is_int(self, empty_log_file):
        result = analyze_logs(empty_log_file)
        assert isinstance(result["total_errors"], int)


# ── Empty / clean files ───────────────────────────────────────────────────────


class TestLogAnalyzerEmptyFiles:
    def test_empty_file_total_errors_is_zero(self, empty_log_file):
        result = analyze_logs(empty_log_file)
        assert result["total_errors"] == 0

    def test_empty_file_all_categories_zero(self, empty_log_file):
        result = analyze_logs(empty_log_file)
        for count in result["categories"].values():
            assert count == 0

    def test_empty_file_no_critical_issues(self, empty_log_file):
        result = analyze_logs(empty_log_file)
        assert result["critical_issues"] == []

    def test_no_errors_in_file(self, log_file_no_errors):
        result = analyze_logs(log_file_no_errors)
        assert result["total_errors"] == 0

    def test_no_errors_all_categories_zero(self, log_file_no_errors):
        result = analyze_logs(log_file_no_errors)
        for count in result["categories"].values():
            assert count == 0


# ── Missing file ──────────────────────────────────────────────────────────────


class TestLogAnalyzerMissingFile:
    def test_missing_file_does_not_crash(self, nonexistent_log_file):
        """Should return safe defaults, not raise FileNotFoundError."""
        try:
            result = analyze_logs(nonexistent_log_file)
            assert isinstance(result, dict)
        except Exception:
            pytest.fail("analyze_logs() raised an exception for a missing file")

    def test_missing_file_total_errors_zero(self, nonexistent_log_file):
        result = analyze_logs(nonexistent_log_file)
        assert result["total_errors"] == 0

    def test_missing_file_categories_all_zero(self, nonexistent_log_file):
        result = analyze_logs(nonexistent_log_file)
        for count in result["categories"].values():
            assert count == 0


# ── Category detection ────────────────────────────────────────────────────────


class TestLogAnalyzerCategories:
    def test_database_tag_detected(self, tmp_path):
        f = tmp_path / "test.log"
        f.write_text("2026-03-05 10:00:00 ERROR [Database] Connection lost\n")
        result = analyze_logs(str(f))
        assert result["categories"]["Database"] == 1

    def test_db_conn_fail_detected_as_database(self, tmp_path):
        """DB_CONN_FAIL keyword should count as Database category."""
        f = tmp_path / "test.log"
        f.write_text("2026-03-05 10:00:00 ERROR DB_CONN_FAIL: Connection refused\n")
        result = analyze_logs(str(f))
        assert result["categories"]["Database"] == 1

    def test_deadlock_detected_as_database(self, tmp_path):
        """DEADLOCK keyword should count as Database category."""
        f = tmp_path / "test.log"
        f.write_text("2026-03-05 10:00:00 ERROR DEADLOCK detected on table users\n")
        result = analyze_logs(str(f))
        assert result["categories"]["Database"] == 1

    def test_network_tag_detected(self, tmp_path):
        f = tmp_path / "test.log"
        f.write_text("2026-03-05 10:00:00 ERROR [Network] Connection timed out\n")
        result = analyze_logs(str(f))
        assert result["categories"]["Network"] == 1

    def test_api_tag_detected(self, tmp_path):
        f = tmp_path / "test.log"
        f.write_text("2026-03-05 10:00:00 ERROR [API] Gateway returned 502\n")
        result = analyze_logs(str(f))
        assert result["categories"]["API/Gateway"] == 1

    def test_gateway_tag_detected(self, tmp_path):
        f = tmp_path / "test.log"
        f.write_text("2026-03-05 10:00:00 ERROR [Gateway] Upstream timeout\n")
        result = analyze_logs(str(f))
        assert result["categories"]["API/Gateway"] == 1

    def test_firewall_tag_detected(self, tmp_path):
        f = tmp_path / "test.log"
        f.write_text("2026-03-05 10:00:00 ERROR [Firewall] Rule violation — blocked IP\n")
        result = analyze_logs(str(f))
        assert result["categories"]["Security/Firewall"] == 1

    def test_access_tag_detected_as_security(self, tmp_path):
        f = tmp_path / "test.log"
        f.write_text("2026-03-05 10:00:00 ERROR [Access] Unauthorized access attempt\n")
        result = analyze_logs(str(f))
        assert result["categories"]["Security/Firewall"] == 1

    def test_active_directory_tag_detected(self, tmp_path):
        f = tmp_path / "test.log"
        f.write_text("2026-03-05 10:00:00 ERROR [ActiveDirectory] LDAP bind failed\n")
        result = analyze_logs(str(f))
        assert result["categories"]["ActiveDirectory"] == 1

    def test_untagged_error_goes_to_application(self, tmp_path):
        """ERROR lines with no known tag should fall into Application category."""
        f = tmp_path / "test.log"
        f.write_text("2026-03-05 10:00:00 ERROR NullPointerException in Service.java:42\n")
        result = analyze_logs(str(f))
        assert result["categories"]["Application"] == 1

    def test_multiple_categories_in_one_file(self, log_file_with_errors):
        result = analyze_logs(log_file_with_errors)
        # From conftest: Database=2, Network=1, API=1, Security=1, AD=1, Application=1
        assert result["categories"]["Database"] >= 1
        assert result["categories"]["Network"] >= 1
        assert result["categories"]["API/Gateway"] >= 1
        assert result["categories"]["Security/Firewall"] >= 1
        assert result["categories"]["ActiveDirectory"] >= 1
        assert result["categories"]["Application"] >= 1

    def test_warn_lines_not_counted_as_errors(self, tmp_path):
        """WARN lines should NOT be counted in total_errors or any category."""
        f = tmp_path / "test.log"
        f.write_text("2026-03-05 10:00:00 WARN  High memory usage\n2026-03-05 10:00:01 WARN  Slow query detected\n")
        result = analyze_logs(str(f))
        assert result["total_errors"] == 0

    def test_info_lines_not_counted(self, tmp_path):
        """INFO lines must never be counted."""
        f = tmp_path / "test.log"
        f.write_text("2026-03-05 10:00:00 INFO  Application started successfully\n")
        result = analyze_logs(str(f))
        assert result["total_errors"] == 0


# ── Total errors count ────────────────────────────────────────────────────────


class TestLogAnalyzerTotalErrors:
    def test_total_errors_matches_error_lines(self, tmp_path):
        f = tmp_path / "test.log"
        f.write_text("2026-03-05 ERROR line one\n2026-03-05 ERROR line two\n2026-03-05 ERROR line three\n2026-03-05 INFO  not an error\n")
        result = analyze_logs(str(f))
        assert result["total_errors"] == 3

    def test_total_errors_includes_critical(self, tmp_path):
        """CRITICAL lines should count toward total_errors."""
        f = tmp_path / "test.log"
        f.write_text("2026-03-05 ERROR  something failed\n2026-03-05 CRITICAL complete outage\n")
        result = analyze_logs(str(f))
        assert result["total_errors"] == 2

    def test_total_matches_sum_of_categories(self, log_file_with_errors):
        """total_errors must equal sum of all category counts."""
        result = analyze_logs(log_file_with_errors)
        category_sum = sum(result["categories"].values())
        assert result["total_errors"] == category_sum


# ── Critical issues tracking ──────────────────────────────────────────────────


class TestLogAnalyzerCritical:
    def test_critical_lines_captured(self, log_file_with_critical):
        result = analyze_logs(log_file_with_critical)
        assert len(result["critical_issues"]) == 2  # 2 CRITICAL lines in fixture

    def test_critical_issue_content(self, log_file_with_critical):
        result = analyze_logs(log_file_with_critical)
        assert any("CRITICAL" in issue for issue in result["critical_issues"])

    def test_no_critical_in_normal_errors(self, log_file_with_errors):
        """Normal ERROR-only file should have empty critical_issues."""
        result = analyze_logs(log_file_with_errors)
        # conftest fixture has no CRITICAL lines
        assert result["critical_issues"] == []

    def test_critical_line_also_counted_in_total(self, tmp_path):
        f = tmp_path / "test.log"
        f.write_text("2026-03-05 CRITICAL [Database] Complete DB outage\n")
        result = analyze_logs(str(f))
        assert result["total_errors"] == 1
        assert len(result["critical_issues"]) == 1
