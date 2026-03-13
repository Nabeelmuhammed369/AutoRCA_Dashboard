"""
tests/test_rca_endpoints.py  (v2 — fixed)
==========================================
Root cause of v1 failures:
  "module 'api_server' has no attribute 'sb'"

  api_server.sb is assigned at MODULE IMPORT TIME via:
      sb = create_client(SUPABASE_URL, SUPABASE_KEY)

  v1 tried to set `api_server.sb` after import — but if the import itself
  failed or sb was never defined as a module-level name, AttributeError fires.

Fix: patch `supabase.create_client` BEFORE importing api_server (or before
reloading it), so the module-level assignment picks up our mock.
"""

import sys
import os
import uuid
import importlib
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAKE_ID   = str(uuid.uuid4())
BASE_ENV  = {
    "SUPABASE_URL":    "https://test.supabase.co",
    "SUPABASE_KEY":    "test-key",
    "AUTORCA_API_KEY": "test-api-key",
}
SAMPLE_PAYLOAD = {
    "source_name": "app.log", "severity": "critical",
    "total_entries": 2500,    "error_count": 1310,
    "warn_count": 634,        "error_rate": 52.4,
    "ai_summary": "High error rate.", "fix_steps": "1. Restart",
    "incident_groups": [],    "affected_services": ["auth"],
    "remediation": [],        "stats": {"total":2500,"err":1310,"warn":634,"rate":52.4},
}

# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_table(execute_data=None):
    """Fluent supabase-py table mock — every chain method returns self."""
    t = MagicMock()
    for m in ("select","insert","delete","update","eq","ilike","order","range","single"):
        getattr(t, m).return_value = t
    t.execute.return_value = MagicMock(data=execute_data if execute_data is not None else [])
    return t

def _sb(table):
    s = MagicMock()
    s.table.return_value = table
    return s

def _client_with(table):
    """Reload api_server with a mocked create_client and return TestClient."""
    from fastapi.testclient import TestClient
    with patch.dict(os.environ, BASE_ENV, clear=False):
        with patch("supabase.create_client", return_value=_sb(table)):
            import api_server
            importlib.reload(api_server)
            return TestClient(api_server.app, raise_server_exceptions=False), table

def _client_no_sb():
    """Reload api_server with Supabase vars empty so sb=None."""
    from fastapi.testclient import TestClient
    env = {**BASE_ENV, "SUPABASE_URL": "", "SUPABASE_KEY": ""}
    with patch.dict(os.environ, env, clear=False):
        with patch("supabase.create_client", side_effect=Exception("no creds")):
            import api_server
            importlib.reload(api_server)
            api_server.sb = None   # guarantee None regardless of exception handling
            return TestClient(api_server.app, raise_server_exceptions=False)

# ═════════════════════════════════════════════════════════════════════════════
# POST /api/rca/save
# ═════════════════════════════════════════════════════════════════════════════
class TestRcaSave:
    def setup_method(self):
        self.t = _make_table([{"id": FAKE_ID}])
        self.c, _ = _client_with(self.t)

    def test_save_happy_path(self):
        r = self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["id"] == FAKE_ID

    def test_save_inserts_correct_source_name(self):
        self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert self.t.insert.call_args[0][0]["source_name"] == "app.log"

    def test_save_inserts_correct_severity(self):
        self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert self.t.insert.call_args[0][0]["severity"] == "critical"

    def test_save_coerces_total_entries_to_int(self):
        self.c.post("/api/rca/save", json={**SAMPLE_PAYLOAD, "total_entries": "2500"})
        assert isinstance(self.t.insert.call_args[0][0]["total_entries"], int)

    def test_save_coerces_error_count_to_int(self):
        self.c.post("/api/rca/save", json={**SAMPLE_PAYLOAD, "error_count": "1310"})
        assert isinstance(self.t.insert.call_args[0][0]["error_count"], int)

    def test_save_default_severity_when_missing(self):
        self.c.post("/api/rca/save", json={"source_name": "x.log"})
        assert self.t.insert.call_args[0][0]["severity"] == "warning"

    def test_save_zero_defaults_for_missing_counts(self):
        self.c.post("/api/rca/save", json={"source_name": "x.log"})
        args = self.t.insert.call_args[0][0]
        assert args["total_entries"] == 0
        assert args["error_count"] == 0

    def test_save_ok_false_on_supabase_exception(self):
        self.t.execute.side_effect = Exception("DB write failed")
        r = self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert r.status_code == 200
        assert r.json()["ok"] is False

    def test_save_503_when_supabase_not_configured(self):
        r = _client_no_sb().post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert r.status_code == 503
        assert "Supabase" in r.json()["detail"]

