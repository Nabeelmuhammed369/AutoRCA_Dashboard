"""
# Monitors/log_analyzer.py — Refactored Log Analyzer
# ────────────────────────────────────────────────────
# What changed:
#   ✅ analyze_logs() no longer takes a file path
#   ✅ analyze_logs() accepts a pandas DataFrame from session state
#   ✅ analyze_logs_from_text() accepts raw string content
#   ✅ Old file-based function kept as analyze_logs_from_file() for CLI use only

# Usage in dashboard / rca_engine:
#     from log_source_manager import get_log_df
#     from Monitors.log_analyzer import analyze_logs

#     df = get_log_df()
#     result = analyze_logs(df)
#"""

import logging
import os

import pandas as pd

logger = logging.getLogger("LOG_ANALYZER")


# ─────────────────────────────────────────────
# PRIMARY FUNCTION (session state driven)
# ─────────────────────────────────────────────
def analyze_logs(df: pd.DataFrame | None) -> dict:
    """
    Analyse a normalised log DataFrame (from log_source_manager.get_log_df()).

    Returns a result dict compatible with the existing rca_engine.classify_issue().
    """
    if df is None or df.empty:
        logger.warning("analyze_logs called with empty or None DataFrame.")
        return {
            "total_errors": 0,
            "total_warnings": 0,
            "exceptions": [],
            "formats": [],
            "top_sources": {},
            "has_stacktrace": False,
        }

    errors = df[df["is_error"]] if "is_error" in df.columns else pd.DataFrame()
    warnings = df[df["is_warning"]] if "is_warning" in df.columns else pd.DataFrame()

    # Exceptions: all error messages, prioritise CRITICAL/FATAL first
    exceptions = []
    for level in ("FATAL", "CRITICAL", "ERROR"):
        subset = df[df["level"] == level]["message"].dropna().tolist() if "level" in df.columns else []
        exceptions.extend(subset)
    exceptions = exceptions[:100]  # cap for performance

    # ── Stacktrace detection ─────────────────────────────────────────────────
    # Check "extra" column first (structured), then fall back to message text
    _STACKTRACE_LEVELS = {"FATAL", "CRITICAL", "ERROR", "WARNING", "WARN", "INFO", "DEBUG", "NOTICE", "SEVERE"}
    has_stacktrace = False
    if "extra" in df.columns:
        has_stacktrace = df["extra"].apply(lambda x: isinstance(x, dict) and x.get("has_stacktrace", False)).any()
    if not has_stacktrace and "message" in df.columns:
        has_stacktrace = bool(
            df["message"]
            .astype(str)
            .str.contains(r"Traceback|\bat\s+\w|\bFile\s+\"|\bException in thread", regex=True, na=False)
            .any()
        )

    top_sources = {}
    if "source" in df.columns:
        top_sources = df["source"].value_counts().head(5).to_dict()

    # ── Format detection ─────────────────────────────────────────────────────
    # Use "format" column if it has meaningful values; otherwise infer from content.
    # Strip any value that is a log-level word or a generic sentinel — these are
    # not format names and appear when the parser falls back to writing the level
    # into the format column.
    _NOT_FORMAT = {
        "ERROR",
        "CRITICAL",
        "WARNING",
        "WARN",
        "INFO",
        "DEBUG",
        "NOTICE",
        "FATAL",
        "SEVERE",
        "UNKNOWN",
        "NONE",
        "",
    }
    raw_formats = df["format"].unique().tolist() if "format" in df.columns else []
    meaningful = [f for f in raw_formats if f and str(f).upper() not in _NOT_FORMAT]
    formats = list(meaningful)

    # Infer JSON format from message content when not already present
    if "json" not in formats and "message" in df.columns:
        if df["message"].astype(str).str.strip().str.startswith("{").any():
            formats.append("json")

    # Fall back to "plain" so the list is never empty for non-empty DataFrames
    if not formats:
        formats = ["plain"]

    result = {
        "total_errors": int(errors.shape[0]),
        "total_warnings": int(warnings.shape[0]),
        "exceptions": exceptions,
        "formats": formats,
        "top_sources": top_sources,
        "has_stacktrace": bool(has_stacktrace),
    }

    logger.info(
        f"Log analysis complete: {result['total_errors']} errors, "
        f"{result['total_warnings']} warnings, {len(formats)} formats"
    )
    return result


# ─────────────────────────────────────────────
# CONVENIENCE: analyse raw text directly
# ─────────────────────────────────────────────
def analyze_logs_from_text(raw_content: str) -> dict:
    """
    Parse raw log text and analyse it.
    Used by api_server.py /api/run after /api/ingest.
    """
    from log_parser import parse_log_content

    df = parse_log_content(raw_content)
    return analyze_logs(df)


# ─────────────────────────────────────────────
# LEGACY: file-based (CLI / backward compat)
# ─────────────────────────────────────────────
def analyze_logs_from_file(log_file_path: str) -> dict:
    """
    Read a log file from disk and analyse it.
    ONLY use this for CLI / scripted runs where no UI session exists.
    Do NOT call this from the Streamlit dashboard.
    """
    if not log_file_path or not os.path.exists(log_file_path):
        logger.error(f"Log file not found: {log_file_path}")
        return {
            "total_errors": 0,
            "total_warnings": 0,
            "exceptions": [],
            "formats": [],
            "top_sources": {},
            "has_stacktrace": False,
            "error": f"File not found: {log_file_path}",
        }
    try:
        with open(log_file_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
        return analyze_logs_from_text(content)
    except Exception as e:
        logger.exception(f"Failed to read log file: {log_file_path}")
        return {"total_errors": 0, "exceptions": [], "error": str(e)}
