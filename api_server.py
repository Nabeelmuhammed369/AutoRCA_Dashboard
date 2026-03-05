"""
api_server.py — FastAPI bridge for AutoRCA Dashboard
─────────────────────────────────────────────────────
Connects autorca_dashboard.html to your Python monitors
+ Gemini AI features.

HOW TO RUN:
    pip install fastapi uvicorn requests google-generativeai python-dotenv
    python api_server.py

Then open autorca_dashboard.html in your browser.
"""

import os
import logging
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv

# Support both .env and app.env
load_dotenv(".env")
load_dotenv("app.env")

# ── Import your existing modules ──────────────────────────────────────────────
from Monitors.api_monitor  import check_api_health
from Monitors.log_analyzer import analyze_logs
from Monitors.db_validator import validate_data
from Core.rca_engine       import classify_issue
from Core.logger           import setup_logger

# ── Import AI analyzer ────────────────────────────────────────────────────────
from Core.ai_analyzer import explain_incident, suggest_fixes, generate_ticket_summary

# ── Init ──────────────────────────────────────────────────────────────────────
setup_logger()
logger = logging.getLogger("API_SERVER")

app = FastAPI(title="AutoRCA API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── Load config ───────────────────────────────────────────────────────────────
with open("config.yaml") as f:
    config = yaml.safe_load(f)


# ── Pydantic model for AI requests ────────────────────────────────────────────
class AIRequest(BaseModel):
    classification: str
    exceptions:     list
    api_result:     dict
    db_result:      dict


# ── Helper: read raw exception/critical lines ────────────────────────────────
def read_exception_lines(log_file: str, max_lines: int = 100) -> list:
    """Return the last `max_lines` ERROR or CRITICAL lines with their category tag."""
    if not os.path.exists(log_file):
        return []
    try:
        with open(log_file, "r") as f:
            lines = f.readlines()
        filtered = [l.strip() for l in lines if "ERROR" in l or "CRITICAL" in l]
        return filtered[-max_lines:]
    except Exception:
        logger.exception("Could not read exception lines.")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# EXISTING ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/run")
def run_diagnostic():
    """Runs all monitors and returns real-time system state."""
    logger.info("── Diagnostic requested via /api/run ──")

    api_result     = check_api_health(config["api"]["url"], config["api"]["timeout"])
    log_result     = analyze_logs(config["log"]["file"])
    db_result      = validate_data(config["database"]["path"])
    classification = classify_issue(api_result, log_result, db_result)

    log_result["exceptions"] = read_exception_lines(config["log"]["file"])

    return JSONResponse(content={
        "api":            api_result,
        "logs":           log_result,
        "db":             db_result,
        "classification": classification,
    })


@app.get("/api/simulate/db-crash")
def simulate_db_crash():
    """Injects a DB_CONN_FAIL error line into the log file."""
    try:
        with open(config["log"]["file"], "a") as f:
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"\n{ts} ERROR DB_CONN_FAIL: Connection refused by host")
        return {"status": "injected", "message": "DB_CONN_FAIL written to log"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "AutoRCA API", "version": "2.0.0"}


# ─────────────────────────────────────────────────────────────────────────────
# AI ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/ai/explain")
def ai_explain(req: AIRequest):
    """
    Feature 1: Plain-English explanation of what caused the incident.
    
    Request body:
        { classification, exceptions, api_result, db_result }
    Response:
        { success, explanation, error }
    """
    logger.info("AI explain requested.")
    result = explain_incident(
        classification = req.classification,
        exceptions     = req.exceptions,
        api_result     = req.api_result,
        db_result      = req.db_result,
    )
    if not result["success"]:
        logger.warning(f"AI explain failed: {result['error']}")
    return JSONResponse(content=result)


@app.post("/api/ai/fix-steps")
def ai_fix_steps(req: AIRequest):
    """
    Feature 2: AI-generated fix steps grounded in actual log lines.

    Response:
        { success, steps: [{step, command}], error }
    """
    logger.info("AI fix steps requested.")
    result = suggest_fixes(
        classification = req.classification,
        exceptions     = req.exceptions,
        api_result     = req.api_result,
        db_result      = req.db_result,
    )
    if not result["success"]:
        logger.warning(f"AI fix steps failed: {result['error']}")
    return JSONResponse(content=result)


@app.post("/api/ai/ticket-summary")
def ai_ticket_summary(req: AIRequest):
    """
    Feature 3: Auto-generate GitHub issue + Slack message.

    Response:
        { success, github, slack, error }
    """
    logger.info("AI ticket summary requested.")
    result = generate_ticket_summary(
        classification = req.classification,
        exceptions     = req.exceptions,
        api_result     = req.api_result,
        db_result      = req.db_result,
    )
    if not result["success"]:
        logger.warning(f"AI ticket summary failed: {result['error']}")
    return JSONResponse(content=result)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, reload=True)