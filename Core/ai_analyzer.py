"""
Core/ai_analyzer.py — AI integration using Groq (Free, no region restrictions)
────────────────────────────────────────────────────────────────────────────────
Switched from Gemini to Groq because Gemini free tier has region restrictions.
Groq is completely free, faster, and works globally.

Model: llama-3.3-70b-versatile (Groq free tier)
Install: pip install groq python-dotenv
Get key: console.groq.com → API Keys → Create API Key
"""

import json
import logging
import os

from dotenv import load_dotenv

load_dotenv(".env")
load_dotenv("app.env")

logger = logging.getLogger("AI_ANALYZER")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")


def _call_groq(prompt: str) -> str:
    """Call Groq API — fast, free, no region restrictions."""
    import Core.ai_analyzer as _self

    api_key = _self.GROQ_API_KEY
    if not api_key:
        raise ValueError("GROQ_API_KEY not found in .env file. Get a free key at console.groq.com")
    try:
        from groq import Groq

        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",  # best free model on Groq
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,  # lower = more consistent, factual responses
            max_tokens=1024,
        )
        return response.choices[0].message.content.strip()
    except ImportError as err:
        raise ImportError("groq not installed. Run: pip install groq") from err


# ── Feature 1: Incident Explanation ──────────────────────────────────────────
def explain_incident(classification, exceptions, api_result, db_result):
    """
    Returns a plain-English explanation of what caused the incident.
    Response: { success, explanation, error }
    """
    try:
        error_sample = "\n".join(exceptions[-10:]) if exceptions else "No exceptions found."
        api_status = api_result.get("status_code") or api_result.get("error", "Unknown")
        api_latency = api_result.get("response_time", "N/A")
        null_emails = db_result.get("null_email_count", 0)

        prompt = f"""You are a senior Site Reliability Engineer analyzing a system incident.

Incident Data:
- Classification: {classification}
- API Status Code: {api_status}
- API Response Time: {api_latency}s
- DB Null Email Count: {null_emails}
- Recent Error Log Lines:
{error_sample}

Write a clear 3-4 sentence incident explanation for a technical team.
Cover: what specifically went wrong, which component is the root cause, likely user impact.
Be direct and technical. Write as flowing paragraphs — no bullet points."""

        explanation = _call_groq(prompt)
        logger.info("Incident explanation generated successfully.")
        return {"success": True, "explanation": explanation, "error": None}

    except Exception as e:
        logger.exception("Failed to generate explanation.")
        return {"success": False, "explanation": "", "error": str(e)}


# ── Feature 2: AI Fix Steps ───────────────────────────────────────────────────
def suggest_fixes(classification, exceptions, api_result, db_result):
    """
    Returns specific fix steps grounded in the actual log lines.
    Response: { success, steps: [{step, command}], error }
    """
    try:
        error_sample = "\n".join(exceptions[-15:]) if exceptions else "No exceptions found."
        api_status = api_result.get("status_code") or api_result.get("error", "Unknown")
        null_emails = db_result.get("null_email_count", 0)

        prompt = f"""You are a senior Site Reliability Engineer.

Incident Classification: {classification}
API Status: {api_status}
DB Null Email Count: {null_emails}
Actual Log Errors:
{error_sample}

Provide exactly 4 specific fix steps based on the ACTUAL errors shown above.
Each step must reference the specific error patterns you see.

Respond ONLY as a valid JSON array. No markdown. No code fences. No explanation outside the JSON:
[
  {{"step": "Clear action description", "command": "actual shell command or null"}},
  {{"step": "Clear action description", "command": "actual shell command or null"}},
  {{"step": "Clear action description", "command": "actual shell command or null"}},
  {{"step": "Clear action description", "command": "actual shell command or null"}}
]"""

        raw = _call_groq(prompt)
        # Strip any accidental markdown fences
        raw = raw.replace("```json", "").replace("```", "").strip()
        # Extract just the JSON array if there's extra text
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start != -1 and end > start:
            raw = raw[start:end]
        steps = json.loads(raw)
        logger.info("Fix steps generated successfully.")
        return {"success": True, "steps": steps, "error": None}

    except Exception as e:
        logger.exception("Failed to generate fix steps.")
        return {"success": False, "steps": [], "error": str(e)}


# ── Feature 3: GitHub / Slack Ticket Summary ─────────────────────────────────
def generate_ticket_summary(classification, exceptions, api_result, db_result):
    """
    Generates a GitHub issue body + Slack message.
    Response: { success, github, slack, error }
    """
    try:
        from datetime import datetime

        error_sample = "\n".join(exceptions[-8:]) if exceptions else "No exceptions found."
        api_status = api_result.get("status_code") or api_result.get("error", "Unknown")
        api_latency = api_result.get("response_time", "N/A")
        null_emails = db_result.get("null_email_count", 0)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        sev_emoji = (
            "🔴" if classification == "Infrastructure Issue" else "🟡" if classification != "System Healthy" else "✅"
        )

        prompt = f"""You are a senior Site Reliability Engineer writing an incident report.

Incident Data:
- Timestamp: {timestamp}
- Classification: {classification}
- API Status: {api_status}
- Latency: {api_latency}s
- DB Null Email Count: {null_emails}
- Error Sample:
{error_sample}

Generate TWO summaries in this EXACT format with no extra text before or after:

===GITHUB===
## Summary
[2 sentences describing the incident]

## Impact
[Who was affected and how]

## Root Cause
[Technical root cause]

## Evidence
[Key log lines as a markdown code block]

## Suggested Fix Steps
- [step 1]
- [step 2]
- [step 3]

**Labels:** `incident`, `{classification.lower().replace(" ", "-")}`, `priority-{"p1" if classification == "Infrastructure Issue" else "p2"}`
===SLACK===
{sev_emoji} *Incident: {classification}* — {timestamp}
API: {api_status} | Errors detected | DB anomalies: {null_emails}
[1 sentence root cause summary]
[1 sentence recommended action]
cc: @on-call-engineer"""

        raw = _call_groq(prompt)

        if "===GITHUB===" in raw and "===SLACK===" in raw:
            parts = raw.split("===SLACK===")
            github_text = parts[0].replace("===GITHUB===", "").strip()
            slack_text = parts[1].strip()
        else:
            # Fallback if model doesn't follow format perfectly
            github_text = raw
            slack_text = (
                f"{sev_emoji} *Incident: {classification}* — {timestamp}\n"
                f"API: {api_status} | DB anomalies: {null_emails}\n"
                f"Please investigate immediately. cc: @on-call-engineer"
            )

        logger.info("Ticket summary generated successfully.")
        return {"success": True, "github": github_text, "slack": slack_text, "error": None}

    except Exception as e:
        logger.exception("Failed to generate ticket summary.")
        return {"success": False, "github": "", "slack": "", "error": str(e)}