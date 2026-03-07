"""
api_server.py — AutoRCA FastAPI Backend (v4 - Log Source Aware)
────────────────────────────────────────────────────────────────
What changed from v3:
  ✅ Added  POST /api/ingest  — log agents push raw logs here
  ✅ Removed hardcoded log file path from /api/run
  ✅ /api/run now works on the last-ingested log content
  ✅ Ingested logs stored in shared memory (works for single-server deployment)
  ✅ All other endpoints unchanged

HOW TO RUN:
  python api_server.py
  or: uvicorn api_server:app --host 0.0.0.0 --port 8000 --reload
"""

import os
import logging
import yaml
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Depends, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

load_dotenv(".env")
load_dotenv("app.env")

from Monitors.api_monitor  import check_api_health
from Monitors.db_validator import validate_data
from Core.rca_engine       import classify_issue
from Core.logger           import setup_logger
from Core.ai_analyzer      import explain_incident, suggest_fixes, generate_ticket_summary

# ── Import log parser (no Streamlit dependency) ───────────────────────────────
from log_parser import parse_log_content, summarise

setup_logger()
logger = logging.getLogger("API_SERVER")

with open("config.yaml") as f:
    config = yaml.safe_load(f)

# ─────────────────────────────────────────────
# IN-MEMORY LOG STORE
# Stores the last ingested raw log content.
# For multi-worker deployments, replace with Redis or a DB.
# ─────────────────────────────────────────────
_log_store: dict = {
    "raw":        None,   # str: raw log text
    "df":         None,   # pd.DataFrame from parse_log_content()
    "stats":      None,   # dict from summarise()
    "ingested_at": None,  # ISO timestamp
    "source":     None,   # label string
}


# ── API Key Auth ──────────────────────────────────────────────────────────────
AUTORCA_API_KEY = os.getenv("AUTORCA_API_KEY", "")
if not AUTORCA_API_KEY:
    logger.warning("AUTORCA_API_KEY not set — all requests will be rejected.")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_api_key(key: str = Depends(api_key_header)):
    if not AUTORCA_API_KEY:
        raise HTTPException(status_code=500, detail="Server misconfigured: AUTORCA_API_KEY not set.")
    if key != AUTORCA_API_KEY:
        logger.warning("Rejected request — invalid or missing API key.")
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")
    return key


# ── Rate Limiter ──────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

# ── CORS ──────────────────────────────────────────────────────────────────────
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "http://localhost:8501")
CORS_ORIGINS = [
    ALLOWED_ORIGIN,
    "http://localhost:8501",   # Streamlit default port
    "http://127.0.0.1:8501",
    "http://localhost:5500",   # Live Server
    "http://127.0.0.1:5500",
    "null",                    # file:// protocol
]

# Allow ALL localhost ports (Five Server uses random ports like 54856)
CORS_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="AutoRCA API", version="4.0.0", docs_url="/docs", redoc_url=None)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=CORS_ORIGIN_REGEX,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["X-API-Key", "Content-Type"],
    allow_credentials=False,
)


# ── Models ────────────────────────────────────────────────────────────────────
class AIRequest(BaseModel):
    classification: str  = Field(..., max_length=100)
    exceptions:     list = Field(default_factory=list)
    api_result:     dict
    db_result:      dict

class IngestMetadata(BaseModel):
    source: Optional[str] = "api_push"


# ── Helpers ───────────────────────────────────────────────────────────────────
def _log_result_from_store() -> dict:
    """Build a log_result dict from the current in-memory store."""
    df = _log_store.get("df")
    if df is None or df.empty:
        return {"total_errors": 0, "total_warnings": 0, "exceptions": [], "formats": []}
    errors   = df[df["is_error"]]
    warnings = df[df["is_warning"]]
    return {
        "total_errors":   int(errors.shape[0]),
        "total_warnings": int(warnings.shape[0]),
        "exceptions":     errors["message"].tolist()[:50],
        "formats":        df["format"].unique().tolist(),
    }


# ─────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────

@app.get("/api/health")
def health():
    """Public health check — no auth needed."""
    has_logs = _log_store["raw"] is not None
    return {
        "status":      "ok",
        "version":     "4.0.0",
        "logs_loaded": has_logs,
        "ingested_at": _log_store.get("ingested_at"),
        "source":      _log_store.get("source"),
        "checks": {
            "api_key_set": "ok" if AUTORCA_API_KEY else "not set",
        }
    }


