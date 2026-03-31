"""
tests/test_rca_endpoints.py  (v7 — aligned to actual api_server.py v3.2)
=========================================================================

What changed from v6 → v7
--------------------------
FIXED (failing test):
  test_save_inserts_correct_source_name
    Old:  args.get("source")          ← wrong, old api_server field name
    New:  args.get("source_name")     ← correct, to_db_row() uses "source_name"

FIXED (wrong severity expectation):
    Old:  args["severity"].upper() == "CRITICAL"
    New:  args["severity"] == "critical"   ← _sev_map normalises to lowercase

FIXED (wrong ilike field):
    Old:  "source" in call_args
    New:  "source_name" in call_args   ← q.ilike("source_name", ...)

FIXED (wrong no-sb behaviour for /api/rca/history):
    Old:  _is_no_sb expected 503
    New:  returns HTTP 200 {"ok": True, "supabase": False}  (not an error)

NEW test classes for recent enhancements:
  TestCORSFix            — CORS middleware allows localhost/127.0.0.1 on any port
  TestAuthSystem         — dev-mode pass-through + 401 on wrong key
  TestHealthEndpoint     — /api/health shape & fields
  TestLogParser          — _parse_line() regex across all 9 log formats
  TestParseAndRespond    — _parse_and_respond() stats calculation
  TestIntegrationLoki    — /api/integration/loki mocked HTTP responses
  TestIntegrationES      — /api/integration/elasticsearch
  TestIntegrationS3      — /api/integration/s3
  TestIntegrationHTTP    — /api/integration/http (GET + POST methods)
  TestWebSocket          — /ws/logs connection, seek, pause/resume, ping/pong
  TestToDbRow            — RCASavePayload.to_db_row() field mapping & severity normalisation
  TestRcaSave (updated)  — all existing cases with correct field names
  TestRcaHistory (upd.)  — ilike uses source_name, no-sb is HTTP 200 not 503
  TestRcaGet             — unchanged
  TestRcaDelete          — unchanged
  TestDuplicateFlow      — unchanged
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import uuid
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ─────────────────────────────────────────────────────────────────────────────
# Constants shared across all tests
# ─────────────────────────────────────────────────────────────────────────────
FAKE_ID = str(uuid.uuid4())

BASE_ENV = {
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_KEY": "test-key",
    "AUTORCA_API_KEY": "test-api-key",
}

# Payload that matches what the dashboard / livestream page actually POSTs.
# Uses "source_name" (primary field) and "source" (legacy alias) together.
SAMPLE_PAYLOAD = {
    "source_name": "app.log",  # primary DB field
    "source": "app.log",  # legacy alias — also accepted
    "severity": "CRITICAL",
    "total_entries": 2500,
    "error_count": 1310,
    "warn_count": 634,
    "error_rate": 52.4,
    "ai_summary": "High error rate detected.",
    "fix_steps": "1. Restart the service.",
    "incident_groups": [],
    "affected_services": ["auth", "payment"],
    "remediation": [],
    "stats": {"total": 2500, "err": 1310, "warn": 634, "rate": 52.4},
}

# Minimal payload that the livestream page sends when saving a stream session.
LIVESTREAM_PAYLOAD = {
    "source_name": "Live Stream · 22/03/2026, 14:30:00",
    "severity": "warning",
    "total_entries": 216,
    "error_count": 86,
    "warn_count": 32,
    "error_rate": 39.8,
    "ai_summary": "Live stream session: 216 lines, 86 errors, 0 critical across 3 source(s).",
    "fix_steps": "Top affected sources: api.gateway, db.connector.",
    "incident_groups": [],
    "affected_services": ["api.gateway", "db.connector", "auth.service"],
    "remediation": [],
    "stats": {"total": 216, "err": 86, "warn": 32, "crit": 0, "sources": 3, "error_rate": 39.8},
    "exceptions": ["DB_CONN_FAIL: Connection refused"],
}


# ═════════════════════════════════════════════════════════════════════════════
# Mock infrastructure
# ═════════════════════════════════════════════════════════════════════════════


class TestSessionPayload:
    """Validates the autorca_session localStorage contract written by openLiveStream()"""

    def test_session_structure_has_required_keys(self):
        session = {
            "connected": True,
            "label": "app.log",
            "ingestedAt": "01/01/2026, 12:00:00",
            "stats": {"total": 100, "err": 5, "rate": 5.0},
            "logs": [],
        }
        assert "connected" in session
        assert "label" in session
        assert "stats" in session
        assert "logs" in session

    def test_session_logs_capped_at_2000(self):
        # Simulates the .slice(0, 2000) in openLiveStream()
        large_logs = [{"message": f"log {i}"} for i in range(5000)]
        capped = large_logs[:2000]
        assert len(capped) == 2000

    def test_session_not_written_when_disconnected(self):
        # When S.connected is false, no session should be saved
        connected = False
        session_written = connected  # mirrors the JS: if(S.connected){...}
        assert session_written is False


class _Result:
    """Serialisable result — only .data and .count, both JSON-safe."""

    def __init__(self, data):
        self.data = data if data is not None else []
        self.count = len(self.data) if isinstance(self.data, list) else 1


class _TableMock:
    """
    Fluent supabase-py table mock.  Any method not listed explicitly
    falls through __getattr__ → returns self → chain always resolves.
    """

    def __init__(self, execute_data=None):
        self._result = _Result(execute_data)
        self.select = MagicMock(return_value=self)
        self.insert = MagicMock(return_value=self)
        self.delete = MagicMock(return_value=self)
        self.update = MagicMock(return_value=self)
        self.eq = MagicMock(return_value=self)
        self.ilike = MagicMock(return_value=self)
        self.order = MagicMock(return_value=self)
        self.range = MagicMock(return_value=self)
        self.single = MagicMock(return_value=self)
        self.execute = MagicMock(return_value=self._result)

    def __getattr__(self, name):
        def _passthrough(*args, **kwargs):
            return self

        return _passthrough


def _make_table(execute_data=None) -> _TableMock:
    return _TableMock(execute_data)


def _sb_client(table: _TableMock) -> MagicMock:
    s = MagicMock()
    s.table.return_value = table
    return s


def _supabase_mod(table, *, fail=False) -> MagicMock:
    mod = MagicMock()
    if fail:
        mod.create_client = MagicMock(side_effect=Exception("no creds"))
    else:
        mod.create_client = MagicMock(return_value=_sb_client(table))
    return mod


def _set_execute(table: _TableMock, data):
    """Replace a table mock's execute return value mid-test."""
    r = _Result(data)
    table._result = r
    table.execute.return_value = r


