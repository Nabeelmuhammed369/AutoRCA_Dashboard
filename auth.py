"""
auth.py — AutoRCA Authentication Module v1.1
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Endpoints mounted by api_server.py on startup:
  POST /api/auth/register      — create org + generate API key
  POST /api/auth/validate-key  — validate key against DB hash
  GET  /api/auth/me            — get org info from X-API-Key header

Security model:
  • Raw key: autorca_live_<48 hex chars> — 192 bits entropy, shown ONCE
  • Only SHA-256 hash stored in Supabase — raw key never persisted
  • Login: submit raw key → server hashes → DB lookup → match = access

Requirements:
  • Place this file next to api_server.py in the project root
  • Supabase tables: organizations, api_keys (see session notes for SQL)
  • Python 3.10+ compatible (no datetime.UTC, no X | Y union syntax)
"""

import hashlib
import logging
import re
import secrets
import traceback
from datetime import datetime, timezone          # ← timezone.utc, NOT UTC (3.10 compat)
from typing import Optional                      # ← Optional[X] not X | None (3.10 compat)

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

logger = logging.getLogger("AutoRCA.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ══════════════════════════════════════════════════════════════════
# PYDANTIC MODELS  — using Optional[X] for Python 3.10 compat
# ══════════════════════════════════════════════════════════════════

class RegisterRequest(BaseModel):
    org_name:     str                = Field(..., min_length=2, max_length=120)
    email:        EmailStr
    plan:         str                = Field(default="free")
    account_type: str                = Field(default="biz")
    website:      Optional[str]      = Field(default=None, max_length=255)
    ref_code:     Optional[str]      = Field(default=None, max_length=50)


class ValidateKeyRequest(BaseModel):
    api_key: str = Field(..., min_length=10, max_length=200)


# ══════════════════════════════════════════════════════════════════
# KEY UTILITIES
# ══════════════════════════════════════════════════════════════════

def _generate_raw_key() -> str:
    """autorca_live_<48 hex chars> — 61 chars total, 192 bits entropy."""
    return "autorca_live_" + secrets.token_hex(24)


def _hash_key(raw_key: str) -> str:
    """SHA-256 hex digest — the ONLY thing stored in the database."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _key_prefix(raw_key: str) -> str:
    """First 20 chars + ellipsis for safe display. Cannot reconstruct key."""
    return raw_key[:20] + "..."


def _valid_key_format(key: str) -> bool:
    """
    Quick format gate before hitting the database.
    Rejects garbage input without a DB round-trip.
    Valid: autorca_live_<48 lowercase hex chars>
           autorca_test_<48 lowercase hex chars>
    """
    return bool(re.match(r"^autorca_(live|test)_[a-f0-9]{48}$", key))


# ══════════════════════════════════════════════════════════════════
# SUPABASE HELPER — lazy import avoids circular dependency
# ══════════════════════════════════════════════════════════════════

def _get_sb():
    """
    Return the shared Supabase client from api_server module.
    Lazy import prevents circular dependency at load time.
    Returns None if Supabase is not configured.
    """
    try:
        import api_server
        return api_server._sb
    except Exception as e:
        logger.warning(f"[auth._get_sb] Could not import api_server._sb: {e}")
        return None


# ══════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════

@router.post("/register", summary="Create organisation and generate API key")
async def register(body: RegisterRequest, request: Request):
    """
    Flow:
      1. Check email not already registered (→ 409 if duplicate)
      2. Insert organisation row
      3. Generate raw key + SHA-256 hash
      4. Insert api_keys row — stores HASH only, never raw key
      5. Return raw key to caller — shown ONCE, never stored

    On any failure after org insert → org row is deleted (rollback).
    """
    logger.info(f"[register] Request from {request.client.host} for org='{body.org_name}' email={body.email}")

    sb = _get_sb()
    if sb is None:
        logger.error("[register] Supabase client is None — SUPABASE_URL/KEY not configured")
        raise HTTPException(
            status_code=503,
            detail="Database not configured. Set SUPABASE_URL and SUPABASE_KEY in Render environment."
        )

    # 1. Normalise inputs
    email    = body.email.lower().strip()
    org_name = body.org_name.strip()
    plan     = body.plan if body.plan in ("free", "pro") else "free"
    acc_type = body.account_type if body.account_type in ("biz", "ind") else "biz"
    logger.info(f"[register] Normalised: org='{org_name}' email={email} plan={plan} type={acc_type}")

    # 2. Duplicate email check
    try:
        existing = sb.table("organizations").select("id").eq("email", email).execute()
        if existing.data:
            logger.warning(f"[register] Duplicate email rejected: {email}")
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists. Please sign in instead."
            )
        logger.info(f"[register] Email {email} is available")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[register] DB duplicate check failed: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Database error during registration check.")

    # 3. Insert organisation row
    org_id = None
    try:
        org_result = sb.table("organizations").insert({
            "org_name":     org_name,
            "email":        email,
            "account_type": acc_type,
            "plan":         plan,
            "website":      body.website,
            "ref_code":     body.ref_code,
            "status":       "active",
        }).execute()
        org_id = org_result.data[0]["id"]
        logger.info(f"[register] Org created: id={org_id} name='{org_name}'")
    except Exception as e:
        logger.error(f"[register] Failed to insert org row: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to create organisation record.")

    # 4. Generate key — store HASH only, never the raw value
    raw_key  = _generate_raw_key()
    key_hash = _hash_key(raw_key)
    prefix   = _key_prefix(raw_key)
    logger.info(f"[register] Generated key prefix={prefix} for org {org_id}")

    try:
        sb.table("api_keys").insert({
            "org_id":     org_id,
            "key_hash":   key_hash,      # ← SHA-256 only
            "key_prefix": prefix,
            "label":      "Default Key",
            "is_active":  True,
        }).execute()
        logger.info(f"[register] Key hash stored for org {org_id}")
    except Exception as e:
        # Rollback: delete the org row to avoid orphaned records
        logger.error(f"[register] Key insert failed, rolling back org {org_id}: {e}\n{traceback.format_exc()}")
        try:
            sb.table("organizations").delete().eq("id", org_id).execute()
            logger.info(f"[register] Rollback successful — org {org_id} deleted")
        except Exception as rb_err:
            logger.error(f"[register] Rollback also failed for org {org_id}: {rb_err}")
        raise HTTPException(status_code=500, detail="Failed to generate API key. Registration rolled back.")

    logger.info(f"[register] SUCCESS org_id={org_id} email={email} plan={plan}")

    # 5. Return raw key ONCE — never stored, never retrievable again
    return {
        "org_id":   org_id,
        "org_name": org_name,
        "email":    email,
        "plan":     plan,
        "api_key":  raw_key,
        "message":  "Registration successful. Save your API key — it will not be shown again.",
    }


@router.post("/validate-key", summary="Validate API key against database hash")
async def validate_key(body: ValidateKeyRequest, request: Request):
    """
    Flow:
      1. Format gate — rejects garbage without hitting DB
      2. SHA-256 hash the submitted key
      3. Lookup hash in api_keys table (JOIN organizations)
      4. Check is_active and org.status
      5. Update last_used_at timestamp (best-effort)
      6. Return org info to client

    Returns 401 on invalid/inactive key, 403 on suspended org.
    """
    logger.info(f"[validate-key] Request from {request.client.host}")

    sb = _get_sb()
    if sb is None:
        logger.error("[validate-key] Supabase client is None")
        raise HTTPException(
            status_code=503,
            detail="Database not configured. Set SUPABASE_URL and SUPABASE_KEY in Render environment."
        )

    raw_key = body.api_key.strip()

    # 1. Format gate — no DB hit on obviously bad input
    if not _valid_key_format(raw_key):
        logger.warning(f"[validate-key] Invalid format rejected: prefix={raw_key[:20]}")
        raise HTTPException(
            status_code=401,
            detail="Invalid API key format. Keys look like autorca_live_xxxxxxxx…"
        )

    # 2. Hash the key
    key_hash = _hash_key(raw_key)
    logger.info(f"[validate-key] Looking up hash for prefix={raw_key[:20]}")

    # 3. DB lookup
    try:
        result = (
            sb.table("api_keys")
            .select("id, org_id, is_active, label, organizations(id, org_name, email, plan, status)")
            .eq("key_hash", key_hash)
            .single()
            .execute()
        )
    except Exception as e:
        err_str = str(e)
        # supabase-py raises when .single() finds 0 rows
        if "PGRST116" in err_str or "JSON object requested" in err_str or "No rows" in err_str.lower():
            logger.warning(f"[validate-key] Key not found in DB (PGRST116 or no rows)")
            raise HTTPException(status_code=401, detail="Invalid or inactive API key.")
        logger.error(f"[validate-key] DB lookup error: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Database error during key validation.")

    if not result.data:
        logger.warning("[validate-key] result.data is empty after lookup")
        raise HTTPException(status_code=401, detail="Invalid or inactive API key.")

    key_row = result.data
    org     = key_row.get("organizations") or {}

    # 4. Active checks
    if not key_row.get("is_active", False):
        logger.warning(f"[validate-key] Deactivated key rejected for org {key_row.get('org_id')}")
        raise HTTPException(status_code=401, detail="This API key has been deactivated.")

    if org.get("status") != "active":
        logger.warning(f"[validate-key] Suspended org {org.get('id')} rejected")
        raise HTTPException(status_code=403, detail="Your account has been suspended. Contact support.")

    # 5. Update last_used_at — best-effort, never fail the login over this
    try:
        sb.table("api_keys").update({
            "last_used_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", key_row["id"]).execute()
    except Exception as e:
        logger.warning(f"[validate-key] last_used_at update failed (non-critical): {e}")

    logger.info(f"[validate-key] SUCCESS org_id={org.get('id')} org='{org.get('org_name')}' plan={org.get('plan')}")

    return {
        "valid":    True,
        "org_id":   org.get("id"),
        "org_name": org.get("org_name"),
        "email":    org.get("email"),
        "plan":     org.get("plan", "free"),
    }


@router.get("/me", summary="Get current account info from X-API-Key header")
async def get_me(request: Request):
    """
    Reads X-API-Key header, validates it, returns org info.
    Used by dashboard to show logged-in org name and plan badge.
    """
    raw_key = request.headers.get("X-API-Key", "").strip()
    if not raw_key:
        logger.warning(f"[me] Request without X-API-Key from {request.client.host}")
        raise HTTPException(status_code=401, detail="X-API-Key header required.")
    return await validate_key(ValidateKeyRequest(api_key=raw_key), request)