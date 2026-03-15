"""
test_rca_logic.py
=================
Python equivalent of the former test_rca_logic.js (Vitest).
Covers RCA duplicate-check logic, severity classification,
log parsing heuristics, and history deduplication.

Run with:
    pytest tests/test_rca_logic.py -v
"""


# ---------------------------------------------------------------------------
# Helpers — pure Python reimplementations of the JS logic
# ---------------------------------------------------------------------------


def classify_severity(error_rate: float, crit_count: int) -> str:
    """Mirror of the JS classify() / _rcaClass logic."""
    if crit_count > 0 or error_rate > 50:
        return "critical"
    if error_rate > 10:
        return "error"
    if error_rate > 0:
        return "warning"
    return "healthy"


def build_fingerprint(source: str, total: int, err: int, rate: float) -> str:
    """Mirror of the JS saveRCAToHistory fingerprint builder."""
    return f"{source}|{total}|{err}|{rate:.2f}"


def is_duplicate(fingerprint: str, existing_records: list[dict]) -> bool:
    """
    Mirror of the JS duplicate-check logic in saveRCAToHistory().
    Checks whether a record with identical source_name + total_entries
    + error_count already exists in the loaded history.
    """
    parts = fingerprint.split("|")
    if len(parts) < 3:
        return False
    source = parts[0]
    try:
        total = int(parts[1])
        err = int(parts[2])
    except ValueError:
        return False

    return any(
        r.get("source_name") == source
        and int(r.get("total_entries", 0)) == total
        and int(r.get("error_count", 0)) == err
        for r in existing_records
    )


def parse_log_level(line: str) -> str:
    """
    Simplified mirror of parseLine() level-detection fallback.
    Returns the highest-priority level keyword found in the line.
    """
    upper = line.upper()
    for level in ["CRITICAL", "FATAL", "ERROR", "WARNING", "WARN", "INFO", "DEBUG", "TRACE"]:
        if level in upper:
            return level if level != "WARN" else "WARNING"
    return "UNKNOWN"


def calc_error_rate(total: int, errors: int) -> float:
    """Mirror of calcStats() rate calculation."""
    if total == 0:
        return 0.0
    return round(errors / total * 100, 1)


def normalize_severity(raw: str) -> str:
    """Mirror of the severity normalisation in _doSaveRCA."""
    mapping = {"error": "critical", "critical": "critical", "healthy": "healthy"}
    return mapping.get(raw, "warning")


# ---------------------------------------------------------------------------
# SUITE 1 — Severity Classification
# ---------------------------------------------------------------------------


class TestSeverityClassification:
    def test_critical_when_crit_count_above_zero(self):
        assert classify_severity(0.0, crit_count=1) == "critical"

    def test_critical_when_error_rate_above_50(self):
        assert classify_severity(52.4, crit_count=0) == "critical"

    def test_error_when_rate_between_10_and_50(self):
        assert classify_severity(25.0, crit_count=0) == "error"

    def test_warning_when_rate_above_zero_below_10(self):
        assert classify_severity(4.5, crit_count=0) == "warning"

    def test_healthy_when_no_errors(self):
        assert classify_severity(0.0, crit_count=0) == "healthy"

    def test_critical_takes_priority_over_rate(self):
        assert classify_severity(2.0, crit_count=5) == "critical"

    def test_boundary_exactly_50_percent(self):
        # 50% is NOT above 50, so not critical by rate alone
        assert classify_severity(50.0, crit_count=0) == "error"

    def test_boundary_exactly_10_percent(self):
        # 10% is NOT above 10, so warning not error
        assert classify_severity(10.0, crit_count=0) == "warning"


# ---------------------------------------------------------------------------
# SUITE 2 — Fingerprint & Duplicate Detection
# ---------------------------------------------------------------------------