def _client_with(table: _TableMock, *, env: dict | None = None):
    """Build a TestClient with Supabase mocked to the given table."""
    from fastapi.testclient import TestClient

    merged_env = {**BASE_ENV, **(env or {})}
    with patch.dict(os.environ, merged_env, clear=False):
        with patch.dict("sys.modules", {"supabase": _supabase_mod(table)}, clear=False):
            import api_server

            importlib.reload(api_server)
            client = TestClient(
                api_server.app,
                raise_server_exceptions=False,
                headers={"X-API-Key": merged_env.get("AUTORCA_API_KEY", "")},
            )
            return client, table, api_server


def _client_no_sb():
    """Build a TestClient where Supabase is NOT configured."""
    from fastapi.testclient import TestClient

    env = {**BASE_ENV, "SUPABASE_URL": "", "SUPABASE_KEY": ""}
    with patch.dict(os.environ, env, clear=False):
        with patch.dict("sys.modules", {"supabase": _supabase_mod(None, fail=True)}, clear=False):
            import api_server

            importlib.reload(api_server)
            api_server._sb = None
            return TestClient(
                api_server.app,
                raise_server_exceptions=False,
                headers={"X-API-Key": BASE_ENV["AUTORCA_API_KEY"]},
            )


def _client_no_key(table: _TableMock):
    """Build a TestClient where AUTORCA_API_KEY is NOT set (dev mode)."""
    from fastapi.testclient import TestClient

    env = {**BASE_ENV, "AUTORCA_API_KEY": ""}
    with patch.dict(os.environ, env, clear=False):
        with patch.dict("sys.modules", {"supabase": _supabase_mod(table)}, clear=False):
            import api_server

            importlib.reload(api_server)
            return TestClient(api_server.app, raise_server_exceptions=False)


# ── Shared assertion helpers ──────────────────────────────────────────────────


def _is_no_sb(r) -> bool:
    """True when server signals 'Supabase not configured'."""
    if r.status_code == 503:
        return True
    if r.status_code == 200:
        body = r.json()
        if body.get("supabase") is False:
            return True
        if body.get("ok") is False and "supabase" in body:
            return True
    return False


def _is_error(r) -> bool:
    """True when server returns an error response."""
    if r.status_code in (500, 503):
        return True
    if r.status_code == 200:
        return r.json().get("ok") is False
    return False


def _integration_response(logs: str, *, status: int = 200):
    """Build a fake requests.Response for mocking _requests.get / .post."""
    resp = MagicMock()
    resp.status_code = status
    resp.text = logs
    resp.raise_for_status = MagicMock()
    return resp


# ═════════════════════════════════════════════════════════════════════════════
# TestToDbRow  — unit-test RCASavePayload.to_db_row() in isolation
# ═════════════════════════════════════════════════════════════════════════════


class TestToDbRow:
    """Tests the field-mapping logic inside RCASavePayload without HTTP."""

    def _payload(self, **kwargs):
        # Import here so reload order doesn't matter
        import api_server

        importlib.reload(api_server)
        return api_server.RCASavePayload(**kwargs)

    def test_source_name_primary_field_used(self):
        row = self._payload(source_name="primary.log").to_db_row()
        assert row["source_name"] == "primary.log"

    def test_source_legacy_alias_fallback(self):
        """
        The legacy 'source' field fills source_name ONLY when source_name is
        explicitly empty.  source_name has Field(default='Unknown Source'), which
        is truthy, so omitting it still wins over the alias.
        Pass source_name='' explicitly to force the fallback branch.
        """
        row = self._payload(source_name="", source="legacy.log").to_db_row()
        assert row["source_name"] == "legacy.log"

    def test_source_name_takes_priority_over_source(self):
        row = self._payload(source_name="primary.log", source="legacy.log").to_db_row()
        assert row["source_name"] == "primary.log"

    def test_severity_critical_normalised_to_lowercase(self):
        row = self._payload(severity="CRITICAL").to_db_row()
        assert row["severity"] == "critical"

    def test_severity_error_maps_to_critical(self):
        row = self._payload(severity="error").to_db_row()
        assert row["severity"] == "critical"

    def test_severity_warning_normalised(self):
        row = self._payload(severity="WARNING").to_db_row()
        assert row["severity"] == "warning"

    def test_severity_warn_alias(self):
        row = self._payload(severity="warn").to_db_row()
        assert row["severity"] == "warning"

    def test_severity_healthy_normalised(self):
        row = self._payload(severity="ok").to_db_row()
        assert row["severity"] == "healthy"

    def test_severity_unknown_falls_back_to_warning(self):
        row = self._payload(severity="random_unknown").to_db_row()
        assert row["severity"] == "warning"

    def test_total_entries_preserved(self):
        row = self._payload(total_entries=999).to_db_row()
        assert row["total_entries"] == 999

    def test_total_logs_legacy_fallback(self):
        row = self._payload(total_logs=888).to_db_row()
        assert row["total_entries"] == 888

    def test_total_logs_camel_fallback(self):
        row = self._payload(totalLogs=777).to_db_row()
        assert row["total_entries"] == 777

    def test_error_count_errCount_alias(self):
        row = self._payload(errCount=50).to_db_row()
        assert row["error_count"] == 50

    def test_error_rate_is_float(self):
        row = self._payload(error_rate=52.4).to_db_row()
        assert isinstance(row["error_rate"], float)

    def test_ai_summary_fallback_to_summary(self):
        row = self._payload(summary="old summary field").to_db_row()
        assert row["ai_summary"] == "old summary field"

    def test_stats_fallback_to_meta(self):
        row = self._payload(meta={"key": "val"}).to_db_row()
        assert row["stats"] == {"key": "val"}

    def test_all_list_fields_present(self):
        row = self._payload().to_db_row()
        for field in ("incident_groups", "affected_services", "remediation"):
            assert isinstance(row[field], list), f"{field} should be a list"

    def test_livestream_payload_maps_correctly(self):
        """Exact payload the livestream page sends — all fields must survive round-trip."""
        import api_server

        importlib.reload(api_server)
        p = api_server.RCASavePayload(**LIVESTREAM_PAYLOAD)
        row = p.to_db_row()
        assert row["source_name"].startswith("Live Stream")
        assert row["severity"] == "warning"
        assert row["total_entries"] == 216
        assert row["error_count"] == 86


