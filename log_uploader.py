"""
log_uploader.py — AutoRCA Log Uploader Page
Drop this file into your project root.

Usage in Main.py / dashboard.py:
    from log_uploader import render_log_uploader
    render_log_uploader()

Or call it as a standalone Streamlit page:
    streamlit run log_uploader.py
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from log_parser import parse_log_content, summarise

# ─────────────────────────────────────────────
# SUPPORTED FILE TYPES
# ─────────────────────────────────────────────
ACCEPTED_TYPES = ["log", "txt", "json", "csv", "tsv", "out", "gz"]

SAMPLE_LOG = """\
2026-03-06 14:00:01,123 INFO  app.server - Server started on port 8080
2026-03-06 14:00:05,456 DEBUG app.db    - Connected to database at localhost:5432
2026-03-06 14:01:10,789 WARNING app.auth - Login attempt failed for user nabeel@example.com (3rd attempt)
2026-03-06 14:01:55,001 ERROR app.api   - NullPointerException in /api/v1/rca endpoint
    at com.autorca.api.RcaController.process(RcaController.java:42)
    at com.autorca.api.RcaController.handle(RcaController.java:28)
2026-03-06 14:02:30,332 INFO  app.cache - Cache miss for key: rca_result_20260306
2026-03-06 14:03:45,991 CRITICAL app.monitor - Disk usage at 96% on /dev/sda1 — alert triggered
{"timestamp": "2026-03-06T14:04:00.000Z", "level": "ERROR", "service": "log-agent", "message": "Failed to ship logs to Loki", "retries": 3}
Mar  6 14:05:01 prod-host sshd[2381]: ERROR: authentication failure for user root
127.0.0.1 - - [06/Mar/2026:14:06:00 +0000] "GET /api/health HTTP/1.1" 200 512
127.0.0.1 - - [06/Mar/2026:14:06:05 +0000] "POST /api/rca HTTP/1.1" 500 1024
"""


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def _read_uploaded_file(uploaded_file) -> str:
    """Decode uploaded file bytes to string, handling gzip."""
    raw_bytes = uploaded_file.read()
    if uploaded_file.name.endswith(".gz"):
        import gzip

        try:
            raw_bytes = gzip.decompress(raw_bytes)
        except Exception as e:
            st.error(f"Could not decompress .gz file: {e}")
            return ""
    for encoding in ("utf-8", "latin-1", "cp1252"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    st.warning("⚠️ Could not decode file — showing raw bytes as latin-1.")
    return raw_bytes.decode("latin-1", errors="replace")


def _level_color_map() -> dict:
    """Map log level to hex colour for Plotly."""
    return {
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


# ─────────────────────────────────────────────
# UI SECTIONS
# ─────────────────────────────────────────────
def _render_upload_zone() -> pd.DataFrame | None:
    """Upload area + sample log button. Returns parsed DataFrame or None."""
    st.markdown("### 📂 Upload Log File")

    col_upload, col_sample = st.columns([3, 1])

    with col_upload:
        uploaded = st.file_uploader(
            label="Drag & drop or click to browse",
            type=ACCEPTED_TYPES,
            help="Supports: Plain text, JSON (NDJSON), Syslog, Apache/Nginx, Log4j, Python logging, Kubernetes, Windows Event Log, CSV/TSV",
            label_visibility="collapsed",
        )

    with col_sample:
        st.markdown("<br>", unsafe_allow_html=True)
        use_sample = st.button(
            "🧪 Load Sample Logs",
            use_container_width=True,
            help="Load a built-in multi-format sample to explore the parser",
        )

    if use_sample:
        st.session_state["log_content"] = SAMPLE_LOG
        st.session_state["log_filename"] = "sample_multi_format.log"
        st.info("ℹ️ Loaded sample log with 7 different formats.")

    if uploaded is not None:
        content = _read_uploaded_file(uploaded)
        st.session_state["log_content"] = content
        st.session_state["log_filename"] = uploaded.name

    content = st.session_state.get("log_content")
    filename = st.session_state.get("log_filename", "")

    if not content:
        # Show placeholder
        st.markdown(
            """
            <div style="border: 2px dashed #334155; border-radius: 12px;
                        padding: 48px; text-align: center; color: #64748b;">
                <div style="font-size: 48px;">📋</div>
                <p style="font-size: 18px; margin: 8px 0;">Drop your log file here or click above</p>
                <p style="font-size: 13px;">
                    .log · .txt · .json · .csv · .tsv · .out · .gz
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return None

    with st.spinner(f"Parsing {filename}…"):
        df = parse_log_content(content)
        st.session_state["parsed_log_df"] = df
    if df.empty:
        st.error("❌ No parseable log lines found. Check that the file contains log data.")
        return None

    st.success(f"✅ **{filename}** — parsed **{len(df):,}** log entries")
    return df


