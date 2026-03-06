"""
api_server.py — AutoRCA FastAPI Backend (Secured v3)
───────────────────────────────────────────────────────
Security features:
  ✅ API key authentication on all endpoints
  ✅ Restricted CORS (not wildcard)
  ✅ Rate limiting via slowapi
  ✅ Input validation via Pydantic
  ✅ Proper error responses

HOW TO RUN:
  1. Copy .env.example to .env and fill in values
  2. pip install fastapi uvicorn requests google-genai python-dotenv slowapi
  3. python api_server.py
"""

import os
import logging
import yaml
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, Depends
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
from Monitors.log_analyzer import analyze_logs
from Monitors.db_validator import validate_data
from Core.rca_engine       import classify_issue
from Core.logger           import setup_logger
from Core.ai_analyzer      import explain_incident, suggest_fixes, generate_ticket_summary

setup_logger()
logger = logging.getLogger("API_SERVER")

with open("config.yaml") as f:
    config = yaml.safe_load(f)

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

# ── CORS — restricted to your dashboard origin only ──────────────────────────
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "http://localhost:5500")
CORS_ORIGINS = [
    ALLOWED_ORIGIN,
    "null",                      # file:// local HTML file
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "http://localhost:5173",
]

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="AutoRCA API", version="3.0.0", docs_url=None, redoc_url=None)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type"],
)

# ── Models ────────────────────────────────────────────────────────────────────
class AIRequest(BaseModel):
    classification: str  = Field(..., max_length=100)
    exceptions:     list = Field(default_factory=list)
    api_result:     dict
    db_result:      dict

# ── Helper ────────────────────────────────────────────────────────────────────
def read_exception_lines(log_file: str, max_lines: int = 100) -> list:
    if not os.path.exists(log_file):
        return []
    try:
        with open(log_file, "r") as f:
            lines = f.readlines()
        return [l.strip() for l in lines if "ERROR" in l or "CRITICAL" in l][-max_lines:]
    except Exception:
        logger.exception("Could not read exception lines.")
        return []

# ── Core Endpoints ────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    """Public health check — no auth needed."""
    db_ok  = os.path.exists(config["database"]["path"])
    log_ok = os.path.exists(config["log"]["file"])
    key_ok = bool(AUTORCA_API_KEY)
    return {
        "status":  "ok" if (db_ok and log_ok and key_ok) else "degraded",
        "version": "3.0.0",
        "checks": {
            "database_file": "ok" if db_ok  else "missing",
            "log_file":      "ok" if log_ok else "missing",
            "api_key_set":   "ok" if key_ok else "not set",
        }
    }

@app.get("/api/run", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
def run_diagnostic(request: Request):
    logger.info("Diagnostic requested.")
    api_result     = check_api_health(config["api"]["url"], config["api"]["timeout"])
    log_result     = analyze_logs(config["log"]["file"])
    db_result      = validate_data(config["database"]["path"])
    classification = classify_issue(api_result, log_result, db_result)
    log_result["exceptions"] = read_exception_lines(config["log"]["file"])
    return JSONResponse(content={
        "api": api_result, "logs": log_result,
        "db": db_result, "classification": classification,
    })

@app.get("/api/simulate/db-crash", dependencies=[Depends(verify_api_key)])
@limiter.limit("5/minute")
def simulate_db_crash(request: Request):
    try:
        with open(config["log"]["file"], "a") as f:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"\n{ts} ERROR [Database] DB_CONN_FAIL: Connection refused by host")
        return {"status": "injected"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── AI Endpoints ──────────────────────────────────────────────────────────────

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