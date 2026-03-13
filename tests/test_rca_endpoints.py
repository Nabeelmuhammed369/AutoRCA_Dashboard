"""
tests/test_rca_endpoints.py
============================
Tests for the RCA save/history/get/delete endpoints in api_server.py.

Key design decisions
--------------------
* api_server.py stores the Supabase client in module-level `_sb` (private).
  We patch `api_server._sb` directly — no need to mock supabase.create_client,
  which avoids the `ModuleNotFoundError: No module named 'supabase'` CI error
  (supabase package only needs to be installed in production, not in tests).

* api_server.py RCASavePayload uses field names:
    source, classification, severity, summary, total_logs, error_count, meta
  The test payload must match these names.

* History response shape from api_server.py:
    {"records": [...], "total": N, "supabase": True}
  (NOT {"ok": True, "data": [...]})

* Severity filter is uppercased by the server: .eq("severity", "CRITICAL")

* Search filter uses .or_() not .ilike() directly.

* GET /api/rca/history/{id} returns result.data directly (no ok/data wrapper).

* History endpoint returns 200 with empty records when _sb is None (graceful).
  Only save/get/delete raise 503 when _sb is None.
"""

import importlib
import os
import sys
import uuid
from unittest.mock import MagicMock, patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAKE_ID = str(uuid.uuid4())

BASE_ENV = {
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_KEY": "test-key",
    "AUTORCA_API_KEY": "test-api-key",
}

# Payload using the actual field names from RCASavePayload in api_server.py
SAMPLE_PAYLOAD = {
    "source": "app.log",
    "classification": "Database",
    "severity": "critical",
    "summary": "High error rate detected.",
    "total_logs": 2500,
    "error_count": 1310,
    "meta": {"rate": 52.4},
}

HEADERS = {"X-API-Key": "test-api-key"}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_table(execute_data=None):
    """Fluent supabase-py table mock — every chain method returns self."""
    t = MagicMock()
    for m in ("select", "insert", "delete", "update", "eq", "or_", "ilike", "order", "range", "single", "desc"):
        getattr(t, m).return_value = t
    t.execute.return_value = MagicMock(data=execute_data if execute_data is not None else [])
    return t


def _make_sb(table):
    """Minimal Supabase client mock."""
    s = MagicMock()
    s.table.return_value = table
    return s


def _get_client(sb_mock=None):
    """
    Return a FastAPI TestClient with api_server._sb replaced by sb_mock.
    Patches at the module level AFTER import — no supabase package needed.
    """
    from fastapi.testclient import TestClient

    with patch.dict(os.environ, BASE_ENV, clear=False):
        import api_server

        importlib.reload(api_server)

    # Directly replace the private _sb with our mock (or None)
    import api_server as _api

    _api._sb = sb_mock
    return TestClient(_api.app, raise_server_exceptions=False)


# ═════════════════════════════════════════════════════════════════════════════
# POST /api/rca/save
# ═════════════════════════════════════════════════════════════════════════════
class TestRcaSave:
    def setup_method(self):
        self.t = _make_table([{"id": FAKE_ID}])
        self.sb = _make_sb(self.t)
        self.c = _get_client(self.sb)

    def test_save_happy_path(self):
        r = self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD, headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["id"] == FAKE_ID

    def test_save_inserts_correct_source(self):
        self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD, headers=HEADERS)
        inserted = self.t.insert.call_args[0][0]
        assert inserted["source"] == "app.log"

    def test_save_inserts_correct_severity_uppercased(self):
        self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD, headers=HEADERS)
        inserted = self.t.insert.call_args[0][0]
        # RCASavePayload.normalised() uppercases severity
        assert inserted["severity"] in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "WARNING", "UNKNOWN", "INFO"}

    def test_save_coerces_total_logs_to_int(self):
        payload = {**SAMPLE_PAYLOAD, "total_logs": "2500"}
        self.c.post("/api/rca/save", json=payload, headers=HEADERS)
        inserted = self.t.insert.call_args[0][0]
        assert isinstance(inserted["total_logs"], int)

    def test_save_coerces_error_count_to_int(self):
        payload = {**SAMPLE_PAYLOAD, "error_count": "1310"}
        self.c.post("/api/rca/save", json=payload, headers=HEADERS)
        inserted = self.t.insert.call_args[0][0]
        assert isinstance(inserted["error_count"], int)

    def test_save_default_severity_when_missing(self):
        self.c.post("/api/rca/save", json={"source": "x.log"}, headers=HEADERS)
        inserted = self.t.insert.call_args[0][0]
        # Default severity is "UNKNOWN" after normalisation
        assert inserted["severity"] == "UNKNOWN"

    def test_save_zero_defaults_for_missing_counts(self):
        self.c.post("/api/rca/save", json={"source": "x.log"}, headers=HEADERS)
        inserted = self.t.insert.call_args[0][0]
        assert inserted["total_logs"] == 0
        assert inserted["error_count"] == 0

    def test_save_500_on_supabase_exception(self):
        self.t.execute.side_effect = Exception("DB write failed")
        r = self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD, headers=HEADERS)
        # api_server raises HTTPException(500) on DB error
        assert r.status_code == 500

    def test_save_503_when_supabase_not_configured(self):
        c = _get_client(sb_mock=None)
        r = c.post("/api/rca/save", json=SAMPLE_PAYLOAD, headers=HEADERS)
        assert r.status_code == 503
        assert "Supabase" in r.json()["detail"] or "supabase" in r.json()["detail"].lower()

    def test_save_requires_api_key(self):
        r = self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert r.status_code == 401


