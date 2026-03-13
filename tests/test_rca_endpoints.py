"""
tests/test_rca_endpoints.py
============================
Pytest suite for the 4 RCA history endpoints in api_server.py.

Scenarios covered
-----------------
1.  POST /api/rca/save  — happy path (returns ok + id)
2.  POST /api/rca/save  — Supabase not configured (503)
3.  POST /api/rca/save  — Supabase insert raises exception (returns ok:false)
4.  GET  /api/rca/history — returns list (no filters)
5.  GET  /api/rca/history — severity filter forwarded to Supabase
6.  GET  /api/rca/history — search filter forwarded to Supabase
7.  GET  /api/rca/history — Supabase not configured (503)
8.  GET  /api/rca/history/{id} — record found
9.  GET  /api/rca/history/{id} — record not found (ok:false)
10. GET  /api/rca/history/{id} — Supabase not configured (503)
11. DELETE /api/rca/history/{id} — happy path
12. DELETE /api/rca/history/{id} — Supabase not configured (503)
13. DELETE /api/rca/history/{id} — delete raises exception (returns ok:false)
14. Duplicate detection flow  — save → delete → re-save (no duplicate block)
15. Duplicate detection flow  — save → query shows duplicate present
"""

import os
import sys
import uuid
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup — allow importing api_server from the project root
# ---------------------------------------------------------------------------
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_sb_mock():
    """Return a MagicMock that mimics the supabase-py fluent query API."""
    sb = MagicMock()

    # .table(...) -> self (fluent)
    table_mock = MagicMock()
    sb.table.return_value = table_mock

    # chain methods all return self so we can stack them
    for method in ("select", "insert", "delete", "update", "eq", "ilike", "order", "range", "single"):
        getattr(table_mock, method).return_value = table_mock

    return sb, table_mock


@pytest.fixture()
def sb_mock_and_table():
    """Yields (sb_mock, table_mock) with a preconfigured default execute()."""
    sb, table = _make_sb_mock()
    # Default execute() — overridden per test where needed
    table.execute.return_value = MagicMock(data=[])
    return sb, table


@pytest.fixture()
def client_with_sb(sb_mock_and_table):
    """TestClient with a live Supabase mock injected into api_server.sb."""
    sb_mock, _ = sb_mock_and_table
    # Patch before importing so module-level `sb` is replaced
    with patch.dict(
        os.environ,
        {
            "SUPABASE_URL": "https://test.supabase.co",
            "SUPABASE_KEY": "test-key",
            "AUTORCA_API_KEY": "test-api-key",
        },
    ):
        import api_server

        original_sb = api_server.sb
        api_server.sb = sb_mock
        from fastapi.testclient import TestClient as TC

        with TC(api_server.app) as c:
            yield c, sb_mock, _
        api_server.sb = original_sb


@pytest.fixture()
def client_no_sb():
    """TestClient with sb=None (Supabase not configured)."""
    with patch.dict(os.environ, {"AUTORCA_API_KEY": "test-api-key"}):
        import api_server

        original_sb = api_server.sb
        api_server.sb = None
        from fastapi.testclient import TestClient as TC

        with TC(api_server.app) as c:
            yield c
        api_server.sb = original_sb


# ---------------------------------------------------------------------------
# Sample payload used across multiple tests
# ---------------------------------------------------------------------------
SAMPLE_PAYLOAD = {
    "source_name": "app.log",
    "severity": "critical",
    "total_entries": 2500,
    "error_count": 1310,
    "warn_count": 634,
    "error_rate": 52.4,
    "ai_summary": "High error rate detected in authentication service.",
    "fix_steps": "1. Restart auth pod\n2. Check DB connections",
    "incident_groups": [{"name": "DB_CONN_FAIL", "count": 450}],
    "affected_services": ["auth-service", "api-gateway"],
    "remediation": ["Restart auth pod", "Scale DB replicas"],
    "stats": {"total": 2500, "err": 1310, "warn": 634, "rate": 52.4},
}

FAKE_ID = str(uuid.uuid4())