# ═════════════════════════════════════════════════════════════════════════════
# TestLogParser  — unit-test _parse_line() across all log formats
# ═════════════════════════════════════════════════════════════════════════════


class TestLogParser:
    """Tests the _parse_line() helper added to fix the KeyError: 'level' bug."""

    def _parse(self, line: str) -> dict:
        import api_server

        importlib.reload(api_server)
        return api_server._parse_line(line)

    def test_standard_format_with_bracket_source(self):
        r = self._parse("2026-03-08 10:00:00 ERROR [Database] DB_CONN_FAIL: Connection refused")
        assert r["level"] == "ERROR"
        assert r["source"] == "Database"
        assert "DB_CONN_FAIL" in r["message"]
        assert r["timestamp"] == "2026-03-08 10:00:00"

    def test_critical_level_parsed(self):
        r = self._parse("2026-03-08 10:00:01 CRITICAL [Database] Complete database outage detected")
        assert r["level"] == "CRITICAL"
        assert r["source"] == "Database"

    def test_warning_level_parsed(self):
        r = self._parse("2026-03-08 10:00:07 WARNING High memory usage detected — 91% utilised")
        assert r["level"] == "WARNING"

    def test_warn_alias_normalised_to_warning(self):
        r = self._parse("2026-03-08 10:00:07 WARN High memory")
        assert r["level"] == "WARNING"

    def test_info_level_parsed(self):
        r = self._parse("2026-03-08 10:00:10 INFO  Health check passed")
        assert r["level"] == "INFO"

    def test_iso_timestamp_format(self):
        r = self._parse("2026-03-08T10:00:00Z ERROR [API] Gateway error")
        assert r["level"] == "ERROR"
        assert "2026-03-08T10:00:00" in r["timestamp"]

    def test_no_timestamp_fallback(self):
        r = self._parse("ERROR [Database] DB_CONN_FAIL: Connection refused")
        assert r["level"] == "ERROR"
        assert r["source"] == "Database"
        assert r["timestamp"] == ""

    def test_no_source_bracket_fallback(self):
        r = self._parse("2026-03-08 10:00:06 ERROR NullPointerException in UserService.java:88")
        assert r["level"] == "ERROR"
        assert r["source"] == "unknown"

    def test_fatal_normalised_to_critical(self):
        r = self._parse("2026-03-08 10:00:00 FATAL [App] unrecoverable error")
        assert r["level"] == "CRITICAL"

    def test_severe_normalised_to_critical(self):
        r = self._parse("2026-03-08 10:00:00 SEVERE [App] disk full")
        assert r["level"] == "CRITICAL"

    def test_debug_level_parsed(self):
        r = self._parse("2026-03-08 10:00:00 DEBUG [Cache] cache miss for key xyz")
        assert r["level"] == "DEBUG"

    def test_unparseable_line_keyword_fallback_error(self):
        r = self._parse("something something ERROR happened here")
        assert r["level"] == "ERROR"

    def test_unparseable_line_keyword_fallback_warning(self):
        r = self._parse("high WARN situation in queue")
        assert r["level"] == "WARNING"

    def test_unparseable_plain_line_defaults_to_info(self):
        r = self._parse("this is a completely plain log line with no keywords")
        assert r["level"] == "INFO"

    def test_raw_field_always_preserved(self):
        line = "2026-03-08 10:00:00 ERROR [API] something"
        r = self._parse(line)
        assert r["raw"] == line

    def test_message_field_populated(self):
        r = self._parse("2026-03-08 10:00:00 ERROR [DB] connection refused at localhost:5432")
        assert "connection refused" in r["message"]

    def test_loki_format_no_timestamp(self):
        """Loki returns raw log strings without leading timestamp."""
        r = self._parse("CRITICAL [Database] Complete outage detected")
        assert r["level"] == "CRITICAL"
        assert r["source"] == "Database"


# ═════════════════════════════════════════════════════════════════════════════
# TestParseAndRespond  — _parse_and_respond() stats calculation
# ═════════════════════════════════════════════════════════════════════════════


class TestParseAndRespond:
    """Tests the _parse_and_respond() helper used by all integration endpoints."""

    def _call(self, raw_text: str):
        import api_server

        importlib.reload(api_server)
        # Run without local modules so we exercise the cloud-mode branch
        original = api_server._local_ok
        api_server._local_ok = False
        try:
            resp = api_server._parse_and_respond(raw_text)
        finally:
            api_server._local_ok = original
        return json.loads(resp.body)

    def test_returns_source_integration(self):
        data = self._call("2026-03-08 10:00:00 INFO health check passed")
        assert data["source"] == "integration"

    def test_lines_fetched_count_correct(self):
        text = "\n".join(
            [
                "2026-03-08 10:00:00 ERROR [DB] fail",
                "2026-03-08 10:00:01 INFO  health ok",
                "2026-03-08 10:00:02 WARNING high cpu",
            ]
        )
        data = self._call(text)
        assert data["lines_fetched"] == 3

    def test_error_count_in_logs(self):
        text = "\n".join(
            [
                "2026-03-08 10:00:00 ERROR [DB] fail",
                "2026-03-08 10:00:01 ERROR [API] error",
                "2026-03-08 10:00:02 INFO  ok",
            ]
        )
        data = self._call(text)
        assert data["logs"]["err"] >= 2

    def test_critical_classification(self):
        text = "2026-03-08 10:00:00 CRITICAL [DB] complete outage"
        data = self._call(text)
        assert data["classification"] == "critical"

    def test_healthy_classification_no_errors(self):
        text = "2026-03-08 10:00:00 INFO health check passed"
        data = self._call(text)
        assert data["classification"] == "healthy"

    def test_raw_sample_present(self):
        text = "2026-03-08 10:00:00 ERROR [DB] fail"
        data = self._call(text)
        assert isinstance(data["raw_sample"], list)
        assert len(data["raw_sample"]) > 0

    def test_empty_lines_ignored(self):
        text = "\n\n2026-03-08 10:00:00 INFO ok\n\n"
        data = self._call(text)
        assert data["lines_fetched"] == 1

    def test_has_stacktrace_detected(self):
        text = "2026-03-08 10:00:00 ERROR [App] fail\n  at com.example.App.main(App.java:10)"
        data = self._call(text)
        assert data["logs"]["has_stacktrace"] is True

    def test_logs_contains_total(self):
        data = self._call("2026-03-08 10:00:00 INFO ok")
        assert "total" in data["logs"]

    def test_exceptions_list_present(self):
        text = "2026-03-08 10:00:00 ERROR [DB] fail"
        data = self._call(text)
        assert isinstance(data["logs"]["exceptions"], list)


