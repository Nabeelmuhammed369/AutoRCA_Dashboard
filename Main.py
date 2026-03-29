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
load_dotenv()
# NOTE: Supabase is initialised in api_server.py — do NOT create a second
# client here. All DB operations go through the FastAPI backend.


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