# =============================================================================
# 1. POST /api/rca/save — happy path
# =============================================================================
class TestRcaSave:
    def test_save_happy_path(self, client_with_sb):
        client, sb_mock, table_mock = client_with_sb
        table_mock.execute.return_value = MagicMock(data=[{"id": FAKE_ID}])

        resp = client.post("/api/rca/save", json=SAMPLE_PAYLOAD)

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["id"] == FAKE_ID
        # Verify insert was called with correct field values
        call_args = table_mock.insert.call_args[0][0]
        assert call_args["source_name"] == "app.log"
        assert call_args["severity"] == "critical"
        assert call_args["total_entries"] == 2500
        assert call_args["error_count"] == 1310

    def test_save_returns_ok_false_on_exception(self, client_with_sb):
        client, sb_mock, table_mock = client_with_sb
        table_mock.execute.side_effect = Exception("Supabase write failed")

        resp = client.post("/api/rca/save", json=SAMPLE_PAYLOAD)

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "error" in body

    def test_save_503_when_supabase_not_configured(self, client_no_sb):
        resp = client_no_sb.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert resp.status_code == 503
        assert "Supabase not configured" in resp.json()["detail"]

    def test_save_coerces_numeric_fields(self, client_with_sb):
        """total_entries/error_count must be stored as int even if sent as string."""
        client, sb_mock, table_mock = client_with_sb
        table_mock.execute.return_value = MagicMock(data=[{"id": FAKE_ID}])

        payload = {**SAMPLE_PAYLOAD, "total_entries": "2500", "error_count": "1310"}
        resp = client.post("/api/rca/save", json=payload)

        assert resp.status_code == 200
        call_args = table_mock.insert.call_args[0][0]
        assert isinstance(call_args["total_entries"], int)
        assert isinstance(call_args["error_count"], int)

    def test_save_uses_defaults_for_missing_fields(self, client_with_sb):
        client, sb_mock, table_mock = client_with_sb
        table_mock.execute.return_value = MagicMock(data=[{"id": FAKE_ID}])

        resp = client.post("/api/rca/save", json={"source_name": "minimal.log"})

        assert resp.status_code == 200
        call_args = table_mock.insert.call_args[0][0]
        assert call_args["severity"] == "warning"  # default
        assert call_args["total_entries"] == 0  # default
        assert call_args["error_rate"] == 0.0  # default


# =============================================================================
# 2. GET /api/rca/history
# =============================================================================
class TestRcaHistory:
    def _sample_records(self, n=3):
        return [
            {
                "id": str(uuid.uuid4()),
                "source_name": f"source_{i}.log",
                "severity": "warning",
                "total_entries": 1000 * (i + 1),
                "error_count": 100 * (i + 1),
                "created_at": f"2026-03-13T0{i}:00:00+00:00",
            }
            for i in range(n)
        ]

    def test_history_returns_list(self, client_with_sb):
        client, sb_mock, table_mock = client_with_sb
        records = self._sample_records(3)
        table_mock.execute.return_value = MagicMock(data=records)

        resp = client.get("/api/rca/history")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert len(body["data"]) == 3
        assert body["count"] == 3

    def test_history_empty_when_no_records(self, client_with_sb):
        client, sb_mock, table_mock = client_with_sb
        table_mock.execute.return_value = MagicMock(data=[])

        resp = client.get("/api/rca/history")

        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_history_severity_filter_forwarded(self, client_with_sb):
        client, sb_mock, table_mock = client_with_sb
        table_mock.execute.return_value = MagicMock(data=[])

        client.get("/api/rca/history?severity=critical")

        # eq("severity", "critical") must have been called
        table_mock.eq.assert_called_with("severity", "critical")

    def test_history_search_filter_forwarded(self, client_with_sb):
        client, sb_mock, table_mock = client_with_sb
        table_mock.execute.return_value = MagicMock(data=[])

        client.get("/api/rca/history?search=app.log")

        table_mock.ilike.assert_called_with("source_name", "%app.log%")

    def test_history_all_severity_does_not_filter(self, client_with_sb):
        """severity=all should NOT add an eq filter."""
        client, sb_mock, table_mock = client_with_sb
        table_mock.execute.return_value = MagicMock(data=[])

        client.get("/api/rca/history?severity=all")

        eq_calls = [str(c) for c in table_mock.eq.call_args_list]
        assert not any("severity" in c for c in eq_calls)

    def test_history_503_when_supabase_not_configured(self, client_no_sb):
        resp = client_no_sb.get("/api/rca/history")
        assert resp.status_code == 503

    def test_history_returns_ok_false_on_exception(self, client_with_sb):
        client, sb_mock, table_mock = client_with_sb
        table_mock.execute.side_effect = Exception("Connection timeout")

        resp = client.get("/api/rca/history")

        assert resp.status_code == 200
        assert resp.json()["ok"] is False