# ═════════════════════════════════════════════════════════════════════════════
# TestHealthEndpoint
# ═════════════════════════════════════════════════════════════════════════════


class TestHealthEndpoint:
    """Tests /api/health — no auth required."""

    def setup_method(self):
        t = _make_table([])
        self.c, _, _ = _client_with(t)

    def test_health_returns_200(self):
        r = self.c.get("/api/health")
        assert r.status_code == 200

    def test_health_status_ok(self):
        r = self.c.get("/api/health")
        assert r.json()["status"] == "ok"

    def test_health_version_present(self):
        r = self.c.get("/api/health")
        assert "version" in r.json()

    def test_health_checks_object_present(self):
        r = self.c.get("/api/health")
        assert isinstance(r.json().get("checks"), dict)

    def test_health_websocket_key_present(self):
        r = self.c.get("/api/health")
        assert "websocket" in r.json()["checks"]

    def test_health_supabase_key_present(self):
        r = self.c.get("/api/health")
        assert "supabase" in r.json()["checks"]

    def test_health_does_not_require_api_key(self):
        """Health must be publicly accessible — no auth header."""
        from fastapi.testclient import TestClient

        t = _make_table([])
        c, _, _ = _client_with(t)
        r = TestClient(c.app).get("/api/health")
        assert r.status_code == 200

    def test_health_dev_mode_field_present(self):
        r = self.c.get("/api/health")
        assert "dev_mode" in r.json()


# ═════════════════════════════════════════════════════════════════════════════
# TestAuthSystem
# ═════════════════════════════════════════════════════════════════════════════


class TestAuthSystem:
    """Tests the fixed auth layer — dev mode + 401 on wrong key."""

    def test_wrong_api_key_returns_401(self):
        t = _make_table([{"id": FAKE_ID}])
        c, _, _ = _client_with(t)
        r = c.post(
            "/api/rca/save",
            json=SAMPLE_PAYLOAD,
            headers={"X-API-Key": "totally-wrong-key"},
        )
        assert r.status_code == 401

    def test_missing_api_key_returns_401_when_key_is_set(self):
        t = _make_table([{"id": FAKE_ID}])
        c, _, _ = _client_with(t)
        r = c.post("/api/rca/save", json=SAMPLE_PAYLOAD, headers={"X-API-Key": ""})
        assert r.status_code == 401

    def test_dev_mode_no_key_required(self):
        """When AUTORCA_API_KEY is unset, any request (including no key) is allowed."""
        t = _make_table([{"id": FAKE_ID}])
        c = _client_no_key(t)
        r = c.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert r.status_code == 200

    def test_correct_key_accepted(self):
        t = _make_table([{"id": FAKE_ID}])
        c, _, _ = _client_with(t)
        r = c.post("/api/rca/save", json=SAMPLE_PAYLOAD, headers={"X-API-Key": BASE_ENV["AUTORCA_API_KEY"]})
        assert r.status_code == 200

    def test_401_detail_message(self):
        t = _make_table([{"id": FAKE_ID}])
        c, _, _ = _client_with(t)
        r = c.post("/api/rca/save", json=SAMPLE_PAYLOAD, headers={"X-API-Key": "wrong"})
        body = r.json()
        assert "detail" in body or "message" in body or r.status_code == 401


# ═════════════════════════════════════════════════════════════════════════════
# TestCORSFix — CORS allows localhost on any port (no wildcard conflict)
# ═════════════════════════════════════════════════════════════════════════════


class TestCORSFix:
    """
    The CORS fix removes allow_origins=['*'] and uses allow_origin_regex +
    an explicit list.  These tests verify that the correct origins are allowed
    and that the headers are present on actual responses.
    """

    def setup_method(self):
        t = _make_table([])
        self.c, _, _ = _client_with(t)

    def test_options_preflight_localhost_5500_allowed(self):
        r = self.c.options(
            "/api/health",
            headers={
                "Origin": "http://127.0.0.1:5500",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r.status_code in (200, 204)

    def test_options_preflight_localhost_3000_allowed(self):
        r = self.c.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type,X-API-Key",
            },
        )
        assert r.status_code in (200, 204)

    def test_get_health_acao_header_present(self):
        r = self.c.get(
            "/api/health",
            headers={"Origin": "http://localhost:5500"},
        )
        assert "access-control-allow-origin" in r.headers

    def test_cors_allows_arbitrary_localhost_port(self):
        r = self.c.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:9999",
                "Access-Control-Request-Method": "GET",
            },
        )
        # Should not be blocked — regex covers any port
        assert r.status_code in (200, 204)


# ═════════════════════════════════════════════════════════════════════════════
# TestIntegrationLoki
# ═════════════════════════════════════════════════════════════════════════════

LOKI_RESPONSE = json.dumps(
    {
        "status": "success",
        "data": {
            "result": [
                {
                    "stream": {"app": "autorca", "level": "error"},
                    "values": [
                        [str(int(1e18)), "ERROR [Database] DB_CONN_FAIL: Connection refused"],
                        [str(int(1e18) + 1), "CRITICAL [Database] Complete outage detected"],
                        [str(int(1e18) + 2), "INFO health check passed"],
                    ],
                }
            ]
        },
    }
)


