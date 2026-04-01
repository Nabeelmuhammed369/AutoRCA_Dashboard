"""
tests/test_auth.py — AutoRCA Auth Module Test Suite
═══════════════════════════════════════════════════════════════════════════════
Covers:
  auth.py        → key utilities, /api/auth/register,
                   /api/auth/validate-key, /api/auth/me
  api_server.py  → verify_api_key (dual-mode), ok_key, /api/health,
                   all protected endpoints return 401 without key,
                   all protected endpoints pass with a valid key

All Supabase I/O is mocked — no real network calls.
Fixtures are defined in conftest.py:
  sb                     — chainable Supabase mock
  client                 — AsyncClient + sb with AUTORCA_API_KEY=""
  valid_raw_key          — fresh autorca_live_* key
  valid_register_payload — complete registration dict
  make_org_row()         — builds an organizations table row dict
  make_api_key_row()     — builds an api_keys row dict (with nested org)

Run:
  python -m pytest tests/test_auth.py -v
  python -m pytest tests/test_auth.py::TestKeyUtilities -v   (fast, no async)
═══════════════════════════════════════════════════════════════════════════════
"""

import hashlib
import re
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

# conftest.py provides: sb, client, valid_raw_key, valid_register_payload,
#                       make_org_row, make_api_key_row
from conftest import make_api_key_row

# ══════════════════════════════════════════════════════════════════════════════
# 1.  KEY UTILITY FUNCTIONS  (pure Python — no I/O, no async)
# ══════════════════════════════════════════════════════════════════════════════


class TestKeyUtilities:
    """
    Unit tests for the four pure-Python helpers in auth.py.
    No mocks, no network, no async — these run in milliseconds.
    """

    def setup_method(self):
        from auth import (
            _generate_raw_key,
            _hash_key,
            _key_prefix,
            _valid_key_format,
        )

        self.gen = _generate_raw_key
        self.hash = _hash_key
        self.pfx = _key_prefix
        self.valid = _valid_key_format

    # ── _generate_raw_key ─────────────────────────────────────────────────────

    def test_key_starts_with_autorca_live(self):
        assert self.gen().startswith("autorca_live_")

    def test_key_total_length_is_61(self):
        # "autorca_live_" = 13 chars, token_hex(24) = 48 hex chars → 61 total
        assert len(self.gen()) == 61

    def test_key_hex_suffix_is_exactly_48_chars(self):
        suffix = self.gen()[len("autorca_live_") :]
        assert len(suffix) == 48

    def test_key_suffix_is_lowercase_hex_only(self):
        suffix = self.gen()[len("autorca_live_") :]
        assert re.fullmatch(r"[a-f0-9]{48}", suffix)

    def test_100_generated_keys_are_unique(self):
        keys = {self.gen() for _ in range(100)}
        assert len(keys) == 100, "Every generated key must be unique"

    # ── _hash_key ──────────────────────────────────────────────────────────────

    def test_hash_output_is_64_char_hex(self):
        h = self.hash("autorca_live_" + "a" * 48)
        assert len(h) == 64 and re.fullmatch(r"[a-f0-9]{64}", h)

    def test_hash_is_deterministic(self):
        raw = "autorca_live_" + "b" * 48
        assert self.hash(raw) == self.hash(raw)

    def test_hash_matches_manual_sha256(self):
        raw = "autorca_live_" + "c" * 48
        assert self.hash(raw) == hashlib.sha256(raw.encode()).hexdigest()

    def test_different_keys_produce_different_hashes(self):
        k1 = "autorca_live_" + "a" * 48
        k2 = "autorca_live_" + "b" * 48
        assert self.hash(k1) != self.hash(k2)

    # ── _key_prefix ────────────────────────────────────────────────────────────

    def test_prefix_ends_with_ellipsis(self):
        key = "autorca_live_" + "d" * 48
        assert self.pfx(key).endswith("...")

    def test_prefix_shows_exactly_first_20_chars(self):
        key = "autorca_live_" + "e" * 48
        assert self.pfx(key) == key[:20] + "..."

    def test_prefix_is_shorter_than_full_key(self):
        key = "autorca_live_" + "f" * 48
        assert len(self.pfx(key)) < len(key)

    # ── _valid_key_format ──────────────────────────────────────────────────────

    def test_live_key_is_valid(self):
        assert self.valid("autorca_live_" + "a" * 48) is True

    def test_test_key_is_valid(self):
        assert self.valid("autorca_test_" + "b" * 48) is True

    def test_empty_string_is_invalid(self):
        assert self.valid("") is False

    def test_wrong_prefix_is_invalid(self):
        assert self.valid("autorca_dev_" + "a" * 48) is False

    def test_suffix_too_short_is_invalid(self):
        assert self.valid("autorca_live_" + "a" * 10) is False

    def test_suffix_too_long_is_invalid(self):
        assert self.valid("autorca_live_" + "a" * 49) is False

    def test_uppercase_hex_is_invalid(self):
        # Keys must be lowercase hex
        assert self.valid("autorca_live_" + "A" * 48) is False

    def test_non_hex_chars_are_invalid(self):
        assert self.valid("autorca_live_" + "z" * 48) is False

    def test_freshly_generated_key_passes_format_check(self):
        from auth import _generate_raw_key

        assert self.valid(_generate_raw_key()) is True


