"""
tests/test_log_analyzer.py — Tests for Monitors/log_analyzer.py
─────────────────────────────────────────────────────────────────
Tests cover the CURRENT analyze_logs(df: pd.DataFrame | None) -> dict API:

Return keys: total_errors, total_warnings, exceptions, formats,
             top_sources, has_stacktrace

Fixtures build DataFrames directly (matching what log_source_manager produces)
instead of passing raw file paths, which the function no longer accepts.
"""

import pandas as pd

from Monitors.log_analyzer import analyze_logs

# ── DataFrame builder helpers ─────────────────────────────────────────────────


def _make_df(rows: list) -> pd.DataFrame:
    """Build a minimal normalised log DataFrame from a list of row dicts."""
    defaults = {
        "level": "INFO",
        "message": "",
        "source": "",
        "format": "plain",
        "category": "",
        "is_error": False,
        "is_warning": False,
        "extra": {},
    }
    records = [{**defaults, **row} for row in rows]
    return pd.DataFrame(records)


def _error_row(message="Something failed", source="app", category="Application"):
    return {
        "level": "ERROR",
        "message": message,
        "source": source,
        "format": "plain",
        "category": category,
        "is_error": True,
        "is_warning": False,
    }


def _critical_row(message="Critical failure", source="db", category="Database"):
    return {
        "level": "CRITICAL",
        "message": message,
        "source": source,
        "format": "plain",
        "category": category,
        "is_error": True,
        "is_warning": False,
    }


def _warn_row(message="High memory", source="app"):
    return {
        "level": "WARNING",
        "message": message,
        "source": source,
        "format": "plain",
        "category": "",
        "is_error": False,
        "is_warning": True,
    }


def _info_row(message="Server started"):
    return {
        "level": "INFO",
        "message": message,
        "source": "app",
        "format": "plain",
        "category": "",
        "is_error": False,
        "is_warning": False,
    }


# ── Return structure ──────────────────────────────────────────────────────────


class TestLogAnalyzerStructure:
    def test_returns_dict(self):
        result = analyze_logs(None)
        assert isinstance(result, dict)

    def test_has_total_errors_key(self):
        result = analyze_logs(None)
        assert "total_errors" in result

    def test_has_total_warnings_key(self):
        result = analyze_logs(None)
        assert "total_warnings" in result

    def test_has_exceptions_key(self):
        result = analyze_logs(None)
        assert "exceptions" in result

    def test_has_formats_key(self):
        result = analyze_logs(None)
        assert "formats" in result

    def test_has_top_sources_key(self):
        result = analyze_logs(None)
        assert "top_sources" in result

    def test_has_stacktrace_key(self):
        result = analyze_logs(None)
        assert "has_stacktrace" in result

    def test_total_errors_is_int(self):
        result = analyze_logs(None)
        assert isinstance(result["total_errors"], int)

    def test_exceptions_is_list(self):
        result = analyze_logs(None)
        assert isinstance(result["exceptions"], list)

    def test_has_stacktrace_is_bool(self):
        result = analyze_logs(None)
        assert isinstance(result["has_stacktrace"], bool)


# ── None / empty DataFrame ────────────────────────────────────────────────────


class TestLogAnalyzerEmptyInputs:
    def test_none_returns_zero_errors(self):
        assert analyze_logs(None)["total_errors"] == 0

    def test_none_returns_zero_warnings(self):
        assert analyze_logs(None)["total_warnings"] == 0

    def test_none_returns_empty_exceptions(self):
        assert analyze_logs(None)["exceptions"] == []

    def test_none_no_stacktrace(self):
        assert analyze_logs(None)["has_stacktrace"] is False

    def test_empty_df_returns_zero_errors(self):
        df = pd.DataFrame(columns=["level", "message", "source", "format", "is_error", "is_warning"])
        assert analyze_logs(df)["total_errors"] == 0

    def test_empty_df_returns_zero_warnings(self):
        df = pd.DataFrame(columns=["level", "message", "source", "format", "is_error", "is_warning"])
        assert analyze_logs(df)["total_warnings"] == 0

    def test_info_only_df_zero_errors(self):
        df = _make_df([_info_row(), _info_row("Health check OK")])
        assert analyze_logs(df)["total_errors"] == 0

    def test_info_only_df_zero_warnings(self):
        df = _make_df([_info_row(), _info_row("Health check OK")])
        assert analyze_logs(df)["total_warnings"] == 0


