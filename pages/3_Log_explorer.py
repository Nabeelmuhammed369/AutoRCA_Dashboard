"""
pages/3_Log_Explorer.py — AutoRCA Log Explorer
─────────────────────────────────────────────────
Searchable, filterable log table.
Reads entirely from st.session_state — no file access.
"""

import streamlit as st

from log_source_manager import (
    get_log_df,
    get_source_label,
    get_source_meta,
    init_log_source,
    is_connected,
)

st.set_page_config(page_title="Log Explorer · AutoRCA", page_icon="🔍", layout="wide")
init_log_source()

st.markdown(
    """
<style>
[data-testid="stSidebar"] { background: #0f172a; }
</style>
""",
    unsafe_allow_html=True,
)

st.title("🔍 Log Explorer")

if not is_connected():
    st.markdown(
        """
    <div style="text-align:center; padding:80px 0;">
        <div style="font-size:64px;">📡</div>
        <h2 style="color:#94a3b8;">No Log Source Connected</h2>
        <p style="color:#64748b;">Go to <strong>Log Source</strong> to upload or connect logs first.</p>
    </div>
    """,
        unsafe_allow_html=True,
    )
    st.stop()

meta = get_source_meta()
st.caption(
    f"Source: **{get_source_label()}** · {meta.get('lines', 0):,} lines · Ingested: {meta.get('ingested_at', '')}"
)
st.divider()

df = get_log_df()
if df is None or df.empty:
    st.warning("Log data is empty. Try reconnecting your source.")
    st.stop()

# ── Filters ────────────────────────────────────────────────────────────────────
fc1, fc2, fc3, fc4 = st.columns([2, 2, 2, 3])

with fc1:
    all_levels = sorted(df["level"].unique().tolist())
    sel_levels = st.multiselect("Level", all_levels, default=all_levels)

with fc2:
    all_formats = sorted(df["format"].unique().tolist())
    sel_formats = st.multiselect("Format", all_formats, default=all_formats)

with fc3:
    all_sources = sorted(df["source"].dropna().unique().tolist())
    sel_sources = st.multiselect("Source / Module", all_sources, default=all_sources, placeholder="All sources")

with fc4:
    search = st.text_input("🔎 Search message / source", placeholder="e.g. NullPointerException, timeout, DB_CONN_FAIL")

# ── Apply filters ──────────────────────────────────────────────────────────────
mask = df["level"].isin(sel_levels) & df["format"].isin(sel_formats)
if sel_sources:
    mask &= df["source"].isin(sel_sources)
if search:
    mask &= df["message"].str.contains(search, case=False, na=False) | df["source"].str.contains(
        search, case=False, na=False
    )

filtered = df[mask].copy()

col_info, col_export = st.columns([3, 1])
col_info.caption(f"Showing **{len(filtered):,}** of **{len(df):,}** entries")

with col_export:
    csv_out = filtered.drop(columns=["extra", "severity_rank", "level_icon", "is_error", "is_warning"], errors="ignore")
    st.download_button(
        "⬇️ Export CSV",
        csv_out.to_csv(index=False).encode(),
        "filtered_logs.csv",
        "text/csv",
        use_container_width=True,
    )

# ── Table ──────────────────────────────────────────────────────────────────────
display = filtered.copy()
if "timestamp" in display.columns:
    display["Time"] = display["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S").fillna("—")
else:
    display["Time"] = "—"

display["Level"] = (display["level_icon"].fillna("") + " " + display["level"].fillna("")).str.strip()
display["Source"] = display["source"].fillna("—")
display["Message"] = display["message"].str[:300]
display["Format"] = display["format"].fillna("—")

st.dataframe(
    display[["Time", "Level", "Source", "Message", "Format"]].reset_index(drop=True),
    use_container_width=True,
    height=500,
    column_config={
        "Time": st.column_config.TextColumn("⏰ Time", width="medium"),
        "Level": st.column_config.TextColumn("🚦 Level", width="small"),
        "Source": st.column_config.TextColumn("📦 Source", width="medium"),
        "Message": st.column_config.TextColumn("💬 Message", width="large"),
        "Format": st.column_config.TextColumn("📄 Format", width="small"),
    },
)

# ── Full message viewer ────────────────────────────────────────────────────────
if not filtered.empty:
    with st.expander("📖 View full message for a specific row"):
        idx = st.number_input("Row #", min_value=0, max_value=max(len(filtered) - 1, 0), step=1)
        row = filtered.iloc[int(idx)]
        st.markdown(f"**Level:** `{row['level']}` · **Source:** `{row['source']}` · **Format:** `{row['format']}`")
        st.code(row["message"], language="")
        if row.get("extra") and row["extra"]:
            st.json(row["extra"])