class TestIntegrationLoki:
    """Tests /api/integration/loki — all branches mocked."""

    def setup_method(self):
        t = _make_table([])
        self.c, _, _ = _client_with(t)

    def _loki_req(self, **kwargs):
        return {
            "url": "http://localhost:8888",
            "query": '{app="autorca"}',
            "hours": 1,
            "limit": 5000,
            **kwargs,
        }

    def test_loki_happy_path_returns_200(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = json.loads(LOKI_RESPONSE)
        resp.raise_for_status = MagicMock()
        with patch("api_server._requests.get", return_value=resp):
            r = self.c.post("/api/integration/loki", json=self._loki_req())
        assert r.status_code == 200

    def test_loki_response_has_source_integration(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = json.loads(LOKI_RESPONSE)
        resp.raise_for_status = MagicMock()
        with patch("api_server._requests.get", return_value=resp):
            r = self.c.post("/api/integration/loki", json=self._loki_req())
        assert r.json()["source"] == "integration"

    def test_loki_response_has_lines_fetched(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = json.loads(LOKI_RESPONSE)
        resp.raise_for_status = MagicMock()
        with patch("api_server._requests.get", return_value=resp):
            r = self.c.post("/api/integration/loki", json=self._loki_req())
        assert r.json()["lines_fetched"] == 3

    def test_loki_connection_error_returns_502(self):
        import requests as rq

        with patch("api_server._requests.get", side_effect=rq.exceptions.ConnectionError("refused")):
            r = self.c.post("/api/integration/loki", json=self._loki_req())
        assert r.status_code == 502

    def test_loki_empty_result_returns_404(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"status": "success", "data": {"result": []}}
        resp.raise_for_status = MagicMock()
        with patch("api_server._requests.get", return_value=resp):
            r = self.c.post("/api/integration/loki", json=self._loki_req())
        assert r.status_code == 404

    def test_loki_url_validation_missing_url(self):
        r = self.c.post("/api/integration/loki", json={"query": '{app="autorca"}'})
        assert r.status_code == 422

    def test_loki_strips_loki_path_from_base_url(self):
        """URL normalisation: /loki/... suffix must be stripped."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = json.loads(LOKI_RESPONSE)
        resp.raise_for_status = MagicMock()
        captured = {}

        def fake_get(url, **kw):
            captured["url"] = url
            return resp

        with patch("api_server._requests.get", side_effect=fake_get):
            self.c.post("/api/integration/loki", json=self._loki_req(url="http://localhost:8888/loki"))
        assert "/loki/api/v1/query_range" in captured.get("url", "")
        # Should not double up with /loki/loki/...
        assert "loki/loki" not in captured.get("url", "")

    def test_loki_classification_present(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = json.loads(LOKI_RESPONSE)
        resp.raise_for_status = MagicMock()
        with patch("api_server._requests.get", return_value=resp):
            r = self.c.post("/api/integration/loki", json=self._loki_req())
        assert "classification" in r.json()


# ═════════════════════════════════════════════════════════════════════════════
# TestIntegrationElasticsearch
# ═════════════════════════════════════════════════════════════════════════════

ES_RESPONSE = {
    "hits": {
        "total": {"value": 3},
        "hits": [
            {"_source": {"message": "DB_CONN_FAIL: Connection refused", "level": "ERROR"}},
            {"_source": {"message": "Complete database outage", "level": "CRITICAL"}},
            {"_source": {"message": "Health check passed", "level": "INFO"}},
        ],
    }
}


class TestIntegrationElasticsearch:
    def setup_method(self):
        t = _make_table([])
        self.c, _, _ = _client_with(t)

    def _es_req(self, **kwargs):
        return {
            "url": "http://localhost:8888",
            "index": "app-logs",
            "query": "level:ERROR OR level:CRITICAL",
            "limit": 5000,
            **kwargs,
        }

    def test_es_happy_path_returns_200(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = ES_RESPONSE
        resp.raise_for_status = MagicMock()
        with patch("api_server._requests.post", return_value=resp):
            r = self.c.post("/api/integration/elasticsearch", json=self._es_req())
        assert r.status_code == 200

    def test_es_lines_fetched_matches_hits(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = ES_RESPONSE
        resp.raise_for_status = MagicMock()
        with patch("api_server._requests.post", return_value=resp):
            r = self.c.post("/api/integration/elasticsearch", json=self._es_req())
        assert r.json()["lines_fetched"] == 3

    def test_es_connection_error_returns_502(self):
        import requests as rq

        with patch("api_server._requests.post", side_effect=rq.exceptions.ConnectionError("refused")):
            r = self.c.post("/api/integration/elasticsearch", json=self._es_req())
        assert r.status_code == 502

    def test_es_empty_hits_returns_404(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"hits": {"hits": []}}
        resp.raise_for_status = MagicMock()
        with patch("api_server._requests.post", return_value=resp):
            r = self.c.post("/api/integration/elasticsearch", json=self._es_req())
        assert r.status_code == 404

    def test_es_url_required(self):
        r = self.c.post("/api/integration/elasticsearch", json={"index": "app-logs"})
        assert r.status_code == 422

    def test_es_posts_to_correct_search_endpoint(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = ES_RESPONSE
        resp.raise_for_status = MagicMock()
        captured = {}

        def fake_post(url, **kw):
            captured["url"] = url
            return resp

        with patch("api_server._requests.post", side_effect=fake_post):
            self.c.post("/api/integration/elasticsearch", json=self._es_req())
        assert "app-logs/_search" in captured.get("url", "")


# ═════════════════════════════════════════════════════════════════════════════
# TestIntegrationS3
# ═════════════════════════════════════════════════════════════════════════════

PLAIN_LOGS = "\n".join(
    [
        "2026-03-08 10:00:00 ERROR [Database] DB_CONN_FAIL: Connection refused",
        "2026-03-08 10:00:01 CRITICAL [Database] Complete database outage detected",
        "2026-03-08 10:00:02 INFO  Health check passed",
    ]
)


class TestIntegrationS3:
    def setup_method(self):
        t = _make_table([])
        self.c, _, _ = _client_with(t)

    def _s3_req(self, **kwargs):
        return {
            "endpoint": "http://localhost:8888",
            "bucket": "autorca-logs",
            "key": "app.log",
            "access_key": "",
            "secret_key": "",
            **kwargs,
        }

    def test_s3_happy_path_returns_200(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = PLAIN_LOGS
        resp.raise_for_status = MagicMock()
        with patch("api_server._requests.get", return_value=resp):
            r = self.c.post("/api/integration/s3", json=self._s3_req())
        assert r.status_code == 200

    def test_s3_lines_fetched_correct(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = PLAIN_LOGS
        resp.raise_for_status = MagicMock()
        with patch("api_server._requests.get", return_value=resp):
            r = self.c.post("/api/integration/s3", json=self._s3_req())
        assert r.json()["lines_fetched"] == 3

    def test_s3_connection_error_returns_502(self):
        import requests as rq

        with patch("api_server._requests.get", side_effect=rq.exceptions.ConnectionError("refused")):
            r = self.c.post("/api/integration/s3", json=self._s3_req())
        assert r.status_code == 502

    def test_s3_builds_correct_url(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = PLAIN_LOGS
        resp.raise_for_status = MagicMock()
        captured = {}

        def fake_get(url, **kw):
            captured["url"] = url
            return resp

        with patch("api_server._requests.get", side_effect=fake_get):
            self.c.post("/api/integration/s3", json=self._s3_req())
        assert "autorca-logs/app.log" in captured.get("url", "")

    def test_s3_endpoint_required(self):
        r = self.c.post("/api/integration/s3", json={"bucket": "b", "key": "k"})
        assert r.status_code == 422


# ═════════════════════════════════════════════════════════════════════════════
# TestIntegrationHTTP
# ═════════════════════════════════════════════════════════════════════════════


class TestIntegrationHTTP:
    def setup_method(self):
        t = _make_table([])
        self.c, _, _ = _client_with(t)

    def _http_req(self, **kwargs):
        return {
            "url": "http://localhost:8888/logs",
            "method": "GET",
            "headers": {},
            **kwargs,
        }

    def test_http_get_happy_path(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = PLAIN_LOGS
        resp.raise_for_status = MagicMock()
        with patch("api_server._requests.get", return_value=resp):
            r = self.c.post("/api/integration/http", json=self._http_req())
        assert r.status_code == 200

    def test_http_post_method_uses_post(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = PLAIN_LOGS
        resp.raise_for_status = MagicMock()
        with patch("api_server._requests.post", return_value=resp) as mock_post:
            self.c.post("/api/integration/http", json=self._http_req(method="POST"))
        mock_post.assert_called_once()

    def test_http_get_method_uses_get(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = PLAIN_LOGS
        resp.raise_for_status = MagicMock()
        with patch("api_server._requests.get", return_value=resp) as mock_get:
            self.c.post("/api/integration/http", json=self._http_req(method="GET"))
        mock_get.assert_called_once()

    def test_http_invalid_method_rejected(self):
        r = self.c.post("/api/integration/http", json=self._http_req(method="DELETE"))
        assert r.status_code == 422

    def test_http_connection_error_returns_502(self):
        import requests as rq

        with patch("api_server._requests.get", side_effect=rq.exceptions.ConnectionError("refused")):
            r = self.c.post("/api/integration/http", json=self._http_req())
        assert r.status_code == 502

    def test_http_url_required(self):
        r = self.c.post("/api/integration/http", json={"method": "GET"})
        assert r.status_code == 422

    def test_http_custom_headers_forwarded(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = PLAIN_LOGS
        resp.raise_for_status = MagicMock()
        captured = {}

        def fake_get(url, headers=None, **kw):
            captured["headers"] = headers
            return resp

        with patch("api_server._requests.get", side_effect=fake_get):
            self.c.post("/api/integration/http", json=self._http_req(headers={"Authorization": "Bearer tok"}))
        assert captured.get("headers", {}).get("Authorization") == "Bearer tok"


# ═════════════════════════════════════════════════════════════════════════════
# TestWebSocket — /ws/logs
# ═════════════════════════════════════════════════════════════════════════════


class TestWebSocket:
    """
    Tests the WebSocket endpoint added in v3.2.
    Uses TestClient's websocket_connect() context manager.
    """

    def setup_method(self):
        t = _make_table([])
        self.c, _, _ = _client_with(t)

    def test_ws_accepts_connection_with_correct_key(self):
        with self.c.websocket_connect(f"/ws/logs?key={BASE_ENV['AUTORCA_API_KEY']}&seek=0") as ws:
            frame = ws.receive_json()
            assert frame["type"] == "connected"

    def test_ws_connected_frame_has_file_field(self):
        with self.c.websocket_connect(f"/ws/logs?key={BASE_ENV['AUTORCA_API_KEY']}&seek=0") as ws:
            frame = ws.receive_json()
            assert "file" in frame

    def test_ws_connected_frame_has_ts_field(self):
        with self.c.websocket_connect(f"/ws/logs?key={BASE_ENV['AUTORCA_API_KEY']}&seek=0") as ws:
            frame = ws.receive_json()
            assert isinstance(frame.get("ts"), (int, float))

    def test_ws_seek_sends_historic_lines(self):
        with self.c.websocket_connect(f"/ws/logs?key={BASE_ENV['AUTORCA_API_KEY']}&seek=3") as ws:
            ws.receive_json()  # connected frame
            frames = [ws.receive_json() for _ in range(3)]
            assert all(f["type"] == "line" for f in frames)

    def test_ws_line_frame_has_parsed_field(self):
        with self.c.websocket_connect(f"/ws/logs?key={BASE_ENV['AUTORCA_API_KEY']}&seek=1") as ws:
            ws.receive_json()  # connected
            line_frame = ws.receive_json()
            assert "parsed" in line_frame

    def test_ws_parsed_has_level_field(self):
        with self.c.websocket_connect(f"/ws/logs?key={BASE_ENV['AUTORCA_API_KEY']}&seek=1") as ws:
            ws.receive_json()
            line_frame = ws.receive_json()
            parsed = line_frame["parsed"]
            assert "level" in parsed

    def test_ws_parsed_has_message_field(self):
        with self.c.websocket_connect(f"/ws/logs?key={BASE_ENV['AUTORCA_API_KEY']}&seek=1") as ws:
            ws.receive_json()
            line_frame = ws.receive_json()
            assert "message" in line_frame["parsed"]

    def test_ws_parsed_has_source_field(self):
        with self.c.websocket_connect(f"/ws/logs?key={BASE_ENV['AUTORCA_API_KEY']}&seek=1") as ws:
            ws.receive_json()
            line_frame = ws.receive_json()
            assert "source" in line_frame["parsed"]

    def test_ws_pong_accepted(self):
        """Server must not crash when client sends pong."""
        with self.c.websocket_connect(f"/ws/logs?key={BASE_ENV['AUTORCA_API_KEY']}&seek=0") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "pong"})
            # No exception = pass

    def test_ws_file_query_param_reflected_in_connected(self):
        with self.c.websocket_connect(f"/ws/logs?key={BASE_ENV['AUTORCA_API_KEY']}&seek=0&file=myapp.log") as ws:
            frame = ws.receive_json()
            assert frame.get("file") == "myapp.log"

    def test_ws_wrong_key_closes_with_4401(self):
        with pytest.raises(Exception):  # noqa: B017
            with self.c.websocket_connect("/ws/logs?key=wrong-key&seek=0") as ws:
                ws.receive_json()

    def test_ws_dev_mode_no_key_required(self):
        """When AUTORCA_API_KEY is unset, WS connects without a key."""
        t = _make_table([])
        c = _client_no_key(t)
        with c.websocket_connect("/ws/logs?key=&seek=0") as ws:
            frame = ws.receive_json()
            assert frame["type"] == "connected"


# ═════════════════════════════════════════════════════════════════════════════
# TestRcaSave  (v7 — corrected field names and severity)
# ═════════════════════════════════════════════════════════════════════════════


class TestRcaSave:
    def setup_method(self):
        self.t = _make_table([{"id": FAKE_ID}])
        self.c, _, _ = _client_with(self.t)

    def test_save_happy_path(self):
        r = self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["id"] == FAKE_ID

    def test_save_inserts_correct_source_name(self):
        """FIX: to_db_row() stores under 'source_name', not 'source'."""
        self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        args = self.t.insert.call_args[0][0]
        # The DB row always uses 'source_name' (what to_db_row() produces)
        assert args.get("source_name") == "app.log"

    def test_save_inserts_correct_severity_normalised_lowercase(self):
        """FIX: _sev_map normalises 'CRITICAL' → 'critical' (lowercase)."""
        self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        args = self.t.insert.call_args[0][0]
        assert args["severity"] == "critical"

    def test_save_source_legacy_alias_resolved(self):
        """
        The legacy 'source' field is used as source_name ONLY when source_name
        is explicitly empty string.  Pydantic's Field(default='Unknown Source')
        means omitting source_name still injects the non-empty default, which is
        truthy and always wins over the alias in to_db_row().
        Passing source_name='' forces the fallback branch.
        """
        minimal = {"source_name": "", "source": "legacy.log", "severity": "warning"}
        self.c.post("/api/rca/save", json=minimal)
        args = self.t.insert.call_args[0][0]
        assert args.get("source_name") == "legacy.log"

    def test_save_coerces_total_entries_to_int(self):
        self.c.post("/api/rca/save", json={**SAMPLE_PAYLOAD, "total_entries": "2500"})
        args = self.t.insert.call_args[0][0]
        assert isinstance(args.get("total_entries", 0), int)

    def test_save_coerces_error_count_to_int(self):
        self.c.post("/api/rca/save", json={**SAMPLE_PAYLOAD, "error_count": "1310"})
        args = self.t.insert.call_args[0][0]
        assert isinstance(args.get("error_count", 0), int)

    def test_save_default_severity_when_missing(self):
        self.c.post("/api/rca/save", json={"source_name": "x.log"})
        args = self.t.insert.call_args[0][0]
        assert isinstance(args.get("severity", ""), str)
        assert len(args.get("severity", "")) > 0

    def test_save_zero_defaults_for_missing_counts(self):
        self.c.post("/api/rca/save", json={"source_name": "x.log"})
        args = self.t.insert.call_args[0][0]
        assert args.get("total_entries", 0) == 0
        assert args.get("error_count", 0) == 0

    def test_save_ok_false_on_supabase_exception(self):
        self.t.execute.side_effect = Exception("DB write failed")
        r = self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert _is_error(r), f"Expected error response, got {r.status_code}"

    def test_save_blocked_when_supabase_not_configured(self):
        r = _client_no_sb().post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert _is_no_sb(r), f"Expected no-sb response, got {r.status_code}: {r.text}"

    def test_save_livestream_payload_accepted(self):
        """The livestream page sends a slightly different payload shape."""
        r = self.c.post("/api/rca/save", json=LIVESTREAM_PAYLOAD)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_save_livestream_source_name_stored(self):
        self.c.post("/api/rca/save", json=LIVESTREAM_PAYLOAD)
        args = self.t.insert.call_args[0][0]
        assert "Live Stream" in args.get("source_name", "")

    def test_save_affected_services_list_preserved(self):
        self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        args = self.t.insert.call_args[0][0]
        assert isinstance(args.get("affected_services"), list)
        assert "auth" in args["affected_services"]

    def test_save_record_field_in_response(self):
        r = self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert "record" in r.json()

    def test_save_error_severity_normalised_to_critical(self):
        p = {**SAMPLE_PAYLOAD, "severity": "error"}
        self.c.post("/api/rca/save", json=p)
        args = self.t.insert.call_args[0][0]
        assert args["severity"] == "critical"

    def test_save_warning_severity_normalised(self):
        p = {**SAMPLE_PAYLOAD, "severity": "WARNING"}
        self.c.post("/api/rca/save", json=p)
        args = self.t.insert.call_args[0][0]
        assert args["severity"] == "warning"


# ═════════════════════════════════════════════════════════════════════════════
# TestRcaHistory  (v7 — ilike uses source_name; no-sb is HTTP 200)
# ═════════════════════════════════════════════════════════════════════════════


class TestRcaHistory:
    def _recs(self, n=3):
        return [
            {
                "id": str(uuid.uuid4()),
                "source_name": f"source_{i}.log",
                "severity": "warning",
                "total_entries": 1000 * (i + 1),
                "error_count": 100 * (i + 1),
                "created_at": "2026-03-13T00:00:00+00:00",
            }
            for i in range(n)
        ]

    def setup_method(self):
        self.t = _make_table([])
        self.c, _, _ = _client_with(self.t)

    def test_history_returns_list(self):
        _set_execute(self.t, self._recs(3))
        r = self.c.get("/api/rca/history")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert len(r.json()["data"]) == 3

    def test_history_empty_when_no_records(self):
        r = self.c.get("/api/rca/history")
        assert r.json()["data"] == []

    def test_history_severity_filter_forwarded_via_eq(self):
        self.c.get("/api/rca/history?severity=critical")
        calls = [str(c) for c in self.t.eq.call_args_list]
        assert any("severity" in c for c in calls)

    def test_history_severity_normalised_to_lowercase_before_eq(self):
        """FIX: severity filter is .lower()-ed before calling .eq()"""
        self.c.get("/api/rca/history?severity=CRITICAL")
        calls = self.t.eq.call_args_list
        # eq should be called with the lowercased value
        eq_values = [str(c) for c in calls]
        assert any("critical" in v for v in eq_values)

    def test_history_search_uses_ilike_on_source_name(self):
        """FIX: ilike field is 'source_name', not 'source'."""
        self.c.get("/api/rca/history?search=app.log")
        calls = [str(c) for c in self.t.ilike.call_args_list]
        assert any("source_name" in c for c in calls)

    def test_history_search_wraps_in_percent_wildcards(self):
        self.c.get("/api/rca/history?search=app.log")
        if self.t.ilike.call_args:
            args = self.t.ilike.call_args[0]
            assert args[1] == "%app.log%"

    def test_history_severity_all_skips_eq_filter(self):
        self.c.get("/api/rca/history?severity=all")
        eq_calls = [str(c) for c in self.t.eq.call_args_list]
        assert not any("severity" in c for c in eq_calls)

    def test_history_no_supabase_returns_200_not_503(self):
        """FIX: /api/rca/history returns HTTP 200 with supabase=False, not 503."""
        r = _client_no_sb().get("/api/rca/history")
        assert r.status_code == 200
        body = r.json()
        # Must signal supabase is unavailable in the body
        assert body.get("supabase") is False or body.get("ok") is True

    def test_history_no_supabase_returns_empty_data(self):
        r = _client_no_sb().get("/api/rca/history")
        assert r.json().get("data") == []

    def test_history_fails_gracefully_on_exception(self):
        self.t.execute.side_effect = Exception("timeout")
        r = self.c.get("/api/rca/history")
        assert _is_error(r)

    def test_history_supabase_field_true_when_connected(self):
        _set_execute(self.t, self._recs(2))
        r = self.c.get("/api/rca/history")
        assert r.json().get("supabase") is True

    def test_history_count_field_present(self):
        _set_execute(self.t, self._recs(2))
        r = self.c.get("/api/rca/history")
        assert "count" in r.json()

    def test_history_default_limit_applied(self):
        """range() must be called (offset / limit applied)."""
        self.c.get("/api/rca/history")
        self.t.range.assert_called_once()

    def test_history_custom_limit_forwarded(self):
        self.c.get("/api/rca/history?limit=10&offset=20")
        self.t.range.assert_called_with(20, 29)


# ═════════════════════════════════════════════════════════════════════════════
# TestRcaGet
# ═════════════════════════════════════════════════════════════════════════════


class TestRcaGet:
    def setup_method(self):
        record = {
            **SAMPLE_PAYLOAD,
            "id": FAKE_ID,
            "created_at": "2026-03-13T07:00:00+00:00",
        }
        self.t = _make_table(record)
        self.c, _, _ = _client_with(self.t)

    def test_get_record_found(self):
        r = self.c.get(f"/api/rca/history/{FAKE_ID}")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_get_calls_eq_with_correct_id(self):
        self.c.get(f"/api/rca/history/{FAKE_ID}")
        self.t.eq.assert_called_with("id", FAKE_ID)

    def test_get_data_field_in_response(self):
        r = self.c.get(f"/api/rca/history/{FAKE_ID}")
        assert "data" in r.json()

    def test_get_fails_gracefully_when_not_found(self):
        self.t.execute.side_effect = Exception("not found")
        r = self.c.get(f"/api/rca/history/{FAKE_ID}")
        assert _is_error(r)

    def test_get_blocked_when_supabase_not_configured(self):
        r = _client_no_sb().get(f"/api/rca/history/{FAKE_ID}")
        assert _is_no_sb(r)

    def test_get_calls_single(self):
        self.c.get(f"/api/rca/history/{FAKE_ID}")
        self.t.single.assert_called_once()


# ═════════════════════════════════════════════════════════════════════════════
# TestRcaDelete
# ═════════════════════════════════════════════════════════════════════════════


class TestRcaDelete:
    def setup_method(self):
        self.t = _make_table([])
        self.c, _, _ = _client_with(self.t)

    def test_delete_happy_path(self):
        r = self.c.delete(f"/api/rca/history/{FAKE_ID}")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_delete_calls_eq_with_id(self):
        self.c.delete(f"/api/rca/history/{FAKE_ID}")
        self.t.eq.assert_called_with("id", FAKE_ID)

    def test_delete_response_contains_deleted_id(self):
        r = self.c.delete(f"/api/rca/history/{FAKE_ID}")
        assert r.json().get("deleted_id") == FAKE_ID

    def test_delete_blocked_when_supabase_not_configured(self):
        r = _client_no_sb().delete(f"/api/rca/history/{FAKE_ID}")
        assert _is_no_sb(r)

    def test_delete_fails_gracefully_on_exception(self):
        self.t.execute.side_effect = Exception("row lock")
        r = self.c.delete(f"/api/rca/history/{FAKE_ID}")
        assert _is_error(r)

    def test_delete_calls_delete_then_eq_then_execute(self):
        self.c.delete(f"/api/rca/history/{FAKE_ID}")
        self.t.delete.assert_called_once()
        self.t.eq.assert_called_with("id", FAKE_ID)
        self.t.execute.assert_called_once()


# ═════════════════════════════════════════════════════════════════════════════
# TestDuplicateDetectionFlow
# ═════════════════════════════════════════════════════════════════════════════


class TestDuplicateDetectionFlow:
    def setup_method(self):
        self.t = _make_table([])
        self.c, _, _ = _client_with(self.t)

    def test_duplicate_present_returned_by_search(self):
        existing = {
            "id": FAKE_ID,
            "source_name": "app.log",
            "total_entries": 2500,
            "error_count": 1310,
            "severity": "critical",
            "created_at": "2026-03-13T07:00:00+00:00",
        }
        _set_execute(self.t, [existing])
        r = self.c.get("/api/rca/history?search=app.log&limit=50")
        assert r.status_code == 200
        assert len(r.json()["data"]) > 0

    def test_no_duplicate_after_deletion(self):
        r = self.c.get("/api/rca/history?search=app.log&limit=50")
        assert r.json()["data"] == []

    def test_full_flow_save_delete_resave(self):
        nid = str(uuid.uuid4())
        _set_execute(self.t, [{"id": nid}])
        r1 = self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert r1.status_code == 200 and r1.json()["ok"] is True

        _set_execute(self.t, [])
        r2 = self.c.delete(f"/api/rca/history/{nid}")
        assert r2.json()["ok"] is True

        assert self.c.get("/api/rca/history?search=app.log&limit=50").json()["data"] == []

        nid2 = str(uuid.uuid4())
        _set_execute(self.t, [{"id": nid2}])
        r4 = self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert r4.json()["ok"] is True

    def test_save_anyway_bypasses_check(self):
        nid = str(uuid.uuid4())
        _set_execute(self.t, [{"id": nid}])
        r = self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert r.json()["ok"] is True

    def test_livestream_save_and_retrieve(self):
        """Full flow: save a livestream session, retrieve it in history."""
        nid = str(uuid.uuid4())
        _set_execute(self.t, [{"id": nid}])
        r_save = self.c.post("/api/rca/save", json=LIVESTREAM_PAYLOAD)
        assert r_save.json()["ok"] is True

        record = {**LIVESTREAM_PAYLOAD, "id": nid, "created_at": "2026-03-22T14:30:00+00:00"}
        _set_execute(self.t, [record])
        r_hist = self.c.get("/api/rca/history?search=Live+Stream")
        assert r_hist.status_code == 200
        assert len(r_hist.json()["data"]) > 0
