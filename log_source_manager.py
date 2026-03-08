"""
log_source_manager.py — AutoRCA Central Log Source Manager
─────────────────────────────────────────────────────────────
Single source of truth for where logs come from.

All other modules (dashboard, rca_engine, api_server) read from
st.session_state["log_source"] instead of any hardcoded file path.

Log source schema stored in session_state:
{
    "type":       "upload" | "loki" | "elasticsearch" | "s3" | "api_push" | None,
    "label":      "Human-readable name shown in the UI",
    "connected":  True | False,
    "df":         pd.DataFrame | None,    # parsed, normalised log entries
    "raw":        str | None,             # raw log text (for re-parsing)
    "meta": {
        "filename":   str,
        "lines":      int,
        "ingested_at": str ISO timestamp,
        "source_detail": str,             # e.g. Loki URL, S3 bucket, filename
    }
}
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from log_parser import parse_log_content, summarise


# ─────────────────────────────────────────────
# SESSION STATE INITIALISATION
# ─────────────────────────────────────────────
def init_log_source():
    """Call once at the top of Main.py — safe to call repeatedly."""
    if "log_source" not in st.session_state:
        st.session_state["log_source"] = _empty_source()
    if "rca_results" not in st.session_state:
        st.session_state["rca_results"] = None
    if "api_history" not in st.session_state:
        st.session_state["api_history"] = []


def _empty_source() -> dict:
    return {
        "type": None,
        "label": "No source connected",
        "connected": False,
        "df": None,
        "raw": None,
        "meta": {},
    }


# ─────────────────────────────────────────────
# PUBLIC: SET LOG SOURCE (called by each connector)
# ─────────────────────────────────────────────
def set_log_source_from_upload(filename: str, raw_content: str):
    """Called when user uploads a file through the UI."""
    df = parse_log_content(raw_content)
    st.session_state["log_source"] = {
        "type": "upload",
        "label": f"📁 {filename}",
        "connected": True,
        "df": df,
        "raw": raw_content,
        "meta": {
            "filename": filename,
            "lines": len(raw_content.splitlines()),
            "ingested_at": _now(),
            "source_detail": f"Uploaded file: {filename}",
        },
    }
    # Clear old RCA results — new source = fresh analysis
    st.session_state["rca_results"] = None


def set_log_source_from_api_push(raw_content: str, source_label: str = "API Push"):
    """Called by api_server.py /api/ingest endpoint via session bridge."""
    df = parse_log_content(raw_content)
    st.session_state["log_source"] = {
        "type": "api_push",
        "label": f"📡 {source_label}",
        "connected": True,
        "df": df,
        "raw": raw_content,
        "meta": {
            "filename": "api_push",
            "lines": len(raw_content.splitlines()),
            "ingested_at": _now(),
            "source_detail": source_label,
        },
    }
    st.session_state["rca_results"] = None


def set_log_source_from_integration(
    integration_type: str,
    label: str,
    raw_content: str,
    source_detail: str = "",
):
    """Called by Loki / Elasticsearch / S3 connectors."""
    df = parse_log_content(raw_content)
    st.session_state["log_source"] = {
        "type": integration_type,
        "label": label,
        "connected": True,
        "df": df,
        "raw": raw_content,
        "meta": {
            "filename": integration_type,
            "lines": len(raw_content.splitlines()),
            "ingested_at": _now(),
            "source_detail": source_detail,
        },
    }
    st.session_state["rca_results"] = None


def clear_log_source():
    """Disconnect current source."""
    st.session_state["log_source"] = _empty_source()
    st.session_state["rca_results"] = None


# ─────────────────────────────────────────────
# PUBLIC: READ LOG SOURCE (called by dashboard, rca_engine)
# ─────────────────────────────────────────────
def get_log_df() -> pd.DataFrame | None:
    """Returns the normalised DataFrame or None if no source connected."""
    src = st.session_state.get("log_source", {})
    return src.get("df")


def get_log_raw() -> str | None:
    src = st.session_state.get("log_source", {})
    return src.get("raw")


def is_connected() -> bool:
    src = st.session_state.get("log_source", {})
    return src.get("connected", False)


def get_source_meta() -> dict:
    src = st.session_state.get("log_source", {})
    return src.get("meta", {})


def get_source_label() -> str:
    src = st.session_state.get("log_source", {})
    return src.get("label", "No source connected")


def get_source_type() -> str | None:
    src = st.session_state.get("log_source", {})
    return src.get("type")


def get_log_stats() -> dict:
    """Returns summarise() dict or empty dict."""
    df = get_log_df()
    if df is None or df.empty:
        return {}
    return summarise(df)


# ─────────────────────────────────────────────
# PRIVATE
# ─────────────────────────────────────────────
def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
