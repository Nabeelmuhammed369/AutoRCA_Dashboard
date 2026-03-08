"""
tests/test_rca_engine.py — Tests for Core/rca_engine.py
─────────────────────────────────────────────────────────
Tests cover all 5 classification paths in order of priority:
  ✅ "Infrastructure Issue"       — api_result has "error" key
  ✅ "Code Issue"                 — status_code >= 500
  ✅ "Data Integrity Issue"       — null_email_count > 0
  ✅ "Database Connectivity Issue"— db_errors > 5
  ✅ "System Healthy"             — everything nominal
  ✅ Priority order is respected (infra beats code beats data etc.)
  ✅ Return type is always a string
  ✅ Boundary values (exactly 500, exactly 5 db_errors, etc.)
"""

from Core.rca_engine import classify_issue

# ── Shared test data builders ─────────────────────────────────────────────────


def api_ok(status_code=200, response_time=0.142):
    return {"status_code": status_code, "response_time": response_time}


def api_error(message="Connection Error"):
    return {"error": message}


def logs_clean():
    return {"total_errors": 0, "db_errors": 0, "categories": {}, "critical_issues": []}


def logs_with_db_errors(count):
    return {"total_errors": count, "db_errors": count, "categories": {}, "critical_issues": []}


def logs_with_errors(count):
    return {"total_errors": count, "db_errors": 0, "categories": {}, "critical_issues": []}


def db_clean():
    return {"null_email_count": 0}


def db_with_nulls(count=1):
    return {"null_email_count": count}


# ── Return type ───────────────────────────────────────────────────────────────


class TestRCAEngineReturnType:
    def test_always_returns_string(self):
        result = classify_issue(api_ok(), logs_clean(), db_clean())
        assert isinstance(result, str)

    def test_returns_non_empty_string(self):
        result = classify_issue(api_ok(), logs_clean(), db_clean())
        assert len(result) > 0


# ── Path 1: Infrastructure Issue ─────────────────────────────────────────────


class TestInfrastructureIssue:
    def test_api_error_returns_infrastructure_issue(self):
        result = classify_issue(api_error(), logs_clean(), db_clean())
        assert result == "Infrastructure Issue"

    def test_timeout_error_returns_infrastructure_issue(self):
        result = classify_issue(api_error("Timeout"), logs_clean(), db_clean())
        assert result == "Infrastructure Issue"

    def test_connection_error_returns_infrastructure_issue(self):
        result = classify_issue(api_error("Connection Error"), logs_clean(), db_clean())
        assert result == "Infrastructure Issue"

    def test_infrastructure_beats_null_emails(self):
        """Infra issue has higher priority than data integrity issue."""
        result = classify_issue(api_error(), logs_clean(), db_with_nulls(5))
        assert result == "Infrastructure Issue"

    def test_infrastructure_beats_db_errors(self):
        """Infra issue has higher priority than DB connectivity issue."""
        result = classify_issue(api_error(), logs_with_db_errors(10), db_clean())
        assert result == "Infrastructure Issue"

    def test_infrastructure_beats_all_other_conditions(self):
        """When API has error, it overrides all other conditions."""
        result = classify_issue(api_error(), logs_with_db_errors(10), db_with_nulls(5))
        assert result == "Infrastructure Issue"


# ── Path 2: Code Issue ────────────────────────────────────────────────────────


class TestCodeIssue:
    def test_status_500_returns_code_issue(self):
        result = classify_issue(api_ok(500), logs_clean(), db_clean())
        assert result == "Code Issue"

    def test_status_503_returns_code_issue(self):
        result = classify_issue(api_ok(503), logs_clean(), db_clean())
        assert result == "Code Issue"

    def test_status_502_returns_code_issue(self):
        result = classify_issue(api_ok(502), logs_clean(), db_clean())
        assert result == "Code Issue"

    def test_status_exactly_500_is_code_issue(self):
        """Boundary: exactly 500 should trigger Code Issue."""
        result = classify_issue(api_ok(500), logs_clean(), db_clean())
        assert result == "Code Issue"

    def test_status_499_is_not_code_issue(self):
        """Boundary: 499 is below threshold — should not be Code Issue."""
        result = classify_issue(api_ok(499), logs_clean(), db_clean())
        assert result != "Code Issue"

    def test_status_200_is_not_code_issue(self):
        result = classify_issue(api_ok(200), logs_clean(), db_clean())
        assert result != "Code Issue"

    def test_code_issue_beats_data_integrity(self):
        """500 status + null emails → Code Issue wins (higher priority)."""
        result = classify_issue(api_ok(500), logs_clean(), db_with_nulls(3))
        assert result == "Code Issue"

    def test_code_issue_beats_db_connectivity(self):
        """500 status + db errors → Code Issue wins."""
        result = classify_issue(api_ok(500), logs_with_db_errors(10), db_clean())
        assert result == "Code Issue"


# ── Path 3: Data Integrity Issue ─────────────────────────────────────────────