# ═════════════════════════════════════════════════════════════════════════════
# GET /api/rca/history
# ═════════════════════════════════════════════════════════════════════════════
class TestRcaHistory:
    def _recs(self, n=3):
        return [
            {
                "id": str(uuid.uuid4()),
                "source": f"s{i}.log",
                "severity": "WARNING",
                "total_logs": 1000 * (i + 1),
                "error_count": 100 * (i + 1),
                "created_at": "2026-03-13T00:00:00+00:00",
            }
            for i in range(n)
        ]

    def setup_method(self):
        self.t = _make_table([])
        self.sb = _make_sb(self.t)
        self.c = _get_client(self.sb)

    def test_history_returns_records_list(self):
        self.t.execute.return_value = MagicMock(data=self._recs(3))
        r = self.c.get("/api/rca/history", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        # api_server returns {"records": [...], "total": N, "supabase": True}
        assert "records" in body
        assert len(body["records"]) == 3
        assert body["total"] == 3
        assert body["supabase"] is True

    def test_history_empty_when_no_records(self):
        r = self.c.get("/api/rca/history", headers=HEADERS)
        body = r.json()
        assert body["records"] == []
        assert body["total"] == 0

    def test_history_severity_filter_uppercased(self):
        self.c.get("/api/rca/history?severity=critical", headers=HEADERS)
        # api_server calls .eq("severity", severity.upper()) → "CRITICAL"
        self.t.eq.assert_called_with("severity", "CRITICAL")

    def test_history_search_uses_or_filter(self):
        self.c.get("/api/rca/history?search=app.log", headers=HEADERS)
        # api_server uses .or_("source.ilike.%app.log%,classification.ilike.%app.log%")
        self.t.or_.assert_called_once()
        call_arg = self.t.or_.call_args[0][0]
        assert "app.log" in call_arg
        assert "ilike" in call_arg

    def test_history_no_severity_filter_when_not_provided(self):
        self.c.get("/api/rca/history", headers=HEADERS)
        # .eq should NOT have been called with "severity"
        eq_calls = [str(c) for c in self.t.eq.call_args_list]
        assert not any("severity" in c for c in eq_calls)

    def test_history_200_with_empty_when_supabase_not_configured(self):
        # api_server returns 200 gracefully (not 503) for history when _sb is None
        c = _get_client(sb_mock=None)
        r = c.get("/api/rca/history", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["records"] == []
        assert body["supabase"] is False

    def test_history_returns_empty_on_db_exception(self):
        self.t.execute.side_effect = Exception("timeout")
        r = self.c.get("/api/rca/history", headers=HEADERS)
        # api_server catches the exception and returns empty gracefully
        assert r.status_code == 200
        assert r.json()["records"] == []

    def test_history_requires_api_key(self):
        r = self.c.get("/api/rca/history")
        assert r.status_code == 401


# ═════════════════════════════════════════════════════════════════════════════
# GET /api/rca/history/{id}
# ═════════════════════════════════════════════════════════════════════════════
class TestRcaGet:
    def setup_method(self):
        self.record = {**SAMPLE_PAYLOAD, "id": FAKE_ID, "created_at": "2026-03-13T07:00:00+00:00"}
        self.t = _make_table(self.record)
        self.sb = _make_sb(self.t)
        self.c = _get_client(self.sb)

    def test_get_record_found(self):
        r = self.c.get(f"/api/rca/history/{FAKE_ID}", headers=HEADERS)
        assert r.status_code == 200
        # api_server returns result.data directly (no ok/data wrapper)
        body = r.json()
        assert body["id"] == FAKE_ID

    def test_get_calls_eq_with_correct_id(self):
        self.c.get(f"/api/rca/history/{FAKE_ID}", headers=HEADERS)
        self.t.eq.assert_called_with("id", FAKE_ID)

    def test_get_500_when_db_raises(self):
        self.t.execute.side_effect = Exception("not found")
        r = self.c.get(f"/api/rca/history/{FAKE_ID}", headers=HEADERS)
        assert r.status_code == 500

    def test_get_503_when_supabase_not_configured(self):
        c = _get_client(sb_mock=None)
        r = c.get(f"/api/rca/history/{FAKE_ID}", headers=HEADERS)
        assert r.status_code == 503
        assert "Supabase" in r.json()["detail"] or "supabase" in r.json()["detail"].lower()

    def test_get_requires_api_key(self):
        r = self.c.get(f"/api/rca/history/{FAKE_ID}")
        assert r.status_code == 401


# ═════════════════════════════════════════════════════════════════════════════
# DELETE /api/rca/history/{id}
# ═════════════════════════════════════════════════════════════════════════════
class TestRcaDelete:
    def setup_method(self):
        self.t = _make_table([])
        self.sb = _make_sb(self.t)
        self.c = _get_client(self.sb)

    def test_delete_happy_path(self):
        r = self.c.delete(f"/api/rca/history/{FAKE_ID}", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_delete_calls_eq_with_id(self):
        self.c.delete(f"/api/rca/history/{FAKE_ID}", headers=HEADERS)
        self.t.eq.assert_called_with("id", FAKE_ID)

    def test_delete_503_when_supabase_not_configured(self):
        c = _get_client(sb_mock=None)
        r = c.delete(f"/api/rca/history/{FAKE_ID}", headers=HEADERS)
        assert r.status_code == 503

    def test_delete_500_on_db_exception(self):
        self.t.execute.side_effect = Exception("row lock")
        r = self.c.delete(f"/api/rca/history/{FAKE_ID}", headers=HEADERS)
        assert r.status_code == 500

    def test_delete_requires_api_key(self):
        r = self.c.delete(f"/api/rca/history/{FAKE_ID}")
        assert r.status_code == 401


# ═════════════════════════════════════════════════════════════════════════════
# End-to-end flow tests
# ═════════════════════════════════════════════════════════════════════════════
class TestEndToEndFlow:
    def setup_method(self):
        self.t = _make_table([])
        self.sb = _make_sb(self.t)
        self.c = _get_client(self.sb)

    def test_full_save_then_history_then_delete(self):
        nid = str(uuid.uuid4())

        # 1. Save
        self.t.execute.return_value = MagicMock(data=[{"id": nid}])
        r = self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD, headers=HEADERS)
        assert r.json()["ok"] is True
        assert r.json()["id"] == nid

        # 2. History shows the record
        saved_record = {**SAMPLE_PAYLOAD, "id": nid, "created_at": "2026-03-13T00:00:00"}
        self.t.execute.return_value = MagicMock(data=[saved_record])
        r = self.c.get("/api/rca/history", headers=HEADERS)
        assert len(r.json()["records"]) == 1
        assert r.json()["records"][0]["id"] == nid

        # 3. Delete
        self.t.execute.return_value = MagicMock(data=[])
        r = self.c.delete(f"/api/rca/history/{nid}", headers=HEADERS)
        assert r.json()["ok"] is True

        # 4. History is empty again
        self.t.execute.return_value = MagicMock(data=[])
        r = self.c.get("/api/rca/history", headers=HEADERS)
        assert r.json()["records"] == []

    def test_resave_after_delete_succeeds(self):
        nid2 = str(uuid.uuid4())
        self.t.execute.return_value = MagicMock(data=[{"id": nid2}])
        r = self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD, headers=HEADERS)
        assert r.json()["ok"] is True
        assert r.json()["id"] == nid2

    def test_search_returns_matching_record(self):
        record = {**SAMPLE_PAYLOAD, "id": FAKE_ID, "created_at": "2026-03-13T00:00:00"}
        self.t.execute.return_value = MagicMock(data=[record])
        r = self.c.get("/api/rca/history?search=app.log", headers=HEADERS)
        assert r.status_code == 200
        records = r.json()["records"]
        assert any(rec["source"] == "app.log" for rec in records)

    def test_severity_filter_is_sent_uppercased_to_db(self):
        self.c.get("/api/rca/history?severity=warning", headers=HEADERS)
        self.t.eq.assert_called_with("severity", "WARNING")
