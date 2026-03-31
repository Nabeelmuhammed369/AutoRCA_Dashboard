"""
api_server.py — AutoRCA FastAPI Backend v3.2
─────────────────────────────────────────────────────────────────
LOCAL:   python api_server.py           → http://localhost:8000
RENDER:  uvicorn api_server:app ...     → https://autorca-api.onrender.com

v3.2 fixes:
  • WebSocket /ws/logs endpoint added (was missing entirely)
  • RCASavePayload fields aligned with what the dashboard actually sends
  • verify_api_key returns 401 (not 500) and allows dev-mode (no key = pass-through)
  • CORS allows ALL localhost / 127.0.0.1 origins on any port
  • WS origin check accepts any localhost/127.0.0.1 origin
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from typing import Optional

import requests as _requests
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

load_dotenv(".env")
load_dotenv("app.env")

IS_RENDER = os.getenv("IS_RENDER", "false").lower() == "true"

# ── Config (yaml, optional) ───────────────────────────────────────────────────
config: dict = {}
try:
    import yaml

    if os.path.exists("config.yaml"):
        with open("config.yaml") as f:
            config = yaml.safe_load(f) or {}
except Exception:
    pass

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("AutoRCA")

# ── Local modules (optional — not on Render) ──────────────────────────────────
_local_ok = False
try:
    from Core.ai_analyzer import explain_incident, generate_ticket_summary, suggest_fixes
    from Core.rca_engine import classify_issue
    from Monitors.api_monitor import check_api_health
    from Monitors.db_validator import validate_data
    from Monitors.log_analyzer import analyze_logs

    _local_ok = True
    logger.info("Local modules loaded — running in full mode.")
except ImportError:
    logger.info("Local modules not found — running in cloud/integration mode.")

# ── Supabase (optional) ───────────────────────────────────────────────────────
_sb = None
try:
    from supabase import create_client

    _sb_url = os.getenv("SUPABASE_URL", "")
    _sb_key = os.getenv("SUPABASE_KEY", "")
    if _sb_url and _sb_key:
        _sb = create_client(_sb_url, _sb_key)
        logger.info("Supabase client initialised.")
    else:
        logger.info("SUPABASE_URL/KEY not set — RCA history persistence disabled.")
except ImportError:
    logger.info("supabase package not installed — history persistence disabled.")
except Exception as e:
    logger.warning(f"Supabase init failed: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# AUTH  — FIX 1: don't 500 when key missing; treat unset key as dev mode
# ══════════════════════════════════════════════════════════════════════════════
AUTORCA_API_KEY = os.getenv("AUTORCA_API_KEY", "")
if not AUTORCA_API_KEY:
    logger.warning("AUTORCA_API_KEY not set — running in DEV mode (all requests allowed).")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(key: str = Depends(api_key_header)):
    """
    FIX: If AUTORCA_API_KEY is not set → dev mode, allow everything.
    If it IS set, the header key must match. Returns 401 (not 500) on mismatch.
    """
    if not AUTORCA_API_KEY:
        return ""  # dev mode — no key required
    if key != AUTORCA_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")
    return key


def ok_key(k: str) -> bool:
    """For WebSocket and inline key checks (no Depends)."""
    if not AUTORCA_API_KEY:
        return True
    return k == AUTORCA_API_KEY


# ── Rate limiter ───────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

# ══════════════════════════════════════════════════════════════════════════════
# CORS — allow all origins. Security is handled by AUTORCA_API_KEY auth.
# Using ["*"] with allow_origin_regex together causes Starlette to silently
# drop the Access-Control-Allow-Origin header — so we use ["*"] only.
# ══════════════════════════════════════════════════════════════════════════════

# ── App lifespan (replaces deprecated @app.on_event("startup")) ───────────────
@asynccontextmanager
async def _lifespan(app):
    logger.info("=" * 58)
    logger.info("  AutoRCA API v3.3  →  http://0.0.0.0:8000")
    logger.info("=" * 58)
    logger.info(f"  Local modules : {'✓ loaded' if _local_ok else '✗ cloud-mode'}")
    logger.info(f"  Supabase      : {'✓ connected' if _sb else '✗ not configured'}")
    logger.info(f"  Auth          : {'✓ key set' if AUTORCA_API_KEY else '⚠ DEV MODE (no key required)'}")
    logger.info("  CORS          : ✓ allow all origins (auth via API key)")
    logger.info("  WebSocket     : ✓ /ws/logs  (key via ?key= query param)")
    logger.info("=" * 58)
    yield


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="AutoRCA API", version="3.3.0", docs_url=None, redoc_url=None, lifespan=_lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=600,
)


# ══════════════════════════════════════════════════════════════════════════════
# MODELS
# ══════════════════════════════════════════════════════════════════════════════
class AIRequest(BaseModel):
    classification: str = Field(default="unknown", max_length=100)
    exceptions: list = Field(default_factory=list)
    api_result: dict = Field(default_factory=dict)
    db_result: dict = Field(default_factory=dict)
    model_config = {"extra": "allow"}


class LokiRequest(BaseModel):
    url: str = Field(..., max_length=500)
    query: str = Field(default='{job="autorca"}', max_length=500)
    hours: int = Field(default=1, ge=1, le=168)
    limit: int = Field(default=5000, ge=1, le=50000)


class ElasticRequest(BaseModel):
    url: str = Field(..., max_length=500)
    index: str = Field(default="app-logs", max_length=200)
    query: str = Field(default="level:ERROR OR level:CRITICAL", max_length=500)
    limit: int = Field(default=5000, ge=1, le=50000)


class S3Request(BaseModel):
    endpoint: str = Field(..., max_length=500)
    bucket: str = Field(..., max_length=200)
    key: str = Field(..., max_length=500)
    access_key: str = Field(default="", max_length=200)
    secret_key: str = Field(default="", max_length=200)


class HttpRequest(BaseModel):
    url: str = Field(..., max_length=500)
    method: str = Field(default="GET", pattern="^(GET|POST)$")
    headers: dict = Field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════════════
# RCA SAVE PAYLOAD  — FIX 3: field names aligned to what dashboard sends
# ══════════════════════════════════════════════════════════════════════════════
class RCASavePayload(BaseModel):
    # ── Fields the dashboard _actually_ sends (from _doSaveRCA) ──────────────
    source_name: str = Field(default="Unknown Source")  # dashboard primary field
    severity: str = Field(default="warning")
    total_entries: int = Field(default=0)
    error_count: int = Field(default=0)
    warn_count: int = Field(default=0)
    error_rate: float = Field(default=0.0)
    ai_summary: str = Field(default="")
    fix_steps: str = Field(default="")
    incident_groups: list = Field(default_factory=list)
    affected_services: list = Field(default_factory=list)
    remediation: list = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)

    # ── Legacy / alternate field names (kept for backward compat) ─────────────
    source: Optional[str] = Field(default=None)  # old name
    summary: Optional[str] = Field(default=None)  # old name
    total_logs: Optional[int] = Field(default=None)  # old name
    totalLogs: Optional[int] = Field(default=None)
    errCount: Optional[int] = Field(default=None)
    errorCount: Optional[int] = Field(default=None)
    classification: Optional[str] = Field(default=None)
    meta: Optional[dict] = Field(default=None)

    model_config = {"extra": "allow"}

    def to_db_row(self) -> dict:
        """Map to the actual Supabase rca_history table columns."""
        _sev_map = {
            "critical": "critical",
            "error": "critical",
            "warning": "warning",
            "warn": "warning",
            "healthy": "healthy",
            "ok": "healthy",
        }
        sev = _sev_map.get((self.severity or "warning").lower(), "warning").lower()

        return {
            "source_name": self.source_name or self.source or "Unknown Source",
            "severity": sev,
            "total_entries": self.total_entries or self.total_logs or self.totalLogs or 0,
            "error_count": self.error_count or self.errCount or self.errorCount or 0,
            "warn_count": self.warn_count or 0,
            "error_rate": float(self.error_rate or 0),
            "ai_summary": self.ai_summary or self.summary or "",
            "fix_steps": self.fix_steps or "",
            "incident_groups": self.incident_groups or [],
            "affected_services": self.affected_services or [],
            "remediation": self.remediation or [],
            "stats": self.stats or self.meta or {},
        }


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def _logs_to_text(lines: list) -> str:
    return "\n".join(str(ln) for ln in lines if ln)


# Regex that parses the most common log line formats:
#   2026-03-08 10:00:00 ERROR [Database] message …
#   2026-03-08T10:00:00Z ERROR source - message …
#   ERROR source message …  (no timestamp)
_LOG_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?\s*"
    r"(?P<level>DEBUG|INFO|NOTICE|WARNING|WARN|ERROR|CRITICAL|FATAL|SEVERE)\s+"
    r"(?:\[(?P<source>[^\]]+)\]\s*)?"
    r"(?P<message>.+)$",
    re.IGNORECASE,
)


def _parse_line(raw: str) -> dict:
    """Parse a single raw log line into a dict with level/source/message/timestamp fields."""
    m = _LOG_RE.match(raw.strip())
    if m:
        lvl = m.group("level").upper()
        # Normalise WARN → WARNING, FATAL/SEVERE → CRITICAL
        lvl = {"WARN": "WARNING", "FATAL": "CRITICAL", "SEVERE": "CRITICAL"}.get(lvl, lvl)
        return {
            "raw": raw,
            "timestamp": m.group("ts") or "",
            "level": lvl,
            "source": m.group("source") or "unknown",
            "message": m.group("message") or raw,
        }
    # Fallback — can't parse structure, but still tag severity from keywords
    upper = raw.upper()
    if "CRITICAL" in upper or "FATAL" in upper:
        lvl = "CRITICAL"
    elif "ERROR" in upper:
        lvl = "ERROR"
    elif "WARN" in upper:
        lvl = "WARNING"
    elif "DEBUG" in upper:
        lvl = "DEBUG"
    else:
        lvl = "INFO"
    return {"raw": raw, "timestamp": "", "level": lvl, "source": "unknown", "message": raw}


def _build_stats(parsed_lines: list) -> tuple:
    """Compute stats dict + severity classification from a list of parsed line dicts."""
    total = len(parsed_lines)
    errors = [d for d in parsed_lines if d["level"] in ("ERROR",)]
    criticals = [d for d in parsed_lines if d["level"] == "CRITICAL"]
    warnings = [d for d in parsed_lines if d["level"] == "WARNING"]

    err_count = len(errors) + len(criticals)
    err_rate = round(err_count / max(total, 1) * 100, 1)

    classify = (
        "critical"
        if len(criticals) > 0
        else "high"
        if err_rate > 50
        else "medium"
        if err_rate > 20
        else "low"
        if len(errors) > 0
        else "healthy"
    )

    raw_lines = [d["raw"] for d in parsed_lines]
    exceptions = [d["raw"] for d in parsed_lines if d["level"] in ("ERROR", "CRITICAL")][:100]

    log_result = {
        "total": total,
        "err": err_count,
        "critical": len(criticals),
        "warn": len(warnings),
        "errorRate": f"{err_rate:.1f}",
        "exceptions": exceptions,
        "total_errors": err_count,
        "total_warnings": len(warnings),
        "formats": ["plain"],
        "top_sources": [],
        "has_stacktrace": any("at " in d["raw"] or "Traceback" in d["raw"] for d in parsed_lines),
    }
    return log_result, classify, raw_lines


def _parse_and_respond(raw_text: str):
    raw_lines = [ln for ln in raw_text.splitlines() if ln.strip()]
    parsed_lines = [_parse_line(ln) for ln in raw_lines]
    total = len(parsed_lines)

    if _local_ok:
        import pandas as pd

        # Build a fully structured DataFrame so analyze_logs never hits a KeyError.
        # The DataFrame has all columns the local module expects: raw, level,
        # timestamp, source, message.
        df = (
            pd.DataFrame(parsed_lines)
            if parsed_lines
            else pd.DataFrame(columns=["raw", "level", "timestamp", "source", "message"])
        )
        try:
            log_result = analyze_logs(df)
            classify = classify_issue({"status": "ok"}, log_result, {"valid": True, "row_count": total})
            log_result["exceptions"] = [d["raw"] for d in parsed_lines if d["level"] in ("ERROR", "CRITICAL")][:100]
        except Exception as exc:
            # analyze_logs failed (e.g. unexpected column set) — fall back to
            # our own stats so the user still gets a useful response.
            logger.warning(f"analyze_logs raised {exc!r} — falling back to built-in stats")
            log_result, classify, _ = _build_stats(parsed_lines)
    else:
        log_result, classify, _ = _build_stats(parsed_lines)

    return JSONResponse(
        content={
            "source": "integration",
            "lines_fetched": total,
            "logs": log_result,
            "classification": classify,
            "raw_sample": raw_lines[:5000],
        }
    )


def _read_exception_lines(log_file: str, max_lines: int = 100) -> list:
    if not log_file or not os.path.exists(log_file):
        return []
    try:
        with open(log_file) as f:
            lines = f.readlines()
        return [ln.strip() for ln in lines if "ERROR" in ln or "CRITICAL" in ln][-max_lines:]
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════


@app.get("/api/health")
def health():
    db_path = config.get("database", {}).get("path", "")
    log_file = config.get("log", {}).get("file", "")
    db_ok = bool(db_path) and os.path.exists(db_path)
    log_ok = bool(log_file) and os.path.exists(log_file)
    key_ok = bool(AUTORCA_API_KEY)
    return {
        "status": "ok",
        "version": "3.2.0",
        "mode": "cloud" if IS_RENDER else "local",
        "dev_mode": not key_ok,
        "checks": {
            "api_key_set": "ok" if key_ok else "dev-mode (not required)",
            "database_file": "n/a" if IS_RENDER else ("ok" if db_ok else "missing"),
            "log_file": "n/a" if IS_RENDER else ("ok" if log_ok else "missing"),
            "local_modules": "loaded" if _local_ok else "cloud-mode",
            "supabase": "connected" if _sb else "not configured",
            "websocket": "ok — /ws/logs",
        },
    }


@app.get("/api/run", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
def run_diagnostic(request: Request):
    if not _local_ok:
        raise HTTPException(503, "Local analysis only available when running locally.")
    log_file = config.get("log", {}).get("file", "")
    api_url = config.get("api", {}).get("url", "")
    api_timeout = config.get("api", {}).get("timeout", 5)
    db_path = config.get("database", {}).get("path", "")
    api_result = check_api_health(api_url, api_timeout)
    log_result = analyze_logs(log_file)
    db_result = validate_data(db_path)
    classify = classify_issue(api_result, log_result, db_result)
    log_result["exceptions"] = _read_exception_lines(log_file)
    return JSONResponse(
        content={
            "api": api_result,
            "logs": log_result,
            "db": db_result,
            "classification": classify,
        }
    )


@app.get("/api/simulate/db-crash", dependencies=[Depends(verify_api_key)])
@limiter.limit("5/minute")
def simulate_db_crash(request: Request):
    log_file = config.get("log", {}).get("file", "")
    if not log_file:
        raise HTTPException(503, "Log file not configured (local mode only).")
    try:
        with open(log_file, "a") as f:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"\n{ts} ERROR [Database] DB_CONN_FAIL: Connection refused by host")
        return {"status": "injected"}
    except Exception as e:
        raise HTTPException(500, str(e)) from e


# ── AI endpoints ───────────────────────────────────────────────────────────────
@app.post("/api/ai/explain", dependencies=[Depends(verify_api_key)])
@limiter.limit("5/minute")
def ai_explain(req: AIRequest, request: Request):
    if not _local_ok:
        raise HTTPException(503, "AI module not available in cloud mode.")
    return JSONResponse(content=explain_incident(req.classification, req.exceptions, req.api_result, req.db_result))


@app.post("/api/ai/fix-steps", dependencies=[Depends(verify_api_key)])
@limiter.limit("5/minute")
def ai_fix_steps(req: AIRequest, request: Request):
    if not _local_ok:
        raise HTTPException(503, "AI module not available in cloud mode.")
    return JSONResponse(content=suggest_fixes(req.classification, req.exceptions, req.api_result, req.db_result))


@app.post("/api/ai/ticket-summary", dependencies=[Depends(verify_api_key)])
@limiter.limit("5/minute")
def ai_ticket_summary(req: AIRequest, request: Request):
    if not _local_ok:
        raise HTTPException(503, "AI module not available in cloud mode.")
    return JSONResponse(
        content=generate_ticket_summary(req.classification, req.exceptions, req.api_result, req.db_result)
    )


# ── Integration endpoints ──────────────────────────────────────────────────────
@app.post("/api/integration/loki", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
def fetch_loki(req: LokiRequest, request: Request):
    now_ns = int(time.time() * 1e9)
    start_ns = int((time.time() - req.hours * 3600) * 1e9)
    base = re.sub(r"/loki(?:/.*)?$", "", req.url.rstrip("/"))
    endpoint = f"{base}/loki/api/v1/query_range"
    try:
        resp = _requests.get(
            endpoint, params={"query": req.query, "start": start_ns, "end": now_ns, "limit": req.limit}, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        lines = [msg for stream in data.get("data", {}).get("result", []) for _ts, msg in stream.get("values", [])]
        if not lines:
            raise HTTPException(
                404,
                f"No log entries returned from Loki at {endpoint}. Try a broader LogQL query or extend the time range.",
            )
        return _parse_and_respond(_logs_to_text(lines))
    except HTTPException:
        raise  # re-raise our own 404/502 — don't wrap in 500
    except _requests.exceptions.ConnectionError as e:
        raise HTTPException(
            502, f"Cannot connect to Loki at {base}. Is mock_log_server.py running?  (python mock_log_server.py)"
        ) from e
    except _requests.exceptions.Timeout as e:
        raise HTTPException(504, f"Loki request timed out after 15 s at {endpoint}.") from e
    except _requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        raise HTTPException(502, f"Loki returned HTTP {status}: {e}") from e
    except Exception as e:
        logger.exception("Unexpected error in fetch_loki")
        raise HTTPException(500, f"Unexpected error fetching Loki logs: {e}") from e


@app.post("/api/integration/elasticsearch", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
def fetch_elasticsearch(req: ElasticRequest, request: Request):
    body = {
        "query": {"query_string": {"query": req.query}},
        "size": req.limit,
        "sort": [{"@timestamp": {"order": "desc"}}],
    }
    try:
        resp = _requests.post(
            f"{req.url.rstrip('/')}/{req.index}/_search",
            json=body,
            timeout=15,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
        lines = [h["_source"].get("message") or str(h["_source"]) for h in hits]
        if not lines:
            raise HTTPException(404, "No documents found. Check index name and query.")
        return _parse_and_respond(_logs_to_text(lines))
    except HTTPException:
        raise
    except _requests.exceptions.ConnectionError as e:
        raise HTTPException(502, f"Cannot connect to Elasticsearch at {req.url}.") from e
    except _requests.exceptions.Timeout as e:
        raise HTTPException(504, "Elasticsearch request timed out.") from e
    except _requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        raise HTTPException(502, f"Elasticsearch returned HTTP {status}: {e}") from e
    except Exception as e:
        logger.exception("Unexpected error in fetch_elasticsearch")
        raise HTTPException(500, f"Unexpected error: {e}") from e


@app.post("/api/integration/s3", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
def fetch_s3(req: S3Request, request: Request):
    url = f"{req.endpoint.rstrip('/')}/{req.bucket}/{req.key.lstrip('/')}"
    try:
        resp = _requests.get(url, timeout=30)
        resp.raise_for_status()
        return _parse_and_respond(resp.text)
    except HTTPException:
        raise
    except _requests.exceptions.ConnectionError as e:
        raise HTTPException(502, f"Cannot connect to S3 endpoint at {req.endpoint}.") from e
    except _requests.exceptions.Timeout as e:
        raise HTTPException(504, "S3 request timed out after 30 s.") from e
    except _requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        raise HTTPException(502, f"S3 returned HTTP {status}. Check bucket and key.") from e
    except Exception as e:
        logger.exception("Unexpected error in fetch_s3")
        raise HTTPException(500, f"Unexpected error: {e}") from e


@app.post("/api/integration/http", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
def fetch_custom_http(req: HttpRequest, request: Request):
    try:
        fn = _requests.get if req.method == "GET" else _requests.post
        resp = fn(req.url, headers=req.headers, timeout=15)
        resp.raise_for_status()
        return _parse_and_respond(resp.text)
    except HTTPException:
        raise
    except _requests.exceptions.ConnectionError as e:
        raise HTTPException(502, f"Cannot connect to {req.url}.") from e
    except _requests.exceptions.Timeout as e:
        raise HTTPException(504, f"Request to {req.url} timed out after 15 s.") from e
    except _requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        raise HTTPException(502, f"Endpoint returned HTTP {status}: {e}") from e
    except Exception as e:
        logger.exception("Unexpected error in fetch_custom_http")
        raise HTTPException(500, f"Unexpected error: {e}") from e


# ══════════════════════════════════════════════════════════════════════════════
# RCA HISTORY  — FIX 4: to_db_row() maps dashboard fields to DB columns
# ══════════════════════════════════════════════════════════════════════════════
@app.post("/api/rca/save", dependencies=[Depends(verify_api_key)])
@limiter.limit("20/minute")
async def rca_save(payload: RCASavePayload, request: Request):
    if not _sb:
        # Message must contain "Supabase" so tests can assert on it
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": "Supabase is not configured. Set SUPABASE_URL and SUPABASE_KEY in .env"},
        )
    try:
        row = payload.to_db_row()
        result = _sb.table("rca_history").insert(row).execute()
        saved = result.data[0] if result.data else row
        return JSONResponse(content={"ok": True, "id": saved.get("id"), "record": saved})
    except Exception as e:
        logger.exception("Failed to save RCA run.")
        # Return HTTP 200 with ok:false so the dashboard can surface the error
        return JSONResponse(status_code=200, content={"ok": False, "error": str(e)})


@app.get("/api/rca/history", dependencies=[Depends(verify_api_key)])
@limiter.limit("30/minute")
async def rca_history(
    request: Request,
    severity: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
):
    if not _sb:
        return JSONResponse(
            status_code=200,
            content={"ok": True, "data": [], "count": 0, "supabase": False},
        )
    try:
        q = _sb.table("rca_history").select("*").order("created_at", desc=True)
        if severity and severity.lower() not in ("all", ""):
            q = q.eq("severity", severity.lower())
        if search:
            q = q.ilike("source_name", f"%{search}%")
        q = q.range(offset, offset + limit - 1)
        result = q.execute()
        return JSONResponse(
            content={"ok": True, "data": result.data or [], "count": len(result.data or []), "supabase": True}
        )
    except Exception as e:
        logger.warning(f"RCA history fetch failed: {e}")
        return JSONResponse(content={"ok": False, "data": [], "count": 0, "supabase": True, "error": str(e)})


@app.get("/api/rca/history/{rca_id}", dependencies=[Depends(verify_api_key)])
async def rca_get(rca_id: str, request: Request):
    if not _sb:
        raise HTTPException(503, "Supabase not configured.")
    try:
        result = _sb.table("rca_history").select("*").eq("id", rca_id).single().execute()
        if not result.data:
            raise HTTPException(404, f"RCA run {rca_id} not found.")
        return JSONResponse(content={"ok": True, "data": result.data})
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to fetch RCA run.", exc_info=True)
        return JSONResponse(status_code=200, content={"ok": False, "error": str(e)})


@app.delete("/api/rca/history/{rca_id}", dependencies=[Depends(verify_api_key)])
async def rca_delete(rca_id: str, request: Request):
    if not _sb:
        raise HTTPException(503, "Supabase not configured.")
    try:
        _sb.table("rca_history").delete().eq("id", rca_id).execute()
        return JSONResponse(content={"ok": True, "deleted_id": rca_id})
    except Exception as e:
        logger.error("Failed to delete RCA run.", exc_info=True)
        return JSONResponse(status_code=200, content={"ok": False, "error": str(e)})


# ══════════════════════════════════════════════════════════════════════════════
# WEBSOCKET  /ws/logs  — FIX 5: endpoint was completely missing
# ══════════════════════════════════════════════════════════════════════════════
#
# HOW BROWSER WS AUTH WORKS:
#   Browsers CANNOT set custom HTTP headers on WebSocket connections.
#   The API key is passed as a query param:  ?key=YOUR_KEY
#   The server reads it from Query(default="").
#
# CORS FOR WS:
#   The HTTP upgrade request carries an Origin header.
#   We accept any localhost / 127.0.0.1 origin (any port).
#   The CORS middleware above already covers the preflight;
#   we check origin again here as defence-in-depth.
#
_ACTIVE_WS: dict = {}


def _ws_origin_ok(origin: str) -> bool:
    """Accept any localhost / 127.0.0.1 origin (covers VS Code Live Server etc.)"""
    if not origin:
        return True
    return bool(re.match(r"https?://(localhost|127\.0\.0\.1)(:\d+)?$|^null$", origin))


# ── Mock log generator (replace body of _stream_loop with real file tail) ─────
_WS_LEVELS = ["INFO", "INFO", "INFO", "WARNING", "ERROR", "ERROR", "CRITICAL"]
_WS_SOURCES = ["api.gateway", "db.connector", "auth.service", "payment.service", "k8s.scheduler", "cache.redis"]
_WS_MSGS = [
    "Request completed in {ms}ms — HTTP 200",
    "DB connection pool at {pct}% capacity",
    "JWT validated for user usr_{uid}",
    "DB_CONN_FAIL: Connection refused by 10.0.0.5:5432",
    "ConnectTimeout after 30s: https://api.internal/health",
    "CRITICAL: Payment gateway unreachable after 5 retries",
    "Slow query: {ms}ms SELECT * FROM orders WHERE status='pending'",
    "SSL certificate expires in 7 days",
    "JVM Heap at 91% — GC pressure detected",
    "Cache miss rate elevated: 68% (normal <20%)",
]


def _make_live_line(i: int) -> dict:
    import random

    lvl = random.choice(_WS_LEVELS)
    src = random.choice(_WS_SOURCES)
    msg = random.choice(_WS_MSGS).format(ms=random.randint(45, 4500), uid=f"{i:04d}", pct=random.randint(40, 98))
    ts = datetime.now(UTC).isoformat()
    return {
        "timestamp": ts,
        "level": lvl,
        "source": src,
        "message": msg,
        "format": "standard",
        "raw": f"{ts} {lvl} {src} - {msg}",
    }


def _make_seek_lines(n: int) -> list:
    return [_make_live_line(i) for i in range(min(n, 300))]


@app.websocket("/ws/logs")
async def ws_logs(
    websocket: WebSocket,
    key: str = Query(default=""),
    seek: int = Query(default=0),
    file: str = Query(default=""),
):
    # 1. Origin check
    origin = websocket.headers.get("origin", "")
    if origin and not _ws_origin_ok(origin):
        await websocket.close(code=4403, reason="Origin not allowed")
        return

    # 2. API key check (from ?key= query param — headers don't work for WS)
    if not ok_key(key):
        await websocket.close(code=4401, reason="Invalid API key")
        return

    await websocket.accept()
    ws_id = id(websocket)

    # 3. Send connected confirmation
    await websocket.send_json(
        {
            "type": "connected",
            "file": file or "live-stream",
            "ts": int(time.time() * 1000),
        }
    )

    # 4. Replay N historic lines if seek > 0
    if seek > 0:
        for mock_line in _make_seek_lines(seek):
            await websocket.send_json({"type": "line", "parsed": mock_line, "ts": int(time.time() * 1000)})
            await asyncio.sleep(0.01)  # don't flood

    _ACTIVE_WS[ws_id] = {"ws": websocket, "paused": False}
    err_total = 0
    total = seek

    try:
        i = 0
        while True:
            # Non-blocking client-message read (pause / resume / seek / pong)
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=1.5)
                msg = json.loads(raw)
                t = msg.get("type", "")
                if t == "pause":
                    _ACTIVE_WS[ws_id]["paused"] = True
                if t == "resume":
                    _ACTIVE_WS[ws_id]["paused"] = False
                if t == "seek":
                    for ml in _make_seek_lines(int(msg.get("lines", 50))):
                        await websocket.send_json({"type": "line", "parsed": ml, "ts": int(time.time() * 1000)})
            except TimeoutError:
                pass  # no client message — continue streaming

            if _ACTIVE_WS.get(ws_id, {}).get("paused"):
                await asyncio.sleep(0.5)
                continue

            # Push one live log line every ~1.5 s
            line = _make_live_line(i)
            await websocket.send_json({"type": "line", "parsed": line, "ts": int(time.time() * 1000)})

            total += 1
            if line["level"] in ("ERROR", "CRITICAL"):
                err_total += 1

            # Stats summary every 10 lines
            if i % 10 == 0:
                await websocket.send_json(
                    {
                        "type": "stats",
                        "data": {
                            "total": total,
                            "err": err_total,
                            "warn": max(0, total // 5),
                            "crit": max(0, err_total // 4),
                        },
                    }
                )

            # Keep-alive ping every 30 s (20 iterations × 1.5 s)
            if i % 20 == 0:
                await websocket.send_json({"type": "ping"})

            i += 1
            await asyncio.sleep(1.5)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "msg": str(e)})
        except Exception:
            pass
    finally:
        _ACTIVE_WS.pop(ws_id, None)





# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("api_server:app", host="0.0.0.0", port=port, reload=not IS_RENDER)