"""
auth.py — AutoRCA Authentication Module
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Handles:
  • Organization creation  → POST /api/auth/register
  • API key validation     → POST /api/auth/validate-key
  • Account info           → GET  /api/auth/me

Security model:
  • Raw key: autorca_live_<48 hex chars> — 192 bits entropy, shown ONCE
  • Only SHA-256 hash stored in Supabase — raw key never persisted
  • Login: submit raw key → server hashes it → DB lookup → match = access

Place this file in the same directory as api_server.py (project root).
api_server.py mounts this router automatically on startup.
"""

import hashlib
import logging
import re
import secrets
from datetime import datetime, timezone  # ← timezone not UTC (3.10 compat)

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

logger = logging.getLogger("AutoRCA.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ══════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ══════════════════════════════════════════════════════════════════

class RegisterRequest(BaseModel):
    org_name:     str           = Field(..., min_length=2, max_length=120)
    email:        EmailStr
    plan:         str           = Field(default="free")
    account_type: str           = Field(default="biz")
    website:      str | None    = Field(default=None, max_length=255)
    ref_code:     str | None    = Field(default=None, max_length=50)


class ValidateKeyRequest(BaseModel):
    api_key: str = Field(..., min_length=10, max_length=200)


# ══════════════════════════════════════════════════════════════════
# KEY UTILITIES
# ══════════════════════════════════════════════════════════════════

def _generate_raw_key() -> str:
    """autorca_live_<48 hex chars>  — 61 chars total, 192 bits entropy."""
    return "autorca_live_" + secrets.token_hex(24)


def _hash_key(raw_key: str) -> str:
    """SHA-256 hex digest — the only thing stored in the DB."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _key_prefix(raw_key: str) -> str:
    """First 20 chars + '...' for safe display (cannot reconstruct key)."""
    return raw_key[:20] + "..."


def _valid_key_format(key: str) -> bool:
    """Format gate — skip DB lookup on obviously invalid input."""
    return bool(re.match(r"^autorca_(live|test)_[a-f0-9]{48}$", key))


# ══════════════════════════════════════════════════════════════════
# SUPABASE HELPER — lazy import avoids circular dependency
# ══════════════════════════════════════════════════════════════════

def _get_sb():
    """Return the Supabase client from api_server, or None if not configured."""
    try:
        import api_server
        return api_server._sb
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════

@router.post("/register", summary="Create organisation and generate API key")
async def register(body: RegisterRequest, request: Request):
    sb = _get_sb()
    if sb is None:
        raise HTTPException(
            status_code=503,
            detail="Database not configured. Set SUPABASE_URL and SUPABASE_KEY in .env"
        )

    # 1. Normalise inputs
    email    = body.email.lower().strip()
    org_name = body.org_name.strip()
    plan     = body.plan if body.plan in ("free", "pro") else "free"
    acc_type = body.account_type if body.account_type in ("biz", "ind") else "biz"

    # 2. Duplicate email check
    try:
        existing = sb.table("organizations").select("id").eq("email", email).execute()
        if existing.data:
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists. Please sign in instead."
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[register] DB check failed: {e}")
        raise HTTPException(status_code=500, detail="Database error during registration.")

    # 3. Insert organisation row
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
        logger.info(f"[register] Created org '{org_name}' ({org_id}) email={email}")
    except Exception as e:
        logger.error(f"[register] Failed to create org: {e}")
        raise HTTPException(status_code=500, detail="Failed to create organisation.")  # noqa: B904

    # 4. Generate key — store HASH only, never raw
    raw_key  = _generate_raw_key()
    key_hash = _hash_key(raw_key)
    prefix   = _key_prefix(raw_key)

    try:
        sb.table("api_keys").insert({
            "org_id":     org_id,
            "key_hash":   key_hash,
            "key_prefix": prefix,
            "label":      "Default Key",
            "is_active":  True,
        }).execute()
        logger.info(f"[register] API key created for org {org_id} prefix={prefix}")
    except Exception as e:
        # Roll back org row to prevent orphan records
        try:
            sb.table("organizations").delete().eq("id", org_id).execute()
        except Exception:
            pass
        logger.error(f"[register] Failed to store key hash: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate API key.")  # noqa: B904

    # 5. Return raw key ONCE — never stored, never retrievable again
    return {
        "org_id":   org_id,
        "org_name": org_name,
        "email":    email,
        "plan":     plan,
        "api_key":  raw_key,
        "message":  "Registration successful. Save your API key — it will not be shown again.",
    }


@router.post("/validate-key", summary="Validate API key against database")
async def validate_key(body: ValidateKeyRequest, request: Request):
    sb = _get_sb()
    if sb is None:
        raise HTTPException(
            status_code=503,
            detail="Database not configured. Set SUPABASE_URL and SUPABASE_KEY in .env"
        )

    raw_key = body.api_key.strip()

    # 1. Format gate — reject garbage before hitting the DB
    if not _valid_key_format(raw_key):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key format. Keys look like autorca_live_xxxxxxxx…"
        )

    # 2. Hash and lookup
    key_hash = _hash_key(raw_key)
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
        if "PGRST116" in err_str or "JSON object requested" in err_str or "No rows" in err_str.lower():
            raise HTTPException(status_code=401, detail="Invalid or inactive API key.")
        logger.error(f"[validate-key] DB lookup error: {e}")
        raise HTTPException(status_code=500, detail="Database error during key validation.")

    if not result.data:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key.")

    key_row = result.data
    org     = key_row.get("organizations") or {}

    # 3. Active checks
    if not key_row.get("is_active", False):
        raise HTTPException(status_code=401, detail="This API key has been deactivated.")
    if org.get("status") != "active":
        raise HTTPException(status_code=403, detail="Your account has been suspended. Contact support.")

    # 4. Update last_used_at (best-effort)
    try:
        sb.table("api_keys").update({
            "last_used_at": datetime.now(timezone.utc).isoformat()  # ← timezone.utc not UTC
        }).eq("id", key_row["id"]).execute()
    except Exception as e:
        logger.warning(f"[validate-key] Could not update last_used_at: {e}")

    logger.info(f"[validate-key] Successful login org={org.get('id')} plan={org.get('plan')}")

    return {
        "valid":    True,
        "org_id":   org.get("id"),
        "org_name": org.get("org_name"),
        "email":    org.get("email"),
        "plan":     org.get("plan", "free"),
    }


@router.get("/me", summary="Get current account info from API key")
async def get_me(request: Request):
    """Used by dashboard to show logged-in org name and plan badge."""
    raw_key = request.headers.get("X-API-Key", "").strip()
    if not raw_key:
        raise HTTPException(status_code=401, detail="X-API-Key header required.")
    return await validate_key(ValidateKeyRequest(api_key=raw_key), request)