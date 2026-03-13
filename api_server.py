"""
api_server.py — AutoRCA FastAPI Backend v3.1
─────────────────────────────────────────────────────────────────
Runs unchanged locally AND on Render.com.

LOCAL:   python api_server.py           → http://localhost:8000
RENDER:  uvicorn api_server:app ...     → https://autorca-api.onrender.com

Key change from v3.0: all local module imports (Core/, Monitors/)
are wrapped in a try/except so the server starts cleanly on Render
even though those local files aren't deployed there. Log integrations
(Loki, ES, S3, HTTP) work in both modes.
"""

import logging
import os
from datetime import datetime

import requests as _requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

load_dotenv(".env")
load_dotenv("app.env")

# ── Detect environment ────────────────────────────────────────────────────────
IS_RENDER = os.getenv("IS_RENDER", "false").lower() == "true"

# ── Config — optional, only present when running locally ─────────────────────
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

# ── Local modules — optional (not deployed to Render) ────────────────────────
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

# ── API Key ────────────────────────────────────────────────────────────────────
AUTORCA_API_KEY = os.getenv("AUTORCA_API_KEY", "")
if not AUTORCA_API_KEY:
    logger.warning("AUTORCA_API_KEY not set — all authenticated requests will be rejected.")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(key: str = Depends(api_key_header)):
    if not AUTORCA_API_KEY:
        raise HTTPException(status_code=500, detail="Server misconfigured: AUTORCA_API_KEY not set.")
    if key != AUTORCA_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")
    return key


# ── Rate limiter ───────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

# ── CORS ───────────────────────────────────────────────────────────────────────
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")
CORS_ORIGINS = (
    ["*"]
    if ALLOWED_ORIGIN == "*"
    else [
        ALLOWED_ORIGIN,
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:5173",
        "http://localhost:3000",
        "null",  # file:// pages send Origin: null
    ]
)
_WILDCARD = ALLOWED_ORIGIN == "*"

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="AutoRCA API",
    version="3.1.0",
    docs_url=None,
    redoc_url=None,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=not _WILDCARD,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ── Models ─────────────────────────────────────────────────────────────────────
class AIRequest(BaseModel):
    classification: str = Field(..., max_length=100)
    exceptions: list = Field(default_factory=list)
    api_result: dict = Field(default_factory=dict)
    db_result: dict = Field(default_factory=dict)


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


# ── Helpers ────────────────────────────────────────────────────────────────────
def _logs_to_text(lines: list) -> str:
    return "\n".join(str(ln) for ln in lines if ln)