class TestFingerprintAndDuplication:
    def test_fingerprint_format(self):
        fp = build_fingerprint("app.log", 2500, 1310, 52.4)
        assert fp == "app.log|2500|1310|52.40"

    def test_fingerprint_zero_values(self):
        fp = build_fingerprint("test", 0, 0, 0.0)
        assert fp == "test|0|0|0.00"

    def test_no_duplicate_when_history_empty(self):
        fp = build_fingerprint("app.log", 2500, 1310, 52.4)
        assert is_duplicate(fp, []) is False

    def test_detects_duplicate_exact_match(self):
        fp = build_fingerprint("app.log", 2500, 1310, 52.4)
        existing = [{"source_name": "app.log", "total_entries": 2500, "error_count": 1310}]
        assert is_duplicate(fp, existing) is True

    def test_no_duplicate_different_source(self):
        fp = build_fingerprint("app.log", 2500, 1310, 52.4)
        existing = [{"source_name": "other.log", "total_entries": 2500, "error_count": 1310}]
        assert is_duplicate(fp, existing) is False

    def test_no_duplicate_different_error_count(self):
        fp = build_fingerprint("app.log", 2500, 1310, 52.4)
        existing = [{"source_name": "app.log", "total_entries": 2500, "error_count": 999}]
        assert is_duplicate(fp, existing) is False

    def test_no_duplicate_different_total(self):
        fp = build_fingerprint("app.log", 2500, 1310, 52.4)
        existing = [{"source_name": "app.log", "total_entries": 9999, "error_count": 1310}]
        assert is_duplicate(fp, existing) is False

    def test_duplicate_found_in_multiple_records(self):
        fp = build_fingerprint("prod.log", 100, 10, 10.0)
        existing = [
            {"source_name": "app.log", "total_entries": 2500, "error_count": 1310},
            {"source_name": "prod.log", "total_entries": 100, "error_count": 10},
        ]
        assert is_duplicate(fp, existing) is True

    def test_malformed_fingerprint_returns_false(self):
        assert is_duplicate("", [{"source_name": "", "total_entries": 0, "error_count": 0}]) is False
        assert is_duplicate("only-one-part", []) is False


# ---------------------------------------------------------------------------
# SUITE 3 — Log Level Parsing
# ---------------------------------------------------------------------------


class TestLogLevelParsing:
    def test_detects_error(self):
        assert parse_log_level("2024-01-01 12:00:00 ERROR something failed") == "ERROR"

    def test_detects_critical(self):
        assert parse_log_level("CRITICAL: database connection pool exhausted") == "CRITICAL"

    def test_detects_warning(self):
        assert parse_log_level("[WARNING] high memory usage") == "WARNING"

    def test_warn_normalized_to_warning(self):
        assert parse_log_level("WARN  slow query detected") == "WARNING"

    def test_detects_info(self):
        assert parse_log_level("INFO  server started on port 8000") == "INFO"

    def test_detects_debug(self):
        assert parse_log_level("DEBUG  cache miss for key xyz") == "DEBUG"

    def test_unknown_for_plain_text(self):
        assert parse_log_level("some random text with no level") == "UNKNOWN"

    def test_critical_takes_priority_in_line(self):
        assert parse_log_level("CRITICAL ERROR in database layer") == "CRITICAL"

    def test_case_insensitive(self):
        assert parse_log_level("error: connection refused") == "ERROR"
        assert parse_log_level("Info: starting up") == "INFO"

    def test_fatal_detected(self):
        assert parse_log_level("FATAL out of memory") == "FATAL"


# ---------------------------------------------------------------------------
# SUITE 4 — Error Rate Calculation
# ---------------------------------------------------------------------------


class TestErrorRateCalculation:
    def test_basic_rate(self):
        assert calc_error_rate(2500, 1310) == 52.4

    def test_zero_total_returns_zero(self):
        assert calc_error_rate(0, 0) == 0.0

    def test_zero_errors(self):
        assert calc_error_rate(1000, 0) == 0.0

    def test_all_errors(self):
        assert calc_error_rate(100, 100) == 100.0

    def test_rounding_to_one_decimal(self):
        # 1/3 = 33.333... rounds to 33.3
        assert calc_error_rate(3, 1) == 33.3

    def test_small_rate(self):
        assert calc_error_rate(10000, 5) == 0.1


# ---------------------------------------------------------------------------
# SUITE 5 — Severity Normalisation
# ---------------------------------------------------------------------------


class TestSeverityNormalisation:
    def test_error_normalises_to_critical(self):
        assert normalize_severity("error") == "critical"

    def test_healthy_stays_healthy(self):
        assert normalize_severity("healthy") == "healthy"

    def test_warning_stays_warning(self):
        assert normalize_severity("warning") == "warning"

    def test_unknown_value_falls_back_to_warning(self):
        assert normalize_severity("unknown_status") == "warning"
        assert normalize_severity("") == "warning"


# ---------------------------------------------------------------------------
# SUITE 6 — Edge Cases & Boundary Conditions
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_source_fingerprint(self):
        fp = build_fingerprint("", 0, 0, 0.0)
        assert fp == "|0|0|0.00"

    def test_source_with_pipe_character(self):
        fp = build_fingerprint("app|prod.log", 100, 5, 5.0)
        assert is_duplicate(fp, []) is False

    def test_large_log_volume(self):
        rate = calc_error_rate(10_000_000, 500_000)
        assert rate == 5.0

    def test_classify_exactly_at_critical_boundary(self):
        assert classify_severity(50.1, 0) == "critical"

    def test_all_suites_independent(self):
        total, errors = 2500, 1310
        rate = calc_error_rate(total, errors)
        sev = classify_severity(rate, crit_count=0)
        norm = normalize_severity(sev)
        assert rate == 52.4
        assert sev == "critical"
        assert norm == "critical"
