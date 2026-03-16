"""
pages/2_RCA_Dashboard.py — AutoRCA Root Cause Analysis Dashboard
────────────────────────────────────────────────────────────────
Replaces the old dashboard.py.

What changed:
  ✅ No hardcoded log file path
  ✅ Reads entirely from st.session_state via log_source_manager
  ✅ "Run Diagnostic" button works on the connected log source
  ✅ API health check is optional (not mandatory for analysis)
  ✅ Simulation mode injects into session state, not a file
"""

import logging

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yaml

from Core.rca_engine import classify_issue
from log_source_manager import (
    get_log_df,
    get_log_stats,
    get_source_label,
    get_source_meta,
    init_log_source,
    is_connected,
)
from Monitors.api_monitor import check_api_health
from Monitors.db_validator import validate_data

st.set_page_config(page_title="RCA Dashboard · AutoRCA", page_icon="📊", layout="wide")
init_log_source()
logger = logging.getLogger("RCA_DASHBOARD")

# ── Load config (only for API / DB checks — NOT for log file path) ────────────
try:
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
except FileNotFoundError:
    config = {"api": {"url": "", "timeout": 3}, "database": {"path": ""}}

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
[data-testid="stSidebar"] { background: #0f172a; }
.rca-card {
    background: #1e293b; border-radius: 10px;
    padding: 20px; border: 1px solid #334155;
}
.error-card {
    border-left: 4px solid #ef4444; background: #ef444410;
    border-radius: 6px; padding: 12px 16px; margin-bottom: 8px;
}
.warn-card {
    border-left: 4px solid #f59e0b; background: #f59e0b10;
    border-radius: 6px; padding: 12px 16px; margin-bottom: 8px;
}
.ok-card {
    border-left: 4px solid #22c55e; background: #22c55e10;
    border-radius: 6px; padding: 16px; border-radius: 8px;
}
</style>
""",
    unsafe_allow_html=True,
)


# ─────────────────────────────────────────────
# EMPTY STATE — no source connected
# ─────────────────────────────────────────────
if not is_connected():
    st.title("📊 RCA Dashboard")
    st.markdown(
        """
    <div style="text-align:center; padding:80px 0;">
        <div style="font-size:64px;">📡</div>
        <h2 style="color:#94a3b8;">No Log Source Connected</h2>
        <p style="color:#64748b; font-size:16px;">
            Go to <strong>Log Source</strong> in the sidebar to upload a file
            or connect your logging system.
        </p>
    </div>
    """,
        unsafe_allow_html=True,
    )
    st.stop()


# ─────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────
st.title("📊 RCA Dashboard")
meta = get_source_meta()
st.caption(
    f"Analysing: **{get_source_label()}** · {meta.get('lines', 0):,} lines · Ingested: {meta.get('ingested_at', 'unknown')}"
)
st.divider()


# ─────────────────────────────────────────────
# DIAGNOSTIC TRIGGER
# ─────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🛠️ Controls")
    run_diagnostic = st.button("▶ Run Full Diagnostic", type="primary", use_container_width=True)
    st.divider()

    st.markdown("### 🧪 Simulation Mode")
    sim_db = st.button("🚨 Inject DB Error", use_container_width=True)
    sim_api = st.button("🌐 Inject API Timeout", use_container_width=True)
    sim_mem = st.button("💾 Inject Memory Warning", use_container_width=True)

    if sim_db:
        df = get_log_df()
        if df is not None:
            import pandas as pd

            fake_row = {
                "timestamp": pd.Timestamp.now(),
                "level": "ERROR",
                "source": "db.connector",
                "format": "standard",
                "message": "DB_CONN_FAIL: Connection refused by host 10.0.0.5:5432",
                "raw": "",
                "extra": {},
                "severity_rank": 4,
                "level_icon": "🔴",
                "is_error": True,
                "is_warning": False,
            }
            st.session_state["log_source"]["df"] = pd.concat([df, pd.DataFrame([fake_row])], ignore_index=True)
            st.session_state["rca_results"] = None
            st.success("DB error injected!")
            st.rerun()

    if sim_api:
        df = get_log_df()
        if df is not None:
            import pandas as pd

            fake_row = {
                "timestamp": pd.Timestamp.now(),
                "level": "ERROR",
                "source": "api.gateway",
                "format": "standard",
                "message": "Connection timeout after 30s: https://api.internal/health",
                "raw": "",
                "extra": {},
                "severity_rank": 4,
                "level_icon": "🔴",
                "is_error": True,
                "is_warning": False,
            }
            st.session_state["log_source"]["df"] = pd.concat([df, pd.DataFrame([fake_row])], ignore_index=True)
            st.session_state["rca_results"] = None
            st.success("API timeout injected!")
            st.rerun()

    if sim_mem:
        df = get_log_df()
        if df is not None:
            import pandas as pd

            fake_row = {
                "timestamp": pd.Timestamp.now(),
                "level": "WARNING",
                "source": "system.monitor",
                "format": "standard",
                "message": "Memory usage at 91% — GC pressure detected",
                "raw": "",
                "extra": {},
                "severity_rank": 3,
                "level_icon": "🟡",
                "is_error": False,
                "is_warning": True,
            }
            st.session_state["log_source"]["df"] = pd.concat([df, pd.DataFrame([fake_row])], ignore_index=True)
            st.session_state["rca_results"] = None
            st.success("Memory warning injected!")
            st.rerun()


# ─────────────────────────────────────────────
# RUN DIAGNOSTIC
# ─────────────────────────────────────────────
if run_diagnostic:
    df = get_log_df()
    with st.spinner("Running full diagnostic…"):
        # Build log result from session state df (not from file)
        errors = df[df["is_error"]] if df is not None else pd.DataFrame()
        warnings = df[df["is_warning"]] if df is not None else pd.DataFrame()

        log_result = {
            "total_errors": len(errors),
            "total_warnings": len(warnings),
            "exceptions": errors["message"].tolist()[:50],
            "formats": df["format"].unique().tolist() if df is not None else [],
        }

        # Optional: API health check
        api_url = config.get("api", {}).get("url", "")
        if api_url:
            api_result = check_api_health(api_url, config["api"].get("timeout", 3))
        else:
            api_result = {
                "status_code": None,
                "response_time": None,
                "error": "No API URL configured",
            }

        # Optional: DB validation
        db_path = config.get("database", {}).get("path", "")
        if db_path:
            db_result = validate_data(db_path)
        else:
            db_result = {"null_email_count": 0, "error": "No DB path configured"}

        classification = classify_issue(api_result, log_result, db_result)

    st.session_state["rca_results"] = {
        "api": api_result,
        "logs": log_result,
        "db": db_result,
        "classification": classification,
    }
    st.session_state["api_history"].append(api_result.get("response_time") or 0)
    st.rerun()


# ─────────────────────────────────────────────
# METRICS ROW
# ─────────────────────────────────────────────
df = get_log_df()
stats = get_log_stats()

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Total Entries", f"{stats.get('total', 0):,}")
m2.metric(
    "Errors",
    f"{stats.get('errors', 0):,}",
    delta="High" if stats.get("errors", 0) > 10 else "Low",
    delta_color="inverse",
)
m3.metric("Warnings", f"{stats.get('warnings', 0):,}")
m4.metric("Log Formats", len(stats.get("formats", [])))

results = st.session_state.get("rca_results")
if results:
    api_rt = results["api"].get("response_time")
    m5.metric("API Latency", f"{api_rt:.3f}s" if api_rt else "N/A")
else:
    m5.metric("API Latency", "—", help="Run diagnostic to check API latency")

st.divider()


# ─────────────────────────────────────────────
# CHARTS ROW
# ─────────────────────────────────────────────
LEVEL_COLORS = {
    "CRITICAL": "#dc2626",
    "FATAL": "#dc2626",
    "ERROR": "#ef4444",
    "WARNING": "#f59e0b",
    "WARN": "#f59e0b",
    "INFO": "#22c55e",
    "DEBUG": "#3b82f6",
    "TRACE": "#94a3b8",
    "UNKNOWN": "#6b7280",
}

col_chart1, col_chart2 = st.columns([1, 2])

with col_chart1:
    st.markdown("#### Error Type Distribution")
    if df is not None and not df.empty:
        level_counts = df["level"].value_counts().reset_index()
        level_counts.columns = ["level", "count"]
        fig = go.Figure(
            go.Pie(
                labels=level_counts["level"],
                values=level_counts["count"],
                hole=0.55,
                marker_colors=[LEVEL_COLORS.get(i, "#6b7280") for i in level_counts["level"]],  # noqa: E741
                textinfo="label+percent",
            )
        )
        fig.update_layout(
            showlegend=False,
            height=280,
            margin=dict(t=10, b=0, l=0, r=0),
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="#cbd5e1",
        )
        st.plotly_chart(fig, use_container_width=True)

with col_chart2:
    st.markdown("#### Log Volume Timeline")
    ts_df = df.dropna(subset=["timestamp"]) if df is not None and "timestamp" in df.columns else pd.DataFrame()
    if not ts_df.empty:
        ts_df = ts_df.copy()
        ts_df["minute"] = ts_df["timestamp"].dt.floor("min")
        timeline = ts_df.groupby(["minute", "level"]).size().reset_index(name="count")
        fig2 = px.bar(
            timeline,
            x="minute",
            y="count",
            color="level",
            color_discrete_map=LEVEL_COLORS,
            labels={"minute": "Time", "count": "Entries", "level": "Level"},
        )
        fig2.update_layout(
            height=280,
            margin=dict(t=10, b=0, l=0, r=0),
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="#cbd5e1",
            plot_bgcolor="rgba(0,0,0,0)",
            legend_title_text="",
        )
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No timestamps in log data — timeline unavailable.")

st.divider()


# ─────────────────────────────────────────────
# RCA RESULTS
# ─────────────────────────────────────────────
st.subheader("🧠 Root Cause Analysis")

if not results:
    st.info("Click **▶ Run Full Diagnostic** in the sidebar to generate RCA results.")
else:
    classification = results["classification"]
    log_res = results["logs"]
    api_res = results["api"]
    db_res = results["db"]

    PLAYBOOKS = {
        "Data Integrity Issue": "Check the database for recent imports or ETL failures. Validate frontend input before write.",
        "Infrastructure Issue": "Check cloud provider status. Review API Gateway, load balancer, and network security groups.",
        "Code Issue": "Review recent commits. Check controller layer for logic errors or unhandled exceptions.",
        "System Healthy": "No action needed. Continue monitoring.",
    }
    PLAYBOOK_STEPS = {
        "Data Integrity Issue": [
            "1. Query DB for null/malformed records",
            "2. Check upstream ETL pipelines",
            "3. Enable strict input validation",
        ],
        "Infrastructure Issue": [
            "1. Check AWS / Azure status dashboard",
            "2. Review firewall & security groups",
            "3. Verify DNS and API Gateway config",
        ],
        "Code Issue": [
            "1. `git log --since='24h'` for recent changes",
            "2. Check error rate in affected service",
            "3. Roll back if critical",
        ],
        "System Healthy": ["✅ System is operating normally", "Continue scheduled monitoring"],
    }

    if "Healthy" in classification:
        st.markdown(
            f'<div class="ok-card">✅ <strong>{classification}</strong> — No issues detected.</div>',
            unsafe_allow_html=True,
        )
    elif "Infrastructure" in classification:
        st.error(f"🚨 {classification}")
    elif "Data" in classification:
        st.warning(f"⚠️ {classification}")
    else:
        st.error(f"🚨 {classification}")

    st.info(f"💡 **Recommended action:** {PLAYBOOKS.get(classification, 'Follow standard SOPs.')}")

    with st.expander("📋 Step-by-Step Remediation Playbook"):
        steps = PLAYBOOK_STEPS.get(classification, [])
        for step in steps:
            st.markdown(f"- {step}")

    st.markdown("#### Diagnostic Summary")
    d1, d2, d3 = st.columns(3)
    with d1:
        api_status = api_res.get("status_code", "N/A")
        st.markdown(
            f"""
        <div class="rca-card">
            <strong style="color:#94a3b8;">API Health</strong><br>
            <span style="font-size:24px; color:{"#22c55e" if api_status == 200 else "#ef4444"};">
                {"✅" if api_status == 200 else "❌"} {api_status or "N/A"}
            </span>
        </div>
        """,
            unsafe_allow_html=True,
        )

    with d2:
        err_count = log_res.get("total_errors", 0)
        st.markdown(
            f"""
        <div class="rca-card">
            <strong style="color:#94a3b8;">Log Errors</strong><br>
            <span style="font-size:24px; color:{"#ef4444" if err_count > 0 else "#22c55e"};">
                {err_count} errors
            </span>
        </div>
        """,
            unsafe_allow_html=True,
        )

    with d3:
        db_nulls = db_res.get("null_email_count", "N/A")
        st.markdown(
            f"""
        <div class="rca-card">
            <strong style="color:#94a3b8;">DB Anomalies</strong><br>
            <span style="font-size:24px; color:{"#ef4444" if db_nulls and db_nulls > 0 else "#22c55e"};">
                {db_nulls} nulls
            </span>
        </div>
        """,
            unsafe_allow_html=True,
        )

    # Top exceptions
    exceptions = log_res.get("exceptions", [])
    if exceptions:
        st.markdown("#### 🔴 Top Errors Found")
        for exc in exceptions[:8]:
            st.markdown(
                f'<div class="error-card"><code style="color:#fca5a5;">{exc[:200]}</code></div>',
                unsafe_allow_html=True,
            )

    # GitHub ticket
    st.divider()
    if st.button("🎫 Create GitHub Incident Ticket"):
        from Core.github_integration import create_github_issue

        report = (
            f"Classification: {classification}\n"
            f"API Status: {api_res.get('status_code')}\n"
            f"Log Errors: {log_res.get('total_errors')}\n"
            f"DB Anomalies: {db_res.get('null_email_count')}\n"
            f"Top error: {exceptions[0] if exceptions else 'None'}"
        )
        create_github_issue(classification, report)
        st.success("GitHub issue created!")