# =============================================================================
# 3. GET /api/rca/history/{id}
# =============================================================================
class TestRcaGet:
    def test_get_record_found(self, client_with_sb):
        client, sb_mock, table_mock = client_with_sb
        record = {**SAMPLE_PAYLOAD, "id": FAKE_ID, "created_at": "2026-03-13T07:00:00+00:00"}
        table_mock.execute.return_value = MagicMock(data=record)

        resp = client.get(f"/api/rca/history/{FAKE_ID}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["data"]["id"] == FAKE_ID
        table_mock.eq.assert_called_with("id", FAKE_ID)

    def test_get_record_not_found_returns_ok_false(self, client_with_sb):
        client, sb_mock, table_mock = client_with_sb
        table_mock.execute.side_effect = Exception("Record not found")

        resp = client.get(f"/api/rca/history/{FAKE_ID}")

        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_get_503_when_supabase_not_configured(self, client_no_sb):
        resp = client_no_sb.get(f"/api/rca/history/{FAKE_ID}")
        assert resp.status_code == 503


# =============================================================================
# 4. DELETE /api/rca/history/{id}
# =============================================================================
class TestRcaDelete:
    def test_delete_happy_path(self, client_with_sb):
        client, sb_mock, table_mock = client_with_sb
        table_mock.execute.return_value = MagicMock(data=[])

        resp = client.delete(f"/api/rca/history/{FAKE_ID}")

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        table_mock.eq.assert_called_with("id", FAKE_ID)

    def test_delete_503_when_supabase_not_configured(self, client_no_sb):
        resp = client_no_sb.delete(f"/api/rca/history/{FAKE_ID}")
        assert resp.status_code == 503

    def test_delete_returns_ok_false_on_exception(self, client_with_sb):
        client, sb_mock, table_mock = client_with_sb
        table_mock.execute.side_effect = Exception("Row lock timeout")

        resp = client.delete(f"/api/rca/history/{FAKE_ID}")

        assert resp.status_code == 200
        assert resp.json()["ok"] is False


# =============================================================================
# 5. Duplicate detection flow
#    The frontend calls GET /api/rca/history?search=<source> BEFORE saving to
#    check for duplicates. These tests verify the backend correctly supports
#    that pattern (returning matching records when they exist, empty when not).
# =============================================================================
class TestDuplicateDetectionFlow:
    def test_duplicate_present_in_db(self, client_with_sb):
        """
        Simulate: user already saved a record with the same source/stats.
        GET /api/rca/history?search=app.log should return that record so the
        frontend can detect the duplicate and show the warning modal.
        """
        client, sb_mock, table_mock = client_with_sb
        existing = {
            "id": FAKE_ID,
            "source_name": "app.log",
            "total_entries": 2500,
            "error_count": 1310,
            "severity": "critical",
            "created_at": "2026-03-13T07:00:00+00:00",
        }
        table_mock.execute.return_value = MagicMock(data=[existing])

        resp = client.get("/api/rca/history?search=app.log&limit=50")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        # Frontend will find this record and compare total_entries + error_count
        match = next(
            (
                r
                for r in body["data"]
                if r["source_name"] == "app.log" and r["total_entries"] == 2500 and r["error_count"] == 1310
            ),
            None,
        )
        assert match is not None, "Duplicate record should be found in response"

    def test_no_duplicate_after_deletion(self, client_with_sb):
        """
        Simulate: user deleted the record from DB.
        GET /api/rca/history?search=app.log should return empty → frontend
        proceeds with save without showing the duplicate modal.
        """
        client, sb_mock, table_mock = client_with_sb
        table_mock.execute.return_value = MagicMock(data=[])

        resp = client.get("/api/rca/history?search=app.log&limit=50")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["data"] == [], "No records should be returned after deletion"

    def test_full_flow_save_delete_resave(self, client_with_sb):
        """
        End-to-end simulation of the scenario described in the bug report:
          1. Save record  → ok: True
          2. Delete record → ok: True
          3. Check for duplicate → empty (no records)
          4. Re-save → ok: True  (no duplicate block)
        """
        client, sb_mock, table_mock = client_with_sb

        # Step 1: Save
        table_mock.execute.return_value = MagicMock(data=[{"id": FAKE_ID}])
        r1 = client.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert r1.json()["ok"] is True

        # Step 2: Delete
        table_mock.execute.return_value = MagicMock(data=[])
        r2 = client.delete(f"/api/rca/history/{FAKE_ID}")
        assert r2.json()["ok"] is True

        # Step 3: Frontend duplicate check — DB is now empty
        table_mock.execute.return_value = MagicMock(data=[])
        r3 = client.get("/api/rca/history?search=app.log&limit=50")
        assert r3.json()["data"] == []

        # Step 4: Re-save proceeds without issue
        new_id = str(uuid.uuid4())
        table_mock.execute.return_value = MagicMock(data=[{"id": new_id}])
        r4 = client.post("/api/rca/save", json=SAMPLE_PAYLOAD)
        assert r4.json()["ok"] is True
        assert r4.json()["id"] == new_id

    def test_save_anyway_after_duplicate_detected(self, client_with_sb):
        """
        When user clicks 'Save Anyway' the frontend skips the check and calls
        POST /api/rca/save directly. Verify the endpoint allows this.
        """
        client, sb_mock, table_mock = client_with_sb
        new_id = str(uuid.uuid4())
        table_mock.execute.return_value = MagicMock(data=[{"id": new_id}])

        # Direct save (frontend bypasses check)
        resp = client.post("/api/rca/save", json=SAMPLE_PAYLOAD)

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["id"] == new_id

    def test_duplicate_check_is_case_insensitive(self, client_with_sb):
        """ilike search must match regardless of case."""
        client, sb_mock, table_mock = client_with_sb
        table_mock.execute.return_value = MagicMock(data=[])

        client.get("/api/rca/history?search=APP.LOG&limit=50")

        table_mock.ilike.assert_called_with("source_name", "%APP.LOG%")