# ── Error counting ────────────────────────────────────────────────────────────


class TestLogAnalyzerErrorCounting:
    def test_single_error_counted(self):
        df = _make_df([_error_row()])
        assert analyze_logs(df)["total_errors"] == 1

    def test_three_errors_counted(self):
        df = _make_df([_error_row(), _error_row(), _error_row()])
        assert analyze_logs(df)["total_errors"] == 3

    def test_critical_counts_as_error(self):
        df = _make_df([_critical_row()])
        assert analyze_logs(df)["total_errors"] == 1

    def test_mixed_errors_and_critical(self):
        df = _make_df([_error_row(), _error_row(), _critical_row()])
        assert analyze_logs(df)["total_errors"] == 3

    def test_warn_not_counted_as_error(self):
        df = _make_df([_warn_row(), _warn_row()])
        assert analyze_logs(df)["total_errors"] == 0

    def test_info_not_counted_as_error(self):
        df = _make_df([_info_row()])
        assert analyze_logs(df)["total_errors"] == 0


# ── Warning counting ──────────────────────────────────────────────────────────


class TestLogAnalyzerWarningCounting:
    def test_single_warning_counted(self):
        df = _make_df([_warn_row()])
        assert analyze_logs(df)["total_warnings"] == 1

    def test_two_warnings_counted(self):
        df = _make_df([_warn_row(), _warn_row("Disk at 90%")])
        assert analyze_logs(df)["total_warnings"] == 2

    def test_error_not_counted_as_warning(self):
        df = _make_df([_error_row()])
        assert analyze_logs(df)["total_warnings"] == 0


# ── Exceptions list ───────────────────────────────────────────────────────────


class TestLogAnalyzerExceptions:
    def test_error_message_in_exceptions(self):
        df = _make_df([_error_row("NullPointerException in Service.java")])
        result = analyze_logs(df)
        assert any("NullPointerException" in e for e in result["exceptions"])

    def test_critical_message_in_exceptions(self):
        df = _make_df([_critical_row("Complete DB outage")])
        result = analyze_logs(df)
        assert any("Complete DB outage" in e for e in result["exceptions"])

    def test_info_not_in_exceptions(self):
        df = _make_df([_info_row("Server started")])
        result = analyze_logs(df)
        assert not any("Server started" in e for e in result["exceptions"])

    def test_exceptions_capped_at_100(self):
        rows = [_error_row(f"Error number {i}") for i in range(150)]
        df = _make_df(rows)
        result = analyze_logs(df)
        assert len(result["exceptions"]) <= 100


# ── Formats ───────────────────────────────────────────────────────────────────


class TestLogAnalyzerFormats:
    def test_format_detected(self):
        df = _make_df([{**_error_row(), "format": "json"}])
        result = analyze_logs(df)
        assert "json" in result["formats"]

    def test_multiple_formats(self):
        rows = [
            {**_error_row(), "format": "json"},
            {**_error_row(), "format": "plain"},
        ]
        df = _make_df(rows)
        result = analyze_logs(df)
        assert "json" in result["formats"]
        assert "plain" in result["formats"]


# ── Top sources ───────────────────────────────────────────────────────────────


class TestLogAnalyzerTopSources:
    def test_source_appears_in_top_sources(self):
        df = _make_df([_error_row(source="api-gateway")])
        result = analyze_logs(df)
        assert "api-gateway" in result["top_sources"]

    def test_top_sources_limited_to_5(self):
        rows = [_error_row(source=f"service-{i}") for i in range(10)]
        df = _make_df(rows)
        result = analyze_logs(df)
        assert len(result["top_sources"]) <= 5


# ── Stacktrace detection ──────────────────────────────────────────────────────


class TestLogAnalyzerStacktrace:
    def test_no_stacktrace_by_default(self):
        df = _make_df([_error_row()])
        assert analyze_logs(df)["has_stacktrace"] is False

    def test_stacktrace_detected_when_flagged(self):
        row = {**_error_row(), "extra": {"has_stacktrace": True}}
        df = _make_df([row])
        assert analyze_logs(df)["has_stacktrace"] is True

    def test_stacktrace_false_when_not_set(self):
        row = {**_error_row(), "extra": {"has_stacktrace": False}}
        df = _make_df([row])
        assert analyze_logs(df)["has_stacktrace"] is False