# ═════════════════════════════════════════════════════════════════════════════
# GET /api/rca/history
# ═════════════════════════════════════════════════════════════════════════════
class TestRcaHistory:
    def _recs(self, n=3):
        return [{"id": str(uuid.uuid4()), "source_name": f"s{i}.log",
                 "severity": "warning", "total_entries": 1000*(i+1),
                 "error_count": 100*(i+1), "created_at": "2026-03-13T00:00:00+00:00"}
                for i in range(n)]

    def setup_method(self):
        self.t = _make_table([])
        self.c, _ = _client_with(self.t)

    def test_history_returns_list(self):
        self.t.execute.return_value = MagicMock(data=self._recs(3))
        r = self.c.get("/api/rca/history")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert len(r.json()["data"]) == 3
        assert r.json()["count"] == 3

    def test_history_empty_when_no_records(self):
        r = self.c.get("/api/rca/history")
        assert r.json()["data"] == []

    def test_history_severity_filter_calls_eq(self):
        self.c.get("/api/rca/history?severity=critical")
        self.t.eq.assert_called_with("severity", "critical")

    def test_history_search_calls_ilike(self):
        self.c.get("/api/rca/history?search=app.log")
        self.t.ilike.assert_called_with("source_name", "%app.log%")

    def test_history_severity_all_skips_eq_filter(self):
        self.c.get("/api/rca/history?severity=all")
        eq_calls = [str(c) for c in self.t.eq.call_args_list]
        assert not any("severity" in c for c in eq_calls)

    def test_history_503_when_supabase_not_configured(self):
        assert _client_no_sb().get("/api/rca/history").status_code == 503

    def test_history_ok_false_on_exception(self):
        self.t.execute.side_effect = Exception("timeout")
        r = self.c.get("/api/rca/history")
        assert r.json()["ok"] is False

# ═════════════════════════════════════════════════════════════════════════════
# GET /api/rca/history/{id}
# ═════════════════════════════════════════════════════════════════════════════
class TestRcaGet:
    def setup_method(self):
        record = {**SAMPLE_PAYLOAD, "id": FAKE_ID, "created_at": "2026-03-13T07:00:00+00:00"}
        self.t = _make_table(record)
        self.c, _ = _client_with(self.t)

    def test_get_record_found(self):
        r = self.c.get(f"/api/rca/history/{FAKE_ID}")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["data"]["id"] == FAKE_ID

    def test_get_calls_eq_with_correct_id(self):
        self.c.get(f"/api/rca/history/{FAKE_ID}")
        self.t.eq.assert_called_with("id", FAKE_ID)

    def test_get_ok_false_when_not_found(self):
        self.t.execute.side_effect = Exception("not found")
        r = self.c.get(f"/api/rca/history/{FAKE_ID}")
        assert r.json()["ok"] is False

    def test_get_503_when_supabase_not_configured(self):
        assert _client_no_sb().get(f"/api/rca/history/{FAKE_ID}").status_code == 503

# ═════════════════════════════════════════════════════════════════════════════
# DELETE /api/rca/history/{id}
# ═════════════════════════════════════════════════════════════════════════════
class TestRcaDelete:
    def setup_method(self):
        self.t = _make_table([])
        self.c, _ = _client_with(self.t)

    def test_delete_happy_path(self):
        r = self.c.delete(f"/api/rca/history/{FAKE_ID}")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_delete_calls_eq_with_id(self):
        self.c.delete(f"/api/rca/history/{FAKE_ID}")
        self.t.eq.assert_called_with("id", FAKE_ID)

    def test_delete_503_when_supabase_not_configured(self):
        assert _client_no_sb().delete(f"/api/rca/history/{FAKE_ID}").status_code == 503

    def test_delete_ok_false_on_exception(self):
        self.t.execute.side_effect = Exception("row lock")
        r = self.c.delete(f"/api/rca/history/{FAKE_ID}")
        assert r.json()["ok"] is False

# ═════════════════════════════════════════════════════════════════════════════
# Duplicate detection flow
# ═════════════════════════════════════════════════════════════════════════════
class TestDuplicateDetectionFlow:
    def setup_method(self):
        self.t = _make_table([])
        self.c, _ = _client_with(self.t)

    def test_duplicate_present_returned_by_search(self):
        existing = {"id": FAKE_ID, "source_name": "app.log",
                    "total_entries": 2500, "error_count": 1310, "severity": "critical",
                    "created_at": "2026-03-13T07:00:00+00:00"}
        self.t.execute.return_value = MagicMock(data=[existing])
        r = self.c.get("/api/rca/history?search=app.log&limit=50")
        assert r.status_code == 200
        match = next((x for x in r.json()["data"]
                      if x["source_name"] == "app.log"
                      and x["total_entries"] == 2500
                      and x["error_count"] == 1310), None)
        assert match is not None

    def test_no_duplicate_after_deletion(self):
        r = self.c.get("/api/rca/history?search=app.log&limit=50")
        assert r.json()["data"] == []

    def test_full_flow_save_delete_resave(self):
        # Save
        nid = str(uuid.uuid4())
        self.t.execute.return_value = MagicMock(data=[{"id": nid}])
        assert self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD).json()["ok"] is True
        # Delete
        self.t.execute.return_value = MagicMock(data=[])
        assert self.c.delete(f"/api/rca/history/{nid}").json()["ok"] is True
        # Check — empty
        assert self.c.get("/api/rca/history?search=app.log&limit=50").json()["data"] == []
        # Re-save — must succeed
        nid2 = str(uuid.uuid4())
        self.t.execute.return_value = MagicMock(data=[{"id": nid2}])
        r = self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert r.json()["ok"] is True
        assert r.json()["id"] == nid2

    def test_save_anyway_bypasses_check(self):
        nid = str(uuid.uuid4())
        self.t.execute.return_value = MagicMock(data=[{"id": nid}])
        r = self.c.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert r.json()["ok"] is True

    def test_duplicate_check_uses_ilike(self):
        self.c.get("/api/rca/history?search=APP.LOG&limit=50")
        self.t.ilike.assert_called_with("source_name", "%APP.LOG%")