@app.post("/api/ingest", dependencies=[Depends(verify_api_key)])
@limiter.limit("20/minute")
async def ingest_logs(request: Request):
    """
    Receive raw log content from any log agent or pipeline.

    Send raw log text as the request body (Content-Type: text/plain).
    Accepts any format supported by log_parser.py.

    Example:
        curl -X POST http://localhost:8000/api/ingest \\
             -H "X-API-Key: YOUR_KEY" \\
             -H "Content-Type: text/plain" \\
             --data-binary @/var/log/app.log
    """
    content_type = request.headers.get("content-type", "text/plain")
    raw_bytes = await request.body()

    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raw_text = raw_bytes.decode("latin-1", errors="replace")

    if not raw_text.strip():
        raise HTTPException(status_code=400, detail="Request body is empty — send raw log text.")

    source_label = request.headers.get("X-Source-Label", "api_push")
    logger.info(f"Ingesting logs from source='{source_label}', size={len(raw_text)} bytes")

    df    = parse_log_content(raw_text)
    stats = summarise(df)

    _log_store["raw"]         = raw_text
    _log_store["df"]          = df
    _log_store["stats"]       = stats
    _log_store["ingested_at"] = datetime.utcnow().isoformat() + "Z"
    _log_store["source"]      = source_label

    return JSONResponse(content={
        "status":      "ingested",
        "lines":       len(raw_text.splitlines()),
        "entries":     stats.get("total", 0),
        "errors":      stats.get("errors", 0),
        "warnings":    stats.get("warnings", 0),
        "formats":     stats.get("formats", []),
        "ingested_at": _log_store["ingested_at"],
        "source":      source_label,
    })


@app.get("/api/run", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
def run_diagnostic(request: Request):
    """
    Run full RCA diagnostic on the currently loaded logs.
    Logs must be loaded first via POST /api/ingest.
    """
    if _log_store["raw"] is None:
        raise HTTPException(
            status_code=400,
            detail="No logs loaded. POST raw log content to /api/ingest first."
        )

    logger.info("Running diagnostic on ingested logs.")
    log_result     = _log_result_from_store()
    api_result     = check_api_health(config["api"]["url"], config["api"].get("timeout", 3))
    db_path        = config.get("database", {}).get("path", "")
    db_result      = validate_data(db_path) if db_path else {"null_email_count": 0}
    classification = classify_issue(api_result, log_result, db_result)

    return JSONResponse(content={
        "api":            api_result,
        "logs":           log_result,
        "db":             db_result,
        "classification": classification,
        "stats":          _log_store["stats"],
        "ingested_at":    _log_store["ingested_at"],
        "source":         _log_store["source"],
    })


@app.get("/api/stats", dependencies=[Depends(verify_api_key)])
def get_stats(request: Request):
    """Return stats for the currently loaded logs without running full diagnostic."""
    if _log_store["stats"] is None:
        raise HTTPException(status_code=404, detail="No logs loaded yet.")
    return JSONResponse(content={
        "stats":       _log_store["stats"],
        "ingested_at": _log_store["ingested_at"],
        "source":      _log_store["source"],
    })


# ── AI Endpoints (unchanged from v3) ──────────────────────────────────────────

@app.post("/api/ai/explain", dependencies=[Depends(verify_api_key)])
@limiter.limit("5/minute")
def ai_explain(req: AIRequest, request: Request):
    result = explain_incident(req.classification, req.exceptions, req.api_result, req.db_result)
    return JSONResponse(content=result)

@app.post("/api/ai/fix-steps", dependencies=[Depends(verify_api_key)])
@limiter.limit("5/minute")
def ai_fix_steps(req: AIRequest, request: Request):
    result = suggest_fixes(req.classification, req.exceptions, req.api_result, req.db_result)
    return JSONResponse(content=result)

@app.post("/api/ai/ticket-summary", dependencies=[Depends(verify_api_key)])
@limiter.limit("5/minute")
def ai_ticket_summary(req: AIRequest, request: Request):
    result = generate_ticket_summary(req.classification, req.exceptions, req.api_result, req.db_result)
    return JSONResponse(content=result)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, reload=True)