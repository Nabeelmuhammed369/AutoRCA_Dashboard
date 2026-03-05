"""
Core/ai_analyzer.py — Gemini AI integration for AutoRCA
─────────────────────────────────────────────────────────
Provides three AI-powered features:
  1. Incident Explanation  — plain English cause analysis
  2. Fix Steps             — specific remediation from actual log lines
  3. Ticket Summary        — ready-to-paste GitHub / Slack summary

Uses google-generativeai with Gemini 1.5 Flash (free tier).
Falls back gracefully if the API key is missing or quota is exceeded.
"""

import os
import logging
from dotenv import load_dotenv

# Support both .env and app.env filenames
load_dotenv(".env")
load_dotenv("app.env")

logger = logging.getLogger("AI_ANALYZER")

# ── Load API key & init client ────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

_model = None

def _get_model():
    """Lazy-init the Gemini model once."""
    global _model
    if _model is not None:
        return _model
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not found in .env / app.env file.")
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        _model = genai.GenerativeModel("gemini-2.0-flash-lite")
        logger.info("Gemini model initialised successfully.")
        return _model
    except ImportError:
        raise ImportError("google-generativeai not installed. Run: pip install google-generativeai")


def _get_model():
    """Not needed for new SDK — kept for compatibility."""
    return None

def _call_gemini(prompt: str) -> str:
    from google import genai
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )
    return response.text.strip()


# ── Feature 1: Incident Explanation ──────────────────────────────────────────
def explain_incident(classification: str, exceptions: list, api_result: dict, db_result: dict) -> dict:
    """
    Returns a plain-English explanation of what caused the incident.

    Returns:
        { "success": bool, "explanation": str, "error": str | None }
    """
    try:
        error_sample = "\n".join(exceptions[-10:]) if exceptions else "No exceptions found."
        api_status   = api_result.get("status_code") or api_result.get("error", "Unknown")
        api_latency  = api_result.get("response_time", "N/A")
        null_emails  = db_result.get("null_email_count", 0)

        prompt = f"""You are a senior Site Reliability Engineer analyzing a system incident.

Incident Data:
- Classification: {classification}
- API Status Code: {api_status}
- API Response Time: {api_latency}s
- DB Null Email Count: {null_emails}
- Recent Error Log Lines:
{error_sample}

Task: Write a clear, concise incident explanation (3-4 sentences) for a technical team.
Explain:
1. What specifically went wrong based on the log evidence
2. Which component is the root cause
3. What the likely impact on users was

Be direct and technical. Do not use bullet points. Write as flowing paragraphs."""

        explanation = _call_gemini(prompt)
        logger.info("Incident explanation generated successfully.")
        return {"success": True, "explanation": explanation, "error": None}

    except Exception as e:
        logger.exception("Failed to generate incident explanation.")
        return {"success": False, "explanation": "", "error": str(e)}


# ── Feature 2: AI Fix Steps ───────────────────────────────────────────────────
def suggest_fixes(classification: str, exceptions: list, api_result: dict, db_result: dict) -> dict:
    """
    Returns specific, actionable fix steps grounded in the actual log lines.

    Returns:
        { "success": bool, "steps": [ {"step": str, "command": str|None} ], "error": str|None }
    """
    try:
        error_sample = "\n".join(exceptions[-15:]) if exceptions else "No exceptions found."
        api_status   = api_result.get("status_code") or api_result.get("error", "Unknown")
        null_emails  = db_result.get("null_email_count", 0)

        prompt = f"""You are a senior Site Reliability Engineer.

Incident Classification: {classification}
API Status: {api_status}
DB Null Email Count: {null_emails}
Actual Log Errors:
{error_sample}

Task: Provide exactly 4 specific, actionable fix steps based on the ACTUAL errors above.
Each step must reference the specific error patterns you see in the logs.

Respond in this EXACT format (JSON array, no markdown, no code fences):
[
  {{"step": "Clear description of action", "command": "actual command or null"}},
  {{"step": "Clear description of action", "command": "actual command or null"}},
  {{"step": "Clear description of action", "command": "actual command or null"}},
  {{"step": "Clear description of action", "command": "actual command or null"}}
]"""

        raw = _call_gemini(prompt)

        # Clean up response — strip any accidental markdown fences
        raw = raw.replace("```json", "").replace("```", "").strip()

        import json
        steps = json.loads(raw)
        logger.info("AI fix steps generated successfully.")
        return {"success": True, "steps": steps, "error": None}

    except Exception as e:
        logger.exception("Failed to generate AI fix steps.")
        return {
            "success": False,
            "steps": [],
            "error": str(e)
        }


# ── Feature 3: GitHub / Slack Ticket Summary ─────────────────────────────────
def generate_ticket_summary(classification: str, exceptions: list, api_result: dict, db_result: dict) -> dict:
    """
    Generates a ready-to-paste incident summary for GitHub Issues or Slack.

    Returns:
        { "success": bool, "github": str, "slack": str, "error": str|None }
    """
    try:
        from datetime import datetime
        error_sample = "\n".join(exceptions[-8:]) if exceptions else "No exceptions found."
        api_status   = api_result.get("status_code") or api_result.get("error", "Unknown")
        api_latency  = api_result.get("response_time", "N/A")
        null_emails  = db_result.get("null_email_count", 0)
        timestamp    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        prompt = f"""You are a senior Site Reliability Engineer writing an incident report.

Incident Data:
- Timestamp: {timestamp}
- Classification: {classification}
- API Status: {api_status}
- Latency: {api_latency}s
- DB Null Email Count: {null_emails}
- Error Sample:
{error_sample}

Generate TWO summaries:

1. GITHUB_ISSUE: A GitHub issue body in markdown. Include:
   - ## Summary (2 sentences)
   - ## Impact
   - ## Root Cause
   - ## Evidence (key log lines)
   - ## Suggested Fix Steps (3 bullet points)
   - Labels suggestion at the bottom

2. SLACK_MESSAGE: A Slack message (plain text, use emoji). Should be:
   - Max 5 lines
   - Lead with severity emoji (🔴 P1 / 🟡 P2 / ✅ OK)
   - Include: what happened, impact, who should look at it

Respond in this EXACT format:
===GITHUB===
[github issue content here]
===SLACK===
[slack message here]"""

        raw = _call_gemini(prompt)

        # Parse the two sections
        github_text = ""
        slack_text  = ""

        if "===GITHUB===" in raw and "===SLACK===" in raw:
            parts       = raw.split("===SLACK===")
            github_text = parts[0].replace("===GITHUB===", "").strip()
            slack_text  = parts[1].strip()
        else:
            # Fallback: use the whole response as github
            github_text = raw
            slack_text  = f"🔴 *Incident Alert* — {classification}\nDetected at {timestamp}\nAPI: {api_status} | Errors: {len(exceptions)} | DB anomalies: {null_emails}\nPlease investigate immediately."

        logger.info("Ticket summary generated successfully.")
        return {
            "success": True,
            "github":  github_text,
            "slack":   slack_text,
            "error":   None
        }

    except Exception as e:
        logger.exception("Failed to generate ticket summary.")
        return {"success": False, "github": "", "slack": "", "error": str(e)}