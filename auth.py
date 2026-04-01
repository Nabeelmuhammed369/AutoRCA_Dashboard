"""
auth.py — AutoRCA Authentication Module
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Handles:
  • Organization creation  → POST /api/auth/register
  • API key validation     → POST /api/auth/validate-key
  • Key rotation           → POST /api/auth/rotate-key     (future)
  • Account info           → GET  /api/auth/me             (future)

Security model:
  • Raw key is generated with secrets.token_hex(24) — 192 bits of entropy
  • Key format: autorca_live_<48 hex chars>  (total 61 chars)
  • ONLY the SHA-256 hash is stored in Supabase — raw key is returned
    exactly once at registration and never stored anywhere
  • Login sends the raw key → server hashes it → lookup in DB
  • No passwords, no sessions — the key IS the credential

Supabase tables required (run SQL in Step 1 below):
  organizations   — tenant records
  api_keys        — hashed keys linked to orgs
"""

import hashlib
import logging
import re
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

logger = logging.getLogger("AutoRCA.auth")

# ── Router (mounted in api_server.py) ────────────────────────────────────────
router = APIRouter(prefix="/api/auth", tags=["auth"])


# ══════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ══════════════════════════════════════════════════════════════════


class RegisterRequest(BaseModel):
    org_name: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    plan: str = Field(default="free")
    account_type: str = Field(default="biz")
    website: str | None = Field(default=None, max_length=255)
    ref_code: str | None = Field(default=None, max_length=50)


class ValidateKeyRequest(BaseModel):
    api_key: str = Field(..., min_length=10, max_length=200)


# ══════════════════════════════════════════════════════════════════
# KEY UTILITIES
# ══════════════════════════════════════════════════════════════════


def _generate_raw_key() -> str:
    """
    Generate a raw API key.
    Format: autorca_live_<48 hex chars>
    Example: autorca_live_a3f9b2c1d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3
    Total length: 61 characters
    Entropy: 192 bits (48 hex = 24 bytes = 192 bits)
    """
    return "autorca_live_" + secrets.token_hex(24)


def _hash_key(raw_key: str) -> str:
    """
    SHA-256 hash of the raw key.
    This is what gets stored in the database.
    Lookup: hash(submitted_key) == stored_hash
    """
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _key_prefix(raw_key: str) -> str:
    """
    First 20 chars for display purposes (e.g. 'autorca_live_a3f9b2c1')
    Never enough to reconstruct the key.
    """
    return raw_key[:20] + "..."


def _valid_key_format(key: str) -> bool:
    """
    Quick format check before hitting the database.
    Avoids unnecessary DB lookups on obviously wrong input.
    """
    return bool(re.match(r"^autorca_(live|test)_[a-f0-9]{48}$", key))


# ══════════════════════════════════════════════════════════════════
# SUPABASE HELPER
# Returns the _sb client imported from api_server context
# We import lazily to avoid circular import at module load time
# ══════════════════════════════════════════════════════════════════


