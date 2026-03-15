"""
tests/test_rca_endpoints.py
============================
Tests for RCA endpoints in api_server.py.

Response shapes (api_server.py):
  POST /api/rca/save       → {"ok": True, "id": "...", "record": {...}}
  GET  /api/rca/history    → {"ok": True, "data": [...], "count": N, "supabase": bool}
  GET  /api/rca/history/id → {"ok": True, "data": {...}}
  DEL  /api/rca/history/id → {"ok": True, "deleted_id": "..."}

Mock strategy:
  Patch supabase.create_client BEFORE reload so _sb = sb_mock from the start.
  Also force AUTORCA_API_KEY at module level after reload.
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
HEADERS = {"X-API-Key": "test-api-key"}

SAMPLE_PAYLOAD = {
    "source": "app.log",
    "classification": "Database",
    "severity": "critical",
    "summary": "High error rate detected.",
    "total_logs": 2500,
    "error_count": 1310,
    "meta": {"rate": 52.4},
}


def _make_table(execute_data=None):
    t = MagicMock()
    for m in ("select", "insert", "delete", "update", "eq", "or_", "ilike", "order", "range", "single", "desc"):
        getattr(t, m).return_value = t
    t.execute.return_value = MagicMock(data=execute_data if execute_data is not None else [])
    return t


def _make_sb(table):
    s = MagicMock()
    s.table.return_value = table
    return s


def _get_client(sb_mock=None):
    from fastapi.testclient import TestClient

    env = {
        "SUPABASE_URL": "https://test.supabase.co" if sb_mock else "",
        "SUPABASE_KEY": "test-key" if sb_mock else "",
        "AUTORCA_API_KEY": "test-api-key",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch("supabase.create_client", return_value=sb_mock):
            import api_server

            importlib.reload(api_server)

    import api_server as _api

    _api.AUTORCA_API_KEY = "test-api-key"
    if sb_mock is None:
        _api._sb = None
    return TestClient(_api.app, raise_server_exceptions=False)


# =============================================================================
# POST /api/rca/save
# =============================================================================


class TestRcaSave:
    def setup_method(self):
        self.t = _make_table([{"id": FAKE_ID}])
        self.c = _get_client(_make_sb(self.t))

    def test_save_happy_path(self):
        r = self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD, headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["id"] == FAKE_ID

    def test_save_response_contains_record(self):
        r = self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD, headers=HEADERS)
        assert "record" in r.json()

    def test_save_inserts_correct_source(self):
        self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD, headers=HEADERS)
        assert self.t.insert.call_args[0][0]["source"] == "app.log"

    def test_save_inserts_correct_classification(self):
        self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD, headers=HEADERS)
        assert self.t.insert.call_args[0][0]["classification"] == "Database"

    def test_save_severity_in_allowed_set(self):
        self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD, headers=HEADERS)
        assert self.t.insert.call_args[0][0]["severity"] in {
            "CRITICAL",
            "HIGH",
            "MEDIUM",
            "LOW",
            "WARNING",
            "UNKNOWN",
            "INFO",
        }

    def test_save_error_maps_to_high(self):
        self.c.post("/api/rca/save", json={**SAMPLE_PAYLOAD, "severity": "error"}, headers=HEADERS)
        assert self.t.insert.call_args[0][0]["severity"] == "HIGH"

    def test_save_warn_maps_to_warning(self):
        self.c.post("/api/rca/save", json={**SAMPLE_PAYLOAD, "severity": "warn"}, headers=HEADERS)
        assert self.t.insert.call_args[0][0]["severity"] == "WARNING"

    def test_save_unknown_severity_maps_to_unknown(self):
        self.c.post("/api/rca/save", json={**SAMPLE_PAYLOAD, "severity": "banana"}, headers=HEADERS)
        assert self.t.insert.call_args[0][0]["severity"] == "UNKNOWN"

    def test_save_default_severity_when_missing(self):
        self.c.post("/api/rca/save", json={"source": "x.log"}, headers=HEADERS)
        assert self.t.insert.call_args[0][0]["severity"] == "UNKNOWN"

    def test_save_zero_defaults_for_missing_counts(self):
        self.c.post("/api/rca/save", json={"source": "x.log"}, headers=HEADERS)
        row = self.t.insert.call_args[0][0]
        assert row["total_logs"] == 0
        assert row["error_count"] == 0

    def test_save_500_on_db_exception(self):
        self.t.execute.side_effect = Exception("DB write failed")
        r = self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD, headers=HEADERS)
        assert r.status_code == 500

    def test_save_503_when_sb_none(self):
        r = _get_client(sb_mock=None).post("/api/rca/save", json=SAMPLE_PAYLOAD, headers=HEADERS)
        assert r.status_code == 503
        assert "supabase" in r.json()["detail"].lower()

    def test_save_401_without_api_key(self):
        r = self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert r.status_code == 401


# =============================================================================
# GET /api/rca/history
# =============================================================================


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
        self.c = _get_client(_make_sb(self.t))

    def test_history_ok_true(self):
        r = self.c.get("/api/rca/history", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_history_has_data_key(self):
        self.t.execute.return_value = MagicMock(data=self._recs(3))
        r = self.c.get("/api/rca/history", headers=HEADERS)
        assert "data" in r.json()

    def test_history_correct_count(self):
        self.t.execute.return_value = MagicMock(data=self._recs(3))
        r = self.c.get("/api/rca/history", headers=HEADERS)
        assert r.json()["count"] == 3
        assert len(r.json()["data"]) == 3

    def test_history_supabase_true(self):
        r = self.c.get("/api/rca/history", headers=HEADERS)
        assert r.json()["supabase"] is True

    def test_history_empty_when_no_records(self):
        r = self.c.get("/api/rca/history", headers=HEADERS)
        assert r.json()["data"] == []
        assert r.json()["count"] == 0

    def test_severity_filter_uppercased(self):
        self.c.get("/api/rca/history?severity=critical", headers=HEADERS)
        self.t.eq.assert_called_with("severity", "CRITICAL")

    def test_severity_all_skips_eq(self):
        self.c.get("/api/rca/history?severity=all", headers=HEADERS)
        eq_calls = [str(c) for c in self.t.eq.call_args_list]
        assert not any("severity" in c for c in eq_calls)

    def test_search_uses_or_filter(self):
        self.c.get("/api/rca/history?search=app.log", headers=HEADERS)
        self.t.or_.assert_called_once()
        arg = self.t.or_.call_args[0][0]
        assert "app.log" in arg
        assert "ilike" in arg

    def test_search_covers_source_and_classification(self):
        self.c.get("/api/rca/history?search=db", headers=HEADERS)
        arg = self.t.or_.call_args[0][0]
        assert "source" in arg
        assert "classification" in arg

    def test_no_eq_when_no_severity(self):
        self.c.get("/api/rca/history", headers=HEADERS)
        eq_calls = [str(c) for c in self.t.eq.call_args_list]
        assert not any("severity" in c for c in eq_calls)

    def test_200_empty_when_sb_none(self):
        r = _get_client(sb_mock=None).get("/api/rca/history", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["data"] == []
        assert r.json()["supabase"] is False

    def test_200_empty_on_db_exception(self):
        self.t.execute.side_effect = Exception("timeout")
        r = self.c.get("/api/rca/history", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["data"] == []

    def test_401_without_api_key(self):
        r = self.c.get("/api/rca/history")
        assert r.status_code == 401


# =============================================================================
# GET /api/rca/history/{id}
# =============================================================================


class TestRcaGet:
    def setup_method(self):
        self.record = {**SAMPLE_PAYLOAD, "id": FAKE_ID, "created_at": "2026-03-13T07:00:00+00:00"}
        self.t = _make_table(self.record)
        self.c = _get_client(_make_sb(self.t))

    def test_get_200(self):
        r = self.c.get(f"/api/rca/history/{FAKE_ID}", headers=HEADERS)
        assert r.status_code == 200

    def test_get_ok_and_data_wrapper(self):
        r = self.c.get(f"/api/rca/history/{FAKE_ID}", headers=HEADERS)
        assert r.json()["ok"] is True
        assert r.json()["data"]["id"] == FAKE_ID

    def test_get_calls_eq_with_id(self):
        self.c.get(f"/api/rca/history/{FAKE_ID}", headers=HEADERS)
        self.t.eq.assert_called_with("id", FAKE_ID)

    def test_get_calls_single(self):
        self.c.get(f"/api/rca/history/{FAKE_ID}", headers=HEADERS)
        self.t.single.assert_called_once()

    def test_get_500_on_exception(self):
        self.t.execute.side_effect = Exception("not found")
        r = self.c.get(f"/api/rca/history/{FAKE_ID}", headers=HEADERS)
        assert r.status_code == 500

    def test_get_503_when_sb_none(self):
        r = _get_client(sb_mock=None).get(f"/api/rca/history/{FAKE_ID}", headers=HEADERS)
        assert r.status_code == 503

    def test_get_401_without_api_key(self):
        r = self.c.get(f"/api/rca/history/{FAKE_ID}")
        assert r.status_code == 401


# =============================================================================
# DELETE /api/rca/history/{id}
# =============================================================================


class TestRcaDelete:
    def setup_method(self):
        self.t = _make_table([])
        self.c = _get_client(_make_sb(self.t))

    def test_delete_happy_path(self):
        r = self.c.delete(f"/api/rca/history/{FAKE_ID}", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_delete_contains_deleted_id(self):
        r = self.c.delete(f"/api/rca/history/{FAKE_ID}", headers=HEADERS)
        assert r.json()["deleted_id"] == FAKE_ID

    def test_delete_calls_eq(self):
        self.c.delete(f"/api/rca/history/{FAKE_ID}", headers=HEADERS)
        self.t.eq.assert_called_with("id", FAKE_ID)

    def test_delete_503_when_sb_none(self):
        r = _get_client(sb_mock=None).delete(f"/api/rca/history/{FAKE_ID}", headers=HEADERS)
        assert r.status_code == 503

    def test_delete_500_on_exception(self):
        self.t.execute.side_effect = Exception("row lock")
        r = self.c.delete(f"/api/rca/history/{FAKE_ID}", headers=HEADERS)
        assert r.status_code == 500

    def test_delete_401_without_api_key(self):
        r = self.c.delete(f"/api/rca/history/{FAKE_ID}")
        assert r.status_code == 401


# =============================================================================
# End-to-end flow
# =============================================================================


class TestEndToEndFlow:
    def setup_method(self):
        self.t = _make_table([])
        self.c = _get_client(_make_sb(self.t))

    def test_save_list_delete_cycle(self):
        nid = str(uuid.uuid4())
        self.t.execute.return_value = MagicMock(data=[{"id": nid}])
        assert self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD, headers=HEADERS).json()["id"] == nid

        saved = {**SAMPLE_PAYLOAD, "id": nid, "created_at": "2026-03-13T00:00:00"}
        self.t.execute.return_value = MagicMock(data=[saved])
        assert len(self.c.get("/api/rca/history", headers=HEADERS).json()["data"]) == 1

        self.t.execute.return_value = MagicMock(data=[])
        assert self.c.delete(f"/api/rca/history/{nid}", headers=HEADERS).json()["ok"] is True

        self.t.execute.return_value = MagicMock(data=[])
        assert self.c.get("/api/rca/history", headers=HEADERS).json()["data"] == []

    def test_search_returns_matching(self):
        record = {**SAMPLE_PAYLOAD, "id": FAKE_ID, "created_at": "2026-03-13T00:00:00"}
        self.t.execute.return_value = MagicMock(data=[record])
        r = self.c.get("/api/rca/history?search=app.log", headers=HEADERS)
        assert any(rec["source"] == "app.log" for rec in r.json()["data"])

    def test_severity_filter_uppercase(self):
        self.c.get("/api/rca/history?severity=warning", headers=HEADERS)
        self.t.eq.assert_called_with("severity", "WARNING")