def _render_summary_cards(stats: dict):
    """Four KPI cards at the top."""
    c1, c2, c3, c4 = st.columns(4)

    def card(col, icon, label, value, color):
        col.markdown(
            f"""
            <div style="background:{color}18; border-left: 4px solid {color};
                        border-radius: 8px; padding: 16px 20px;">
                <div style="font-size: 28px; font-weight: 700; color: {color};">
                    {icon} {value:,}
                </div>
                <div style="font-size: 13px; color: #94a3b8; margin-top: 4px;">{label}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    card(c1, "📋", "Total Entries", stats.get("total", 0), "#3b82f6")
    card(c2, "🔴", "Errors", stats.get("errors", 0), "#ef4444")
    card(c3, "🟡", "Warnings", stats.get("warnings", 0), "#f59e0b")
    card(c4, "📁", "Formats Found", len(stats.get("formats", [])), "#22c55e")

    if stats.get("time_range"):
        t_from, t_to = stats["time_range"]
        if t_from != "NaT" and t_to != "NaT":
            st.caption(f"🕐 Time range: **{t_from}** → **{t_to}**")

    detected = stats.get("formats", [])
    if detected:
        badges = " · ".join(f"`{f}`" for f in detected if f not in ("unknown", "plaintext"))
        if badges:
            st.caption(f"📡 Detected formats: {badges}")


def _render_charts(df: pd.DataFrame):
    """Level distribution + timeline + top sources."""
    colors = _level_color_map()
    c_left, c_right = st.columns([1, 2])

    # Donut — level distribution
    with c_left:
        level_counts = df["level"].value_counts().reset_index()
        level_counts.columns = ["level", "count"]
        level_counts["color"] = level_counts["level"].map(lambda i: colors.get(i, "#6b7280"))  # noqa: E741
        fig_donut = go.Figure(
            go.Pie(
                labels=level_counts["level"],
                values=level_counts["count"],
                hole=0.55,
                marker_colors=level_counts["color"].tolist(),
                textinfo="label+percent",
                hovertemplate="%{label}: %{value} entries<extra></extra>",
            )
        )
        fig_donut.update_layout(
            title="Log Level Distribution",
            showlegend=False,
            height=300,
            margin=dict(t=40, b=0, l=0, r=0),
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="#cbd5e1",
        )
        st.plotly_chart(fig_donut, use_container_width=True)

    # Timeline — entries per minute (if timestamps exist)
    with c_right:
        ts_df = df.dropna(subset=["timestamp"]) if "timestamp" in df.columns else pd.DataFrame()
        if not ts_df.empty:
            ts_df = ts_df.copy()
            ts_df["minute"] = ts_df["timestamp"].dt.floor("min")
            timeline = ts_df.groupby(["minute", "level"]).size().reset_index(name="count")
            fig_timeline = px.bar(
                timeline,
                x="minute",
                y="count",
                color="level",
                color_discrete_map=colors,
                title="Log Volume Over Time",
                labels={"minute": "Time", "count": "Entries", "level": "Level"},
            )
            fig_timeline.update_layout(
                height=300,
                margin=dict(t=40, b=0, l=0, r=0),
                paper_bgcolor="rgba(0,0,0,0)",
                font_color="#cbd5e1",
                plot_bgcolor="rgba(0,0,0,0)",
                legend_title_text="",
            )
            st.plotly_chart(fig_timeline, use_container_width=True)
        else:
            st.info("ℹ️ No timestamps detected — timeline unavailable.")


def _render_log_table(df: pd.DataFrame):
    """Filterable, searchable log table."""
    st.markdown("### 🔍 Log Explorer")

    # ── Filters ──────────────────────────────
    fc1, fc2, fc3 = st.columns([2, 2, 3])

    with fc1:
        all_levels = sorted(df["level"].unique().tolist())
        sel_levels = st.multiselect("Level", all_levels, default=all_levels, key="filter_level")

    with fc2:
        all_formats = sorted(df["format"].unique().tolist())
        sel_formats = st.multiselect("Format", all_formats, default=all_formats, key="filter_format")

    with fc3:
        search_term = st.text_input(
            "🔎 Search in message / source",
            placeholder="e.g. NullPointerException, app.api",
            key="filter_search",
        )

    # ── Apply filters ─────────────────────────
    mask = df["level"].isin(sel_levels) & df["format"].isin(sel_formats)
    if search_term:
        mask &= df["message"].str.contains(search_term, case=False, na=False) | df["source"].str.contains(
            search_term, case=False, na=False
        )

    filtered = df[mask].copy()
    st.caption(f"Showing **{len(filtered):,}** of **{len(df):,}** entries")

    if filtered.empty:
        st.warning("No entries match the current filters.")
        return

    # ── Display columns ───────────────────────
    display_cols = []
    if "timestamp" in filtered.columns:
        filtered["Time"] = filtered["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S").fillna("—")
        display_cols.append("Time")

    filtered["Level"] = filtered["level_icon"].fillna("") + " " + filtered["level"].fillna("")
    filtered["Source"] = filtered["source"].fillna("—")
    filtered["Message"] = filtered["message"].str[:200]  # truncate for table
    filtered["Format"] = filtered["format"].fillna("—")
    display_cols += ["Level", "Source", "Message", "Format"]

    st.dataframe(
        filtered[display_cols].reset_index(drop=True),
        use_container_width=True,
        height=420,
        column_config={
            "Time": st.column_config.TextColumn("⏰ Time", width="medium"),
            "Level": st.column_config.TextColumn("🚦 Level", width="small"),
            "Source": st.column_config.TextColumn("📦 Source", width="medium"),
            "Message": st.column_config.TextColumn("💬 Message", width="large"),
            "Format": st.column_config.TextColumn("📄 Format", width="small"),
        },
    )

    # ── Full message expander ─────────────────
    with st.expander("📖 Click to view full message of selected row"):
        row_idx = st.number_input("Row #", min_value=0, max_value=max(len(filtered) - 1, 0), step=1)
        if not filtered.empty:
            row = filtered.iloc[int(row_idx)]
            st.code(row["message"], language="")
            if row.get("extra"):
                st.json(row["extra"])

    # ── Export ────────────────────────────────
    csv_out = filtered[display_cols].to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Export filtered logs as CSV",
        data=csv_out,
        file_name="filtered_logs.csv",
        mime="text/csv",
    )


def _render_rca_preview(df: pd.DataFrame):
    """Quick RCA insight panel — errors & stack traces."""
    errors = df[df["is_error"]].copy()
    if errors.empty:
        st.success("✅ No errors or critical events detected in this log file.")
        return

    st.markdown("### 🧠 RCA Quick Insights")
    st.caption(f"Found **{len(errors)}** error-level entries. Connect to your RCA engine to run full analysis.")

    for _, row in errors.head(10).iterrows():
        ts = str(row.get("timestamp", ""))[:19] or "—"
        lvl = row.get("level", "ERROR")
        src = row.get("source", "unknown")
        msg = row.get("message", "")
        has_trace = row.get("extra", {}).get("has_stacktrace", False)

        color = "#dc2626" if lvl in ("CRITICAL", "FATAL") else "#ef4444"
        with st.container():
            st.markdown(
                f"""
                <div style="border-left: 4px solid {color}; background: {color}10;
                            border-radius: 6px; padding: 12px 16px; margin-bottom: 8px;">
                    <span style="color:#94a3b8; font-size:12px;">{ts} · {src}</span>
                    <span style="background:{color}; color:white; font-size:11px;
                                 border-radius:4px; padding:1px 6px; margin-left:8px;">
                        {lvl}
                    </span>
                    {'<span style="background:#7c3aed; color:white; font-size:11px; border-radius:4px; padding:1px 6px; margin-left:4px;">STACKTRACE</span>' if has_trace else ""}
                    <div style="margin-top:6px; font-size:14px; color:#e2e8f0;">
                        {msg[:300].replace(chr(10), "<br>")}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )


# ─────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────
def render_log_uploader():
    """Call this from your Main.py or dashboard.py page router."""
    st.markdown("## 📡 Log Ingestion & Analysis")
    st.markdown(
        "Upload a log file in **any format** — the parser auto-detects the structure and normalises it for RCA analysis.",
        help="Supported: Plain text, JSON/NDJSON, Log4j, Syslog, Apache/Nginx, Kubernetes, Windows Event Log, Python logging, CSV/TSV",
    )
    st.divider()

    df = _render_upload_zone()
    if df is None:
        return

    st.divider()

    stats = summarise(df)
    _render_summary_cards(stats)

    st.divider()
    _render_charts(df)

    st.divider()
    _render_log_table(df)

    st.divider()
    _render_rca_preview(df)


df = st.session_state.get("parsed_log_df")  # set by log_uploader automatically

if df is not None:
    errors = df[df["is_error"]]
    st.write(f"Found {len(errors)} errors to analyse")
else:
    st.warning("No log file uploaded yet. Go to 📡 Log Uploader first.")


# ─────────────────────────────────────────────
# STANDALONE MODE (streamlit run log_uploader.py)
# ─────────────────────────────────────────────
if __name__ == "__main__":
    st.set_page_config(
        page_title="AutoRCA — Log Uploader",
        page_icon="📡",
        layout="wide",
    )
    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"] { background: #0f172a; }
        [data-testid="stSidebar"]          { background: #1e293b; }
        h1, h2, h3, p, label              { color: #e2e8f0 !important; }
        .stDataFrame                       { border-radius: 8px; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    render_log_uploader()