def _get_sb():
    """
    Lazily fetch the Supabase client from api_server.
    Returns None if Supabase is not configured.
    """
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
    """
    ── FLOW ──────────────────────────────────────────────────────
    1. Validate email not already registered
    2. Insert organisation row
    3. Generate raw key + SHA-256 hash
    4. Insert api_keys row (stores HASH only)
    5. Return raw key to caller — THIS IS THE ONLY TIME IT'S VISIBLE
    ──────────────────────────────────────────────────────────────

    ── RESPONSE ──────────────────────────────────────────────────
    {
      "org_id":   "<uuid>",
      "org_name": "Acme DevOps",
      "api_key":  "autorca_live_a3f9b2...",  ← shown once only
      "plan":     "free",
      "message":  "Save this key. It will not be shown again."
    }
    """
    sb = _get_sb()

    if sb is None:
        raise HTTPException(
            status_code=503, detail="Database not configured. Set SUPABASE_URL and SUPABASE_KEY in .env"
        )

    # ── 1. Normalise inputs ────────────────────────────────────────
    email = body.email.lower().strip()
    org_name = body.org_name.strip()
    plan = body.plan if body.plan in ("free", "pro") else "free"
    acc_type = body.account_type if body.account_type in ("biz", "ind") else "biz"

    # ── 2. Check duplicate email ───────────────────────────────────
    try:
        existing = sb.table("organizations").select("id").eq("email", email).execute()
        if existing.data:
            raise HTTPException(
                status_code=409, detail="An account with this email already exists. Please sign in instead."
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[register] DB check failed: {e}")
        raise HTTPException(status_code=500, detail="Database error during registration.")  # noqa: B904

    # ── 3. Insert organisation ────────────────────────────────────
    try:
        org_result = (
            sb.table("organizations")
            .insert(
                {
                    "org_name": org_name,
                    "email": email,
                    "account_type": acc_type,
                    "plan": plan,
                    "website": body.website,
                    "ref_code": body.ref_code,
                    "status": "active",
                }
            )
            .execute()
        )
        org_id = org_result.data[0]["id"]
        logger.info(f"[register] Created org '{org_name}' ({org_id}) email={email}")
    except Exception as e:
        logger.error(f"[register] Failed to create org: {e}")
        raise HTTPException(status_code=500, detail="Failed to create organisation.")  # noqa: B904

    # ── 4. Generate key and store hash ────────────────────────────
    raw_key = _generate_raw_key()
    key_hash = _hash_key(raw_key)
    prefix = _key_prefix(raw_key)

    try:
        sb.table("api_keys").insert(
            {
                "org_id": org_id,
                "key_hash": key_hash,
                "key_prefix": prefix,
                "label": "Default Key",
                "is_active": True,
            }
        ).execute()
        logger.info(f"[register] API key created for org {org_id} prefix={prefix}")
    except Exception as e:
        # Roll back the org row if key creation fails
        try:
            sb.table("organizations").delete().eq("id", org_id).execute()
        except Exception:
            pass
        logger.error(f"[register] Failed to store key hash: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate API key.")  # noqa: B904

    # ── 5. Return raw key exactly once ───────────────────────────
    return {
        "org_id": org_id,
        "org_name": org_name,
        "email": email,
        "plan": plan,
        "api_key": raw_key,  # ← SHOWN ONCE — never stored raw
        "message": "Registration successful. Save your API key — it will not be shown again.",
    }


@router.post("/validate-key", summary="Validate API key against database")
async def validate_key(body: ValidateKeyRequest, request: Request):
    """
    ── FLOW ──────────────────────────────────────────────────────
    1. Quick format check (avoid DB hit on obvious garbage)
    2. Hash the submitted key
    3. Lookup hash in api_keys table
    4. Fetch linked organisation
    5. Update last_used_at timestamp
    6. Return org info to client
    ──────────────────────────────────────────────────────────────

    ── RESPONSE (valid) ──────────────────────────────────────────
    {
      "valid":    true,
      "org_id":   "<uuid>",
      "org_name": "Acme DevOps",
      "plan":     "free",
      "email":    "admin@acme.com"
    }

    ── RESPONSE (invalid) → HTTP 401 ─────────────────────────────
    { "detail": "Invalid or inactive API key." }
    """
    sb = _get_sb()

    if sb is None:
        raise HTTPException(
            status_code=503, detail="Database not configured. Set SUPABASE_URL and SUPABASE_KEY in .env"
        )

    raw_key = body.api_key.strip()

    # ── 1. Format gate — don't hit DB on garbage input ─────────────
    if not _valid_key_format(raw_key):
        raise HTTPException(status_code=401, detail="Invalid API key format. Keys look like autorca_live_xxxxxxxx…")

    # ── 2. Hash the submitted key ──────────────────────────────────
    key_hash = _hash_key(raw_key)

    # ── 3. Lookup in database ──────────────────────────────────────
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
        # supabase-py raises an exception when .single() finds 0 rows
        if "PGRST116" in err_str or "JSON object requested" in err_str or "No rows" in err_str.lower():
            raise HTTPException(status_code=401, detail="Invalid or inactive API key.")  # noqa: B904
        logger.error(f"[validate-key] DB lookup error: {e}")
        raise HTTPException(status_code=500, detail="Database error during key validation.")  # noqa: B904

    if not result.data:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key.")

    key_row = result.data
    org = key_row.get("organizations") or {}

    # ── 4. Check key and org are active ───────────────────────────
    if not key_row.get("is_active", False):
        raise HTTPException(status_code=401, detail="This API key has been deactivated.")

    if org.get("status") != "active":
        raise HTTPException(status_code=403, detail="Your account has been suspended. Contact support.")

    # ── 5. Update last_used_at (best-effort, don't fail login) ─────
    try:
        sb.table("api_keys").update({"last_used_at": datetime.now(UTC).isoformat()}).eq("id", key_row["id"]).execute()
    except Exception as e:
        logger.warning(f"[validate-key] Could not update last_used_at: {e}")

    logger.info(f"[validate-key] Successful login org={org.get('id')} plan={org.get('plan')}")

    # ── 6. Return org info ─────────────────────────────────────────
    return {
        "valid": True,
        "org_id": org.get("id"),
        "org_name": org.get("org_name"),
        "email": org.get("email"),
        "plan": org.get("plan", "free"),
    }


@router.get("/me", summary="Get current account info from API key")
async def get_me(request: Request):
    """
    Reads the X-API-Key header, validates it, and returns org info.
    Used by the dashboard to display the logged-in org name and plan badge.
    """
    raw_key = request.headers.get("X-API-Key", "").strip()
    if not raw_key:
        raise HTTPException(status_code=401, detail="X-API-Key header required.")

    # Reuse validate logic
    result = await validate_key(ValidateKeyRequest(api_key=raw_key), request)
    return result