class TestDataIntegrityIssue:
    def test_one_null_email_returns_data_integrity(self):
        result = classify_issue(api_ok(), logs_clean(), db_with_nulls(1))
        assert result == "Data Integrity Issue"

    def test_many_null_emails_returns_data_integrity(self):
        result = classify_issue(api_ok(), logs_clean(), db_with_nulls(50))
        assert result == "Data Integrity Issue"

    def test_zero_null_emails_not_data_integrity(self):
        result = classify_issue(api_ok(), logs_clean(), db_clean())
        assert result != "Data Integrity Issue"

    def test_data_integrity_beats_db_connectivity(self):
        """null_email_count > 0 has higher priority than db_errors > 5."""
        result = classify_issue(api_ok(), logs_with_db_errors(10), db_with_nulls(1))
        assert result == "Data Integrity Issue"

    def test_exactly_one_null_is_sufficient(self):
        """Boundary: even a single NULL email triggers the alert."""
        result = classify_issue(api_ok(), logs_clean(), {"null_email_count": 1})
        assert result == "Data Integrity Issue"


# ── Path 4: Database Connectivity Issue ──────────────────────────────────────


class TestDatabaseConnectivityIssue:
    def test_six_db_errors_returns_db_connectivity(self):
        """db_errors > 5 means exactly 6 should trigger."""
        result = classify_issue(api_ok(), logs_with_db_errors(6), db_clean())
        assert result == "Database Connectivity Issue"

    def test_many_db_errors_returns_db_connectivity(self):
        result = classify_issue(api_ok(), logs_with_db_errors(20), db_clean())
        assert result == "Database Connectivity Issue"

    def test_exactly_five_db_errors_is_not_triggered(self):
        """Boundary: exactly 5 is NOT > 5, should not trigger."""
        result = classify_issue(api_ok(), logs_with_db_errors(5), db_clean())
        assert result != "Database Connectivity Issue"

    def test_four_db_errors_not_triggered(self):
        result = classify_issue(api_ok(), logs_with_db_errors(4), db_clean())
        assert result != "Database Connectivity Issue"

    def test_zero_db_errors_not_triggered(self):
        result = classify_issue(api_ok(), logs_clean(), db_clean())
        assert result != "Database Connectivity Issue"


# ── Path 5: System Healthy ────────────────────────────────────────────────────


class TestSystemHealthy:
    def test_all_nominal_returns_healthy(self):
        result = classify_issue(api_ok(), logs_clean(), db_clean())
        assert result == "System Healthy"

    def test_200_no_errors_no_nulls_is_healthy(self):
        result = classify_issue(
            api_ok(200, 0.099),
            {"total_errors": 0, "db_errors": 0, "categories": {}, "critical_issues": []},
            {"null_email_count": 0},
        )
        assert result == "System Healthy"

    def test_low_errors_below_threshold_is_healthy(self):
        """5 db_errors is NOT above threshold — should still be healthy."""
        result = classify_issue(api_ok(), logs_with_db_errors(5), db_clean())
        assert result == "System Healthy"

    def test_high_latency_alone_does_not_affect_classification(self):
        """Latency is not a classification input — high latency = still healthy."""
        result = classify_issue(api_ok(200, 9.99), logs_clean(), db_clean())
        assert result == "System Healthy"


# ── Priority order enforcement ────────────────────────────────────────────────


class TestClassificationPriority:
    def test_priority_order_infra_first(self):
        """When ALL conditions are bad, Infrastructure Issue must win."""
        result = classify_issue(api_error("Timeout"), logs_with_db_errors(10), db_with_nulls(5))
        assert result == "Infrastructure Issue"

    def test_priority_order_code_second(self):
        """No infra error, but 500 status + data problems → Code Issue."""
        result = classify_issue(api_ok(500), logs_with_db_errors(10), db_with_nulls(5))
        assert result == "Code Issue"

    def test_priority_order_data_integrity_third(self):
        """API ok + null emails + high db_errors → Data Integrity Issue."""
        result = classify_issue(api_ok(200), logs_with_db_errors(10), db_with_nulls(1))
        assert result == "Data Integrity Issue"

    def test_priority_order_db_connectivity_fourth(self):
        """API ok + no nulls + high db_errors → DB Connectivity Issue."""
        result = classify_issue(api_ok(200), logs_with_db_errors(6), db_clean())
        assert result == "Database Connectivity Issue"

    def test_complete_priority_chain(self):
        """Verify all 5 outcomes are reachable in order."""
        scenarios = [
            (api_error(), logs_with_db_errors(10), db_with_nulls(5), "Infrastructure Issue"),
            (api_ok(500), logs_with_db_errors(10), db_with_nulls(5), "Code Issue"),
            (api_ok(200), logs_with_db_errors(10), db_with_nulls(1), "Data Integrity Issue"),
            (api_ok(200), logs_with_db_errors(6), db_clean(), "Database Connectivity Issue"),
            (api_ok(200), logs_clean(), db_clean(), "System Healthy"),
        ]
        for api, logs, db, expected in scenarios:
            result = classify_issue(api, logs, db)
            assert result == expected, f"Expected '{expected}' but got '{result}'"