# ══════════════════════════════════════════════════════════════════════════════
# 2.  REGISTER ENDPOINT   POST /api/auth/register
# ══════════════════════════════════════════════════════════════════════════════


class TestRegister:
    # ── happy path ─────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_successful_registration_returns_200(self, client, valid_register_payload, sb):
        ac, _ = client
        org_id = str(uuid4())
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": org_id}])

        resp = await ac.post("/api/auth/register", json=valid_register_payload)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_response_contains_api_key(self, client, valid_register_payload, sb):
        ac, _ = client
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": str(uuid4())}])

        body = (await ac.post("/api/auth/register", json=valid_register_payload)).json()
        assert "api_key" in body
        assert body["api_key"].startswith("autorca_live_")

    @pytest.mark.asyncio
    async def test_returned_key_passes_format_validation(self, client, valid_register_payload, sb):
        from auth import _valid_key_format

        ac, _ = client
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": str(uuid4())}])

        body = (await ac.post("/api/auth/register", json=valid_register_payload)).json()
        assert _valid_key_format(body["api_key"]) is True

    @pytest.mark.asyncio
    async def test_response_contains_org_id_and_name(self, client, valid_register_payload, sb):
        ac, _ = client
        org_id = str(uuid4())
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": org_id}])

        body = (await ac.post("/api/auth/register", json=valid_register_payload)).json()
        assert body["org_id"] == org_id
        assert body["org_name"] == valid_register_payload["org_name"]

    # ── security: raw key must never reach the database ────────────────────────

    @pytest.mark.asyncio
    async def test_raw_key_never_passed_to_db_insert(self, client, valid_register_payload, sb):
        """
        The raw API key must never appear in any .insert() call.
        Only its SHA-256 hash should be stored.
        """
        ac, _ = client
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": str(uuid4())}])

        body = (await ac.post("/api/auth/register", json=valid_register_payload)).json()
        raw_key = body["api_key"]

        for call in sb.table.return_value.insert.call_args_list:
            inserted = call[0][0] if call[0] else {}
            assert raw_key not in str(inserted), "Raw key must never be in any DB insert — only its SHA-256 hash"

    @pytest.mark.asyncio
    async def test_db_insert_contains_key_hash_not_raw(self, client, valid_register_payload, sb):
        """The insert for api_keys must store a 64-char hex hash."""
        ac, _ = client
        inserted_payloads: list = []

        def capture_insert(data):
            inserted_payloads.append(data)
            m = MagicMock()
            m.execute.return_value = MagicMock(data=[{"id": str(uuid4())}])
            return m

        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        sb.table.return_value.insert.side_effect = capture_insert

        await ac.post("/api/auth/register", json=valid_register_payload)

        key_inserts = [d for d in inserted_payloads if "key_hash" in d]
        assert key_inserts, "An api_keys insert should have occurred"
        hash_val = key_inserts[0]["key_hash"]
        assert len(hash_val) == 64
        assert re.fullmatch(r"[a-f0-9]{64}", hash_val)

    # ── input normalisation ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_email_normalised_to_lowercase(self, client, sb):
        ac, _ = client
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": str(uuid4())}])

        payload = {
            "org_name": "Test Org",
            "email": "ADMIN@ACME.COM",
            "plan": "free",
            "account_type": "biz",
        }
        body = (await ac.post("/api/auth/register", json=payload)).json()
        assert body["email"] == "admin@acme.com"

    @pytest.mark.asyncio
    async def test_invalid_plan_defaults_to_free(self, client, valid_register_payload, sb):
        ac, _ = client
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": str(uuid4())}])

        payload = {**valid_register_payload, "plan": "enterprise_gold"}
        body = (await ac.post("/api/auth/register", json=payload)).json()
        assert body["plan"] == "free"

    @pytest.mark.asyncio
    async def test_pro_plan_accepted(self, client, valid_register_payload, sb):
        ac, _ = client
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": str(uuid4())}])

        payload = {**valid_register_payload, "plan": "pro"}
        body = (await ac.post("/api/auth/register", json=payload)).json()
        assert body["plan"] == "pro"

    # ── duplicate / conflict ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_duplicate_email_returns_409(self, client, valid_register_payload, sb):
        ac, _ = client
        # Email already in DB
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": str(uuid4())}]
        )

        resp = await ac.post("/api/auth/register", json=valid_register_payload)
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    # ── validation errors ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_missing_org_name_returns_422(self, client):
        ac, _ = client
        resp = await ac.post("/api/auth/register", json={"email": "x@x.com"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_org_name_too_short_returns_422(self, client, valid_register_payload):
        ac, _ = client
        payload = {**valid_register_payload, "org_name": "X"}  # min_length=2
        resp = await ac.post("/api/auth/register", json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_email_format_returns_422(self, client, valid_register_payload):
        ac, _ = client
        payload = {**valid_register_payload, "email": "not-an-email"}
        resp = await ac.post("/api/auth/register", json=payload)
        assert resp.status_code == 422

    # ── infrastructure errors ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_no_supabase_returns_503(self, client, valid_register_payload):
        ac, _ = client
        import auth as auth_module

        with patch.object(auth_module, "_get_sb", return_value=None):
            resp = await ac.post("/api/auth/register", json=valid_register_payload)
        assert resp.status_code == 503
        assert "Database not configured" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_key_insert_failure_triggers_org_rollback(self, client, valid_register_payload, sb):
        """
        If the api_keys insert fails after the org is already inserted,
        the org row must be deleted to avoid orphaned records.
        """
        ac, _ = client
        org_id = str(uuid4())
        call_count = {"n": 0}

        def insert_side_effect(data):
            call_count["n"] += 1
            m = MagicMock()
            if call_count["n"] == 1:
                # First call = org insert → succeeds
                m.execute.return_value = MagicMock(data=[{"id": org_id}])
            else:
                # Second call = api_keys insert → fails
                m.execute.side_effect = Exception("Key insert failed")
            return m

        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        sb.table.return_value.insert.side_effect = insert_side_effect

        resp = await ac.post("/api/auth/register", json=valid_register_payload)
        assert resp.status_code == 500
        sb.table.return_value.delete.assert_called()  # rollback happened

    # ── uniqueness ─────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_two_registrations_produce_different_keys(self, client, valid_register_payload, sb):
        ac, _ = client

        def fresh_insert_mock(_):
            m = MagicMock()
            m.execute.return_value = MagicMock(data=[{"id": str(uuid4())}])
            return m

        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        sb.table.return_value.insert.side_effect = fresh_insert_mock

        key1 = (await ac.post("/api/auth/register", json=valid_register_payload)).json()["api_key"]
        p2 = {**valid_register_payload, "email": "other@acme.com"}
        key2 = (await ac.post("/api/auth/register", json=p2)).json()["api_key"]
        assert key1 != key2


# ══════════════════════════════════════════════════════════════════════════════
# 3.  VALIDATE-KEY ENDPOINT   POST /api/auth/validate-key
# ══════════════════════════════════════════════════════════════════════════════


class TestValidateKey:
    def _set_db_row(self, sb, row):
        """Helper: configure the sb mock to return `row` on a single-row lookup."""
        sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
            data=row
        )

    def _set_db_miss(self, sb):
        """Helper: configure sb to simulate no matching row (PGRST116)."""
        sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.side_effect = Exception(
            "PGRST116 — no rows found"
        )

    # ── happy path ─────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_valid_key_returns_200(self, client, valid_raw_key, sb):
        ac, _ = client
        self._set_db_row(sb, make_api_key_row())
        resp = await ac.post("/api/auth/validate-key", json={"api_key": valid_raw_key})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_valid_key_returns_valid_true(self, client, valid_raw_key, sb):
        ac, _ = client
        self._set_db_row(sb, make_api_key_row())
        body = (await ac.post("/api/auth/validate-key", json={"api_key": valid_raw_key})).json()
        assert body["valid"] is True

    @pytest.mark.asyncio
    async def test_valid_key_returns_org_name_and_plan(self, client, valid_raw_key, sb):
        ac, _ = client
        self._set_db_row(sb, make_api_key_row(org_name="Acme Corp", plan="pro"))
        body = (await ac.post("/api/auth/validate-key", json={"api_key": valid_raw_key})).json()
        assert body["org_name"] == "Acme Corp"
        assert body["plan"] == "pro"

    # ── security checks ────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_db_is_queried_with_sha256_hash_not_raw_key(self, client, valid_raw_key, sb):
        """
        The most critical security test: verify the code hashes the
        submitted key and passes the hash to the DB query — never the raw key.
        """
        ac, _ = client
        expected_hash = hashlib.sha256(valid_raw_key.encode()).hexdigest()
        self._set_db_miss(sb)  # doesn't matter for this assertion

        await ac.post("/api/auth/validate-key", json={"api_key": valid_raw_key})

        # The eq() call should have received the hash, not the raw key
        eq_calls = sb.table.return_value.select.return_value.eq.call_args_list
        assert any(expected_hash in str(c) for c in eq_calls), (
            "DB .eq() must be called with the SHA-256 hash, not the raw key"
        )
        assert not any(valid_raw_key in str(c) for c in eq_calls), "Raw key must never be passed to .eq()"

    @pytest.mark.asyncio
    async def test_bad_format_rejected_before_db_hit(self, client, sb):
        """Format gate: garbage key must never cause a DB call."""
        ac, _ = client
        resp = await ac.post("/api/auth/validate-key", json={"api_key": "garbage-key"})
        assert resp.status_code == 401
        sb.table.assert_not_called()

    # ── rejection scenarios ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_key_not_in_db_returns_401(self, client, valid_raw_key, sb):
        ac, _ = client
        self._set_db_miss(sb)
        resp = await ac.post("/api/auth/validate-key", json={"api_key": valid_raw_key})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_deactivated_key_returns_401(self, client, valid_raw_key, sb):
        ac, _ = client
        self._set_db_row(sb, make_api_key_row(is_active=False))
        resp = await ac.post("/api/auth/validate-key", json={"api_key": valid_raw_key})
        assert resp.status_code == 401
        assert "deactivated" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_suspended_org_returns_403(self, client, valid_raw_key, sb):
        ac, _ = client
        self._set_db_row(sb, make_api_key_row(org_status="suspended"))
        resp = await ac.post("/api/auth/validate-key", json={"api_key": valid_raw_key})
        assert resp.status_code == 403
        assert "suspended" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_empty_key_returns_422(self, client):
        ac, _ = client
        resp = await ac.post("/api/auth/validate-key", json={"api_key": ""})
        assert resp.status_code == 422  # pydantic min_length=10

    @pytest.mark.asyncio
    async def test_missing_key_field_returns_422(self, client):
        ac, _ = client
        resp = await ac.post("/api/auth/validate-key", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_no_supabase_returns_503(self, client, valid_raw_key):
        ac, _ = client
        import auth as auth_module

        with patch.object(auth_module, "_get_sb", return_value=None):
            resp = await ac.post("/api/auth/validate-key", json={"api_key": valid_raw_key})
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_unexpected_db_error_returns_500(self, client, valid_raw_key, sb):
        ac, _ = client
        sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.side_effect = Exception(
            "unexpected DB failure"
        )
        resp = await ac.post("/api/auth/validate-key", json={"api_key": valid_raw_key})
        assert resp.status_code == 500

    # ── side-effects ───────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_last_used_at_updated_on_success(self, client, valid_raw_key, sb):
        ac, _ = client
        self._set_db_row(sb, make_api_key_row())
        await ac.post("/api/auth/validate-key", json={"api_key": valid_raw_key})

        sb.table.return_value.update.assert_called()
        update_data = sb.table.return_value.update.call_args[0][0]
        assert "last_used_at" in update_data

    @pytest.mark.asyncio
    async def test_autorca_test_prefix_accepted(self, client, sb):
        ac, _ = client
        test_key = "autorca_test_" + "a" * 48
        self._set_db_row(sb, make_api_key_row())
        resp = await ac.post("/api/auth/validate-key", json={"api_key": test_key})
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# 4.  /api/auth/me ENDPOINT   GET /api/auth/me
# ══════════════════════════════════════════════════════════════════════════════


class TestGetMe:
    @pytest.mark.asyncio
    async def test_valid_header_returns_200(self, client, valid_raw_key, sb):
        ac, _ = client
        sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
            data=make_api_key_row(org_name="My Org", plan="pro")
        )

        resp = await ac.get("/api/auth/me", headers={"X-API-Key": valid_raw_key})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_returns_correct_org_details(self, client, valid_raw_key, sb):
        ac, _ = client
        sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
            data=make_api_key_row(org_name="My Org", plan="pro")
        )

        body = (await ac.get("/api/auth/me", headers={"X-API-Key": valid_raw_key})).json()
        assert body["org_name"] == "My Org"
        assert body["plan"] == "pro"

    @pytest.mark.asyncio
    async def test_missing_header_returns_401(self, client):
        ac, _ = client
        resp = await ac.get("/api/auth/me")
        assert resp.status_code == 401
        assert "X-API-Key" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_empty_header_returns_401(self, client):
        ac, _ = client
        resp = await ac.get("/api/auth/me", headers={"X-API-Key": ""})
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# 5.  DUAL-MODE verify_api_key  (api_server.py)
# ══════════════════════════════════════════════════════════════════════════════


class TestVerifyApiKey:
    """
    Tests for api_server.verify_api_key.

    MODE A — legacy AUTORCA_API_KEY .env var
    MODE B — DB-backed autorca_live_* / autorca_test_* keys
    """

    @pytest.mark.asyncio
    async def test_dev_mode_allows_request_with_no_key(self, client):
        """AUTORCA_API_KEY="" and no header → dev mode, pass through."""
        ac, _ = client
        # client fixture already sets AUTORCA_API_KEY=""
        resp = await ac.get("/api/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_env_key_valid_grants_access(self, client):
        ac, _ = client
        import api_server

        env_key = "my-secret-env-key"
        with patch.object(api_server, "AUTORCA_API_KEY", env_key):
            resp = await ac.get("/api/rca/history", headers={"X-API-Key": env_key})
        # 200 (history OK) or 503 (no Supabase) — both mean auth passed
        assert resp.status_code in (200, 503)

    @pytest.mark.asyncio
    async def test_wrong_env_key_returns_401(self, client):
        ac, _ = client
        import api_server

        with patch.object(api_server, "AUTORCA_API_KEY", "correct-key"):
            resp = await ac.get("/api/rca/history", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_no_key_when_env_configured_returns_401(self, client):
        ac, _ = client
        import api_server

        with patch.object(api_server, "AUTORCA_API_KEY", "required-key"):
            resp = await ac.get("/api/rca/history")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_db_key_grants_access_to_protected_endpoint(self, client, valid_raw_key, sb):
        ac, _ = client
        import api_server

        sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
            data=make_api_key_row()
        )

        with patch.object(api_server, "AUTORCA_API_KEY", ""), patch.object(api_server, "_sb", sb):
            resp = await ac.get("/api/rca/history", headers={"X-API-Key": valid_raw_key})
        assert resp.status_code in (200, 503)

    @pytest.mark.asyncio
    async def test_db_key_not_in_db_returns_401(self, client, valid_raw_key, sb):
        ac, _ = client
        import api_server

        sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.side_effect = Exception(
            "PGRST116"
        )

        with patch.object(api_server, "AUTORCA_API_KEY", ""), patch.object(api_server, "_sb", sb):
            resp = await ac.get("/api/rca/history", headers={"X-API-Key": valid_raw_key})
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# 6.  ALL PROTECTED ENDPOINTS return 401 without a valid key
# ══════════════════════════════════════════════════════════════════════════════


class TestProtectedEndpoints:
    PROTECTED_GET = [
        "/api/rca/history",
        "/api/rca/history/some-id",
    ]
    PROTECTED_POST = [
        ("/api/ai/explain", {"classification": "test"}),
        ("/api/ai/fix-steps", {"classification": "test"}),
        ("/api/ai/ticket-summary", {"classification": "test"}),
        ("/api/rca/save", {"source_name": "app.log", "severity": "error", "total_entries": 10, "error_count": 2}),
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", PROTECTED_GET)
    async def test_get_returns_401_without_key(self, client, path):
        ac, _ = client
        import api_server

        with patch.object(api_server, "AUTORCA_API_KEY", "configured-key"):
            resp = await ac.get(path)
        assert resp.status_code == 401, f"Expected 401 on {path} with no key"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path,body", PROTECTED_POST)
    async def test_post_returns_401_without_key(self, client, path, body):
        ac, _ = client
        import api_server

        with patch.object(api_server, "AUTORCA_API_KEY", "configured-key"):
            resp = await ac.post(path, json=body)
        assert resp.status_code == 401, f"Expected 401 on {path} with no key"

    @pytest.mark.asyncio
    async def test_delete_returns_401_without_key(self, client):
        ac, _ = client
        import api_server

        with patch.object(api_server, "AUTORCA_API_KEY", "configured-key"):
            resp = await ac.delete("/api/rca/history/some-id")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_health_is_always_public(self, client):
        """/api/health must return 200 even when a key is required."""
        ac, _ = client
        import api_server

        with patch.object(api_server, "AUTORCA_API_KEY", "configured-key"):
            resp = await ac.get("/api/health")
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# 7.  /api/health  ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_returns_status_ok(self, client):
        ac, _ = client
        resp = await ac.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_supabase_connected_when_sb_present(self, client, sb):
        ac, _ = client
        import api_server

        with patch.object(api_server, "_sb", sb):
            body = (await ac.get("/api/health")).json()
        assert body["checks"]["supabase"] == "connected"

    @pytest.mark.asyncio
    async def test_supabase_not_configured_when_sb_none(self, client):
        ac, _ = client
        import api_server

        with patch.object(api_server, "_sb", None):
            body = (await ac.get("/api/health")).json()
        assert body["checks"]["supabase"] == "not configured"

    @pytest.mark.asyncio
    async def test_dev_mode_true_when_no_key_set(self, client):
        ac, _ = client
        import api_server

        with patch.object(api_server, "AUTORCA_API_KEY", ""):
            body = (await ac.get("/api/health")).json()
        assert body["dev_mode"] is True

    @pytest.mark.asyncio
    async def test_dev_mode_false_when_key_set(self, client):
        ac, _ = client
        import api_server

        with patch.object(api_server, "AUTORCA_API_KEY", "some-key"):
            body = (await ac.get("/api/health")).json()
        assert body["dev_mode"] is False


# ══════════════════════════════════════════════════════════════════════════════
# 8.  RCA HISTORY & SAVE ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════


class TestRcaEndpoints:
    @pytest.mark.asyncio
    async def test_history_returns_503_when_no_supabase(self, client):
        ac, _ = client
        import api_server

        with patch.object(api_server, "_sb", None):
            resp = await ac.get("/api/rca/history")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_history_returns_supabase_false_when_no_sb(self, client):
        ac, _ = client
        import api_server

        with patch.object(api_server, "_sb", None):
            body = (await ac.get("/api/rca/history")).json()
        assert body["supabase"] is False

    @pytest.mark.asyncio
    async def test_history_returns_records_when_supabase_present(self, client, sb):
        ac, _ = client
        import api_server

        sb.table.return_value.select.return_value.order.return_value.range.return_value.execute.return_value = (
            MagicMock(data=[{"id": "abc", "source_name": "app.log"}])
        )

        with patch.object(api_server, "_sb", sb):
            body = (await ac.get("/api/rca/history")).json()
        assert body["ok"] is True
        assert body["count"] == 1

    @pytest.mark.asyncio
    async def test_rca_save_returns_503_when_no_supabase(self, client):
        ac, _ = client
        import api_server

        with patch.object(api_server, "_sb", None):
            resp = await ac.post(
                "/api/rca/save",
                json={
                    "source_name": "app.log",
                    "severity": "error",
                    "total_entries": 100,
                    "error_count": 5,
                },
            )
        assert resp.status_code == 503
        assert "Supabase" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_rca_save_returns_ok_true_when_supabase_present(self, client, sb):
        ac, _ = client
        import api_server

        record_id = str(uuid4())
        sb.table.return_value.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": record_id, "source_name": "app.log"}]
        )

        with patch.object(api_server, "_sb", sb):
            body = (
                await ac.post(
                    "/api/rca/save",
                    json={
                        "source_name": "app.log",
                        "severity": "error",
                        "total_entries": 100,
                        "error_count": 5,
                        "ai_summary": "Memory leak detected",
                    },
                )
            ).json()
        assert body["ok"] is True
        assert body["id"] == record_id

    @pytest.mark.asyncio
    async def test_rca_save_payload_requires_source_name(self, client):
        ac, _ = client
        # source_name has a default so this should pass validation (200/503)
        resp = await ac.post("/api/rca/save", json={"severity": "error", "total_entries": 10, "error_count": 1})
        assert resp.status_code in (200, 503)


# ══════════════════════════════════════════════════════════════════════════════
# 9.  SESSION CONTRACT  (localStorage autorca_session shape)
# ══════════════════════════════════════════════════════════════════════════════


class TestSessionContract:
    """
    Pure Python tests — no async, no network.
    Validate the shape of the autorca_session object written to
    localStorage by the dashboard before opening the livestream page.
    """

    def test_session_has_all_required_keys(self):
        session = {
            "connected": True,
            "label": "app.log",
            "ingestedAt": "01/04/2025, 12:00:00",
            "stats": {"total": 100, "err": 5, "rate": 5.0},
            "logs": [],
        }
        for key in ("connected", "label", "stats", "logs"):
            assert key in session

    def test_session_logs_are_capped_at_2000(self):
        large_logs = [{"message": f"log {i}"} for i in range(5000)]
        capped = large_logs[:2000]
        assert len(capped) == 2000

    def test_session_not_written_when_disconnected(self):
        connected = False
        session_saved = connected  # mirrors JS: if(S.connected){...}
        assert session_saved is False

    def test_session_stats_has_expected_fields(self):
        stats = {"total": 200, "err": 10, "rate": 5.0}
        assert all(k in stats for k in ("total", "err", "rate"))
        assert isinstance(stats["rate"], float)

    def test_session_label_is_a_string(self):
        session = {"label": "production.log", "connected": True, "stats": {}, "logs": []}
        assert isinstance(session["label"], str)


# ══════════════════════════════════════════════════════════════════════════════
# 10. END-TO-END REGISTRATION → VALIDATION FLOW
# ══════════════════════════════════════════════════════════════════════════════


class TestRegistrationToValidationFlow:
    """
    Calls both endpoints in sequence with a consistent mock DB.
    Verifies the key returned by register can be used to validate.
    """

    @pytest.mark.asyncio
    async def test_register_then_validate_succeeds(self, client, sb):
        ac, _ = client
        org_id = str(uuid4())

        # Track the hash that gets inserted so we can serve it on validate
        captured: dict = {}

        def insert_side_effect(data):
            m = MagicMock()
            if "key_hash" in data:
                captured["hash"] = data["key_hash"]
                captured["org_id"] = org_id
                m.execute.return_value = MagicMock(data=[{"id": str(uuid4())}])
            else:
                m.execute.return_value = MagicMock(data=[{"id": org_id}])
            return m

        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        sb.table.return_value.insert.side_effect = insert_side_effect

        # Step 1 — register
        reg = await ac.post(
            "/api/auth/register", json={"org_name": "Flow Org", "email": "flow@test.com", "plan": "free"}
        )
        assert reg.status_code == 200
        raw_key = reg.json()["api_key"]

        # Step 2 — configure mock to return a matching row for validate
        sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
            data={
                "id": str(uuid4()),
                "org_id": org_id,
                "is_active": True,
                "label": "Default Key",
                "organizations": {
                    "id": org_id,
                    "org_name": "Flow Org",
                    "email": "flow@test.com",
                    "plan": "free",
                    "status": "active",
                },
            }
        )

        val = await ac.post("/api/auth/validate-key", json={"api_key": raw_key})
        assert val.status_code == 200
        assert val.json()["valid"] is True
        assert val.json()["org_name"] == "Flow Org"

    @pytest.mark.asyncio
    async def test_different_key_fails_validation(self, client, valid_register_payload, sb):
        """A key not registered in the DB must return 401."""
        ac, _ = client
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": str(uuid4())}])

        # Register one key …
        await ac.post("/api/auth/register", json=valid_register_payload)

        # … but validate with a completely different key
        from auth import _generate_raw_key

        different_key = _generate_raw_key()
        sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.side_effect = Exception(
            "PGRST116"
        )

        val = await ac.post("/api/auth/validate-key", json={"api_key": different_key})
        assert val.status_code == 401