def _parse_and_respond(raw_text: str):
    """
    Parse raw log text → structured AutoRCA response.
    Uses full local analysis if Core/Monitors available,
    falls back to simple regex-based parsing on Render.
    """
    import pandas as pd

    lines = [ln for ln in raw_text.splitlines() if ln.strip()]
    errors = [ln for ln in lines if "ERROR" in ln.upper()]
    criticals = [ln for ln in lines if "CRITICAL" in ln.upper()]
    warnings = [ln for ln in lines if "WARN" in ln.upper()]
    total = len(lines)

    if _local_ok:
        df = pd.DataFrame({"raw": lines})
        log_result = analyze_logs(df)
        classify = classify_issue({"status": "ok"}, log_result, {"valid": True, "row_count": total})
        log_result["exceptions"] = [ln for ln in lines if "ERROR" in ln or "CRITICAL" in ln][:100]
    else:
        # Cloud fallback — no local modules needed
        err_rate = round((len(errors) + len(criticals)) / max(total, 1) * 100, 1)
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
        log_result = {
            "total": total,
            "err": len(errors) + len(criticals),
            "critical": len(criticals),
            "warn": len(warnings),
            "errorRate": f"{err_rate:.1f}",
            "exceptions": (errors + criticals)[:100],
            "total_errors": len(errors) + len(criticals),
            "total_warnings": len(warnings),
            "formats": ["plain"],
            "top_sources": [],
            "has_stacktrace": any("at " in ln or "Traceback" in ln for ln in lines),
        }

    return JSONResponse(
        content={
            "source": "integration",
            "lines_fetched": total,
            "logs": log_result,
            "classification": classify,
            "raw_sample": lines[:20],
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


# ══════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════


# ── Health ─────────────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    """Public health check — no auth. Used by Render and CI."""
    db_path = config.get("database", {}).get("path", "")
    log_file = config.get("log", {}).get("file", "")
    db_ok = bool(db_path) and os.path.exists(db_path)
    log_ok = bool(log_file) and os.path.exists(log_file)
    key_ok = bool(AUTORCA_API_KEY)
    healthy = key_ok and (IS_RENDER or (db_ok and log_ok))
    return {
        "status": "ok" if healthy else "degraded",
        "version": "3.1.0",
        "mode": "cloud" if IS_RENDER else "local",
        "checks": {
            "api_key_set": "ok" if key_ok else "not set",
            "database_file": "n/a" if IS_RENDER else ("ok" if db_ok else "missing"),
            "log_file": "n/a" if IS_RENDER else ("ok" if log_ok else "missing"),
            "local_modules": "loaded" if _local_ok else "not available (cloud mode)",
        },
    }


# ── Local diagnostics ──────────────────────────────────────────────────────────
@app.get("/api/run", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
def run_diagnostic(request: Request):
    if not _local_ok:
        raise HTTPException(
            status_code=503,
            detail="Local analysis is only available when running locally. Use log integrations instead.",
        )
    log_file = config.get("log", {}).get("file", "")
    api_url = config.get("api", {}).get("url", "")
    api_timeout = config.get("api", {}).get("timeout", 5)
    db_path = config.get("database", {}).get("path", "")
    api_result = check_api_health(api_url, api_timeout)
    log_result = analyze_logs(log_file)
    db_result = validate_data(db_path)
    classification = classify_issue(api_result, log_result, db_result)
    log_result["exceptions"] = _read_exception_lines(log_file)
    return JSONResponse(
        content={
            "api": api_result,
            "logs": log_result,
            "db": db_result,
            "classification": classification,
        }
    )


@app.get("/api/simulate/db-crash", dependencies=[Depends(verify_api_key)])
@limiter.limit("5/minute")
def simulate_db_crash(request: Request):
    log_file = config.get("log", {}).get("file", "")
    if not log_file:
        raise HTTPException(status_code=503, detail="Log file not configured (local mode only).")
    try:
        with open(log_file, "a") as f:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"\n{ts} ERROR [Database] DB_CONN_FAIL: Connection refused by host")
        return {"status": "injected"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── AI endpoints ───────────────────────────────────────────────────────────────
@app.post("/api/ai/explain", dependencies=[Depends(verify_api_key)])
@limiter.limit("5/minute")
def ai_explain(req: AIRequest, request: Request):
    if not _local_ok:
        raise HTTPException(status_code=503, detail="AI module not available in cloud mode.")
    return JSONResponse(content=explain_incident(req.classification, req.exceptions, req.api_result, req.db_result))


@app.post("/api/ai/fix-steps", dependencies=[Depends(verify_api_key)])
@limiter.limit("5/minute")
def ai_fix_steps(req: AIRequest, request: Request):
    if not _local_ok:
        raise HTTPException(status_code=503, detail="AI module not available in cloud mode.")
    return JSONResponse(content=suggest_fixes(req.classification, req.exceptions, req.api_result, req.db_result))


@app.post("/api/ai/ticket-summary", dependencies=[Depends(verify_api_key)])
@limiter.limit("5/minute")
def ai_ticket_summary(req: AIRequest, request: Request):
    if not _local_ok:
        raise HTTPException(status_code=503, detail="AI module not available in cloud mode.")
    return JSONResponse(
        content=generate_ticket_summary(req.classification, req.exceptions, req.api_result, req.db_result)
    )


# ── Integration endpoints ──────────────────────────────────────────────────────
@app.post("/api/integration/loki", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
def fetch_loki(req: LokiRequest, request: Request):
    import re as _re
    import time

    now_ns = int(time.time() * 1e9)
    start_ns = int((time.time() - req.hours * 3600) * 1e9)
    params = {"query": req.query, "start": start_ns, "end": now_ns, "limit": req.limit}
    base = _re.sub(r"/loki(?:/.*)?$", "", req.url.rstrip("/"))
    endpoint = f"{base}/loki/api/v1/query_range"
    try:
        resp = _requests.get(endpoint, params=params, timeout=15)
        resp.raise_for_status()
        lines = [
            msg for stream in resp.json().get("data", {}).get("result", []) for _ts, msg in stream.get("values", [])
        ]
        if not lines:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No log entries returned. Tried: {endpoint} — Use base URL only (e.g. http://localhost:8888)."
                ),
            )
        return _parse_and_respond(_logs_to_text(lines))
    except _requests.exceptions.ConnectionError as e:
        raise HTTPException(
            status_code=502,
            detail=(f"Cannot connect to Loki at {base}. Is mock_log_server.py running? Run: python mock_log_server.py"),
        ) from e
    except _requests.exceptions.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Loki error: {e}") from e


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
            raise HTTPException(status_code=404, detail="No documents found. Check index name and query.")
        return _parse_and_respond(_logs_to_text(lines))
    except _requests.exceptions.ConnectionError as e:
        raise HTTPException(status_code=502, detail=f"Cannot connect to Elasticsearch at {req.url}.") from e
    except _requests.exceptions.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Elasticsearch error: {e}") from e


@app.post("/api/integration/s3", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
def fetch_s3(req: S3Request, request: Request):
    url = f"{req.endpoint.rstrip('/')}/{req.bucket}/{req.key.lstrip('/')}"
    try:
        resp = _requests.get(url, timeout=30)
        resp.raise_for_status()
        return _parse_and_respond(resp.text)
    except _requests.exceptions.ConnectionError as e:
        raise HTTPException(status_code=502, detail=f"Cannot connect to S3 endpoint at {req.endpoint}.") from e
    except _requests.exceptions.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"S3 error {e.response.status_code}. Check bucket and key.") from e


@app.post("/api/integration/http", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
def fetch_custom_http(req: HttpRequest, request: Request):
    try:
        fn = _requests.get if req.method == "GET" else _requests.post
        resp = fn(req.url, headers=req.headers, timeout=15)
        resp.raise_for_status()
        return _parse_and_respond(resp.text)
    except _requests.exceptions.ConnectionError as e:
        raise HTTPException(status_code=502, detail=f"Cannot connect to {req.url}.") from e
    except _requests.exceptions.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Endpoint error: {e}") from e


# ══════════════════════════════════════════════════════════════════
# SUPABASE — RCA History persistence
# Requires: SUPABASE_URL and SUPABASE_KEY in .env
# Falls back gracefully if not configured (no crash)
# ══════════════════════════════════════════════════════════════════
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


class RCASavePayload(BaseModel):
    # Accept every field name variant the frontend might send
    source: str = Field(default="unknown", max_length=300)
    classification: str = Field(default="Unknown", max_length=100)
    severity: str = Field(default="UNKNOWN", max_length=50)
    summary: str = Field(default="")
    total_logs: int = Field(default=0)
    totalLogs: int = Field(default=0)  # camelCase alias
    error_count: int = Field(default=0)
    errCount: int = Field(default=0)  # camelCase alias
    errorCount: int = Field(default=0)  # camelCase alias
    meta: dict = Field(default_factory=dict)

    # Resolve camelCase → snake_case so the DB row is always consistent
    def normalised(self) -> dict:
        _allowed = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "WARNING", "UNKNOWN", "INFO"}
        sev = self.severity.upper()
        # Map frontend variants to allowed values
        _map = {"WARNING": "WARNING", "WARN": "WARNING", "ERROR": "HIGH"}
        sev = _map.get(sev, sev)
        if sev not in _allowed:
            sev = "UNKNOWN"
        return {
            "source": self.source,
            "classification": self.classification,
            "severity": sev,
            "summary": self.summary,
            "total_logs": self.total_logs or self.totalLogs,
            "error_count": self.error_count or self.errCount or self.errorCount,
            "meta": self.meta,
            "created_at": datetime.utcnow().isoformat(),
        }

    model_config = {"extra": "allow"}  # don't reject unknown extra fields


# ── Save an RCA run ────────────────────────────────────────────────────────────
@app.post("/api/rca/save", dependencies=[Depends(verify_api_key)])
@limiter.limit("20/minute")
async def rca_save(payload: RCASavePayload, request: Request):
    if not _sb:
        raise HTTPException(
            status_code=503,
            detail="RCA history is not configured. Set SUPABASE_URL and SUPABASE_KEY in .env",
        )
    try:
        row = payload.normalised()
        result = _sb.table("rca_history").insert(row).execute()
        saved = result.data[0] if result.data else row
        return JSONResponse(content={"ok": True, "id": saved.get("id"), "record": saved})
    except Exception as e:
        logger.exception("Failed to save RCA run.")
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── Fetch history (with optional filters) ─────────────────────────────────────
@app.get("/api/rca/history", dependencies=[Depends(verify_api_key)])
@limiter.limit("30/minute")
async def rca_history(
    request: Request,
    severity: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    if not _sb:
        # Supabase not configured — return empty gracefully
        return JSONResponse(content={"records": [], "total": 0, "supabase": False})
    try:
        q = _sb.table("rca_history").select("*").order("created_at", desc=True)
        if severity:
            q = q.eq("severity", severity.upper())
        if search:
            q = q.or_(f"source.ilike.%{search}%,classification.ilike.%{search}%")
        q = q.range(offset, offset + limit - 1)
        result = q.execute()
        records = result.data if result.data else []
        return JSONResponse(
            content={
                "records": records,
                "total": len(records),
                "supabase": True,
            }
        )
    except Exception as e:
        logger.warning(f"RCA history fetch failed: {e}")
        # Return empty rather than 500 — table may not exist yet
        return JSONResponse(content={"records": [], "total": 0, "supabase": True, "error": str(e)})


# ── Fetch single run by ID ─────────────────────────────────────────────────────
@app.get("/api/rca/history/{rca_id}", dependencies=[Depends(verify_api_key)])
async def rca_get(rca_id: str, request: Request):
    if not _sb:
        raise HTTPException(status_code=503, detail="Supabase not configured.")
    try:
        result = _sb.table("rca_history").select("*").eq("id", rca_id).single().execute()
        if not result.data:
            raise HTTPException(status_code=404, detail=f"RCA run {rca_id} not found.")
        return JSONResponse(content=result.data)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to fetch RCA run.")
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── Delete a run ───────────────────────────────────────────────────────────────
@app.delete("/api/rca/history/{rca_id}", dependencies=[Depends(verify_api_key)])
async def rca_delete(rca_id: str, request: Request):
    if not _sb:
        raise HTTPException(status_code=503, detail="Supabase not configured.")
    try:
        _sb.table("rca_history").delete().eq("id", rca_id).execute()
        return JSONResponse(content={"ok": True, "deleted_id": rca_id})
    except Exception as e:
        logger.exception("Failed to delete RCA run.")
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("api_server:app", host="0.0.0.0", port=port, reload=not IS_RENDER)
