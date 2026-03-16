"""
Main.py — AutoRCA Dashboard Entry Point (Refactored)
─────────────────────────────────────────────────────
Run with:  streamlit run Main.py

What changed vs old version:
  ✅ Removed all hardcoded log file paths
  ✅ Removed auto-run of analyze_logs() on every page load
  ✅ Log source is now session_state driven via log_source_manager
  ✅ Page router uses Streamlit pages/ folder (multi-page app pattern)
  ✅ Sidebar shows live connection status badge
"""

import logging
import os

import streamlit as st
from dotenv import load_dotenv
from supabase import create_client

from api_server import app
from Core.logger import setup_logger
from log_source_manager import (
    get_source_label,
    get_source_meta,
    init_log_source,
    is_connected,
)


def load_env(path=None):
    """Reliable .env reader — works on Windows without dotenv issues."""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                os.environ[key.strip()] = val.strip()


load_env()  # Call BEFORE reading any env vars

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("⚠️  SUPABASE_URL or SUPABASE_KEY not set — RCA history endpoints will fail.")
    supabase = None
else:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    print(f"✅ Supabase connected: {SUPABASE_URL}")

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://ngxvonscsxsqkeyzwuzt.supabase.co")
SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5neHZvbnNjc3hzcWtleXp3dXp0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzMzMzU0MTIsImV4cCI6MjA4ODkxMTQxMn0.qYBMnNOuz3vbWjQ_IU3HE__KSHXfFlylLUr1rSunq_0",
)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ── Save an RCA run ────────────────────────────────────────────
@app.post("/api/rca/save")
async def rca_save(payload: dict):
    try:
        data = {
            "source_name": payload.get("source_name", "Unknown"),
            "severity": payload.get("severity", "warning"),
            "total_entries": payload.get("total_entries", 0),
            "error_count": payload.get("error_count", 0),
            "warn_count": payload.get("warn_count", 0),
            "error_rate": payload.get("error_rate", 0),
            "ai_summary": payload.get("ai_summary", ""),
            "fix_steps": payload.get("fix_steps", ""),
            "incident_groups": payload.get("incident_groups", []),
            "affected_services": payload.get("affected_services", []),
            "remediation": payload.get("remediation", []),
            "stats": payload.get("stats", {}),
            "tags": payload.get("tags", []),
            "notes": payload.get("notes", ""),
        }
        result = supabase.table("rca_history").insert(data).execute()
        return {"ok": True, "id": result.data[0]["id"]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Fetch history (with filters) ──────────────────────────────
@app.get("/api/rca/history")
async def rca_history(severity: str = None, search: str = None, limit: int = 50, offset: int = 0):
    try:
        q = supabase.table("rca_history").select("*").order("created_at", desc=True).range(offset, offset + limit - 1)
        if severity and severity != "all":
            q = q.eq("severity", severity)
        if search:
            q = q.ilike("source_name", f"%{search}%")
        result = q.execute()
        return {"ok": True, "data": result.data, "count": len(result.data)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Fetch single run for diff comparison ─────────────────────
@app.get("/api/rca/history/{rca_id}")
async def rca_get(rca_id: str):
    try:
        result = supabase.table("rca_history").select("*").eq("id", rca_id).single().execute()
        return {"ok": True, "data": result.data}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Delete a run ──────────────────────────────────────────────
@app.delete("/api/rca/history/{rca_id}")
async def rca_delete(rca_id: str):
    try:
        supabase.table("rca_history").delete().eq("id", rca_id).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── App Config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AutoRCA Dashboard",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Logger ────────────────────────────────────────────────────────────────────
setup_logger()
logger = logging.getLogger("MAIN")

# ── Session State Init ────────────────────────────────────────────────────────
init_log_source()  # Safe to call multiple times — only sets defaults if missing

# ── Global CSS ────────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
/* Dark sidebar */
[data-testid="stSidebar"] { background: #0f172a; }
[data-testid="stSidebar"] * { color: #e2e8f0 !important; }

/* Status badge */
.status-badge-ok   { background: #16a34a22; border: 1px solid #16a34a;
                     border-radius: 20px; padding: 4px 14px;
                     color: #4ade80; font-size: 13px; display: inline-block; }
.status-badge-none { background: #64748b22; border: 1px solid #64748b;
                     border-radius: 20px; padding: 4px 14px;
                     color: #94a3b8; font-size: 13px; display: inline-block; }

/* Page title */
h1 { font-size: 28px !important; }

/* Cards */
.metric-card { background: #1e293b; border-radius: 10px;
               padding: 16px 20px; border: 1px solid #334155; }
</style>
""",
    unsafe_allow_html=True,
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🔍 AutoRCA")
    st.markdown("*Intelligent Incident Analyzer*")
    st.divider()

    # Live connection status
    if is_connected():
        label = get_source_label()
        meta = get_source_meta()
        ingested = meta.get("ingested_at", "")
        st.markdown('<div class="status-badge-ok">● Connected</div>', unsafe_allow_html=True)
        st.caption(f"**Source:** {label}")
        if ingested:
            st.caption(f"**Ingested:** {ingested}")
    else:
        st.markdown('<div class="status-badge-none">○ No Log Source</div>', unsafe_allow_html=True)
        st.caption("Connect a log source to begin analysis.")

    st.divider()
    st.caption("v2.0 · [GitHub](https://github.com/Nabeelmuhammed369/AutoRCA_Dashboard)")

# ── Main Page Content (when run directly / home page) ─────────────────────────
st.title("🔍 AutoRCA: Intelligent Incident Analyzer")
st.markdown("Automated root cause analysis for your infrastructure logs.")
st.divider()

if not is_connected():
    # ── Empty State ───────────────────────────────────────────────────────────
    st.markdown(
        """
    <div style="text-align:center; padding: 60px 0;">
        <div style="font-size: 64px;">📡</div>
        <h2 style="color:#94a3b8; margin: 16px 0 8px;">No Log Source Connected</h2>
        <p style="color:#64748b; font-size:16px;">
            Connect your centralized logging system or upload a log file to begin analysis.
        </p>
    </div>
    """,
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            """
        <div style="background:#1e293b; border-radius:10px; padding:20px;
                    border:1px solid #334155; text-align:center;">
            <div style="font-size:32px;">📂</div>
            <h4 style="color:#e2e8f0;">Upload a Log File</h4>
            <p style="color:#64748b; font-size:13px;">
                Drag & drop any .log, .txt, .json, .csv file.<br>
                Supports 9 log formats auto-detected.
            </p>
        </div>
        """,
            unsafe_allow_html=True,
        )

    with c2:
        st.markdown(
            """
        <div style="background:#1e293b; border-radius:10px; padding:20px;
                    border:1px solid #334155; text-align:center;">
            <div style="font-size:32px;">🔗</div>
            <h4 style="color:#e2e8f0;">Connect an Integration</h4>
            <p style="color:#64748b; font-size:13px;">
                Link Loki, Elasticsearch, S3, or any HTTP log source
                directly from the UI.
            </p>
        </div>
        """,
            unsafe_allow_html=True,
        )

    with c3:
        st.markdown(
            """
        <div style="background:#1e293b; border-radius:10px; padding:20px;
                    border:1px solid #334155; text-align:center;">
            <div style="font-size:32px;">📡</div>
            <h4 style="color:#e2e8f0;">Push via API</h4>
            <p style="color:#64748b; font-size:13px;">
                Your log agent or pipeline can POST logs directly
                to the AutoRCA API endpoint.
            </p>
        </div>
        """,
            unsafe_allow_html=True,
        )

    st.markdown("")
    st.info("👈 Use **Log Source** in the left sidebar to get started.")

else:
    # ── Connected: show quick summary ─────────────────────────────────────────
    from log_source_manager import get_log_stats

    stats = get_log_stats()
    meta = get_source_meta()

    st.success(f"✅ Log source connected: **{get_source_label()}**")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Entries", f"{stats.get('total', 0):,}")
    m2.metric("Errors", f"{stats.get('errors', 0):,}")
    m3.metric("Warnings", f"{stats.get('warnings', 0):,}")
    m4.metric("Lines Ingested", f"{meta.get('lines', 0):,}")

    st.divider()
    st.info("📊 Navigate to **RCA Dashboard** in the sidebar to run full analysis.")
    st.info("🔍 Navigate to **Log Explorer** to filter and search log entries.")
