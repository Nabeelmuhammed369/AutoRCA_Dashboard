"""
log_parser.py — AutoRCA Multi-Format Log Parser
Supports: Plain text, JSON, Syslog, Apache/Nginx, Python/Java stacktraces, CSV logs
Drop this file into your project root alongside Main.py
"""

import csv
import io
import json
import re
from datetime import datetime

import pandas as pd

# ─────────────────────────────────────────────
# LOG LEVEL CONSTANTS
# ─────────────────────────────────────────────
LEVEL_COLORS = {
    "CRITICAL": "🔴",
    "ERROR": "🔴",
    "WARNING": "🟡",
    "WARN": "🟡",
    "INFO": "🟢",
    "DEBUG": "🔵",
    "TRACE": "⚪",
    "FATAL": "🔴",
    "UNKNOWN": "⚫",
}

SEVERITY_RANK = {
    "FATAL": 6,
    "CRITICAL": 5,
    "ERROR": 4,
    "WARNING": 3,
    "WARN": 3,
    "INFO": 2,
    "DEBUG": 1,
    "TRACE": 0,
    "UNKNOWN": -1,
}


# ─────────────────────────────────────────────
# FORMAT DETECTION PATTERNS
# ─────────────────────────────────────────────

# 1. JSON log line  {"timestamp": ..., "level": ..., "message": ...}
PATTERN_JSON = re.compile(r"^\s*\{.*\}\s*$")

# 2. Syslog  Mar  6 14:22:01 hostname app[pid]: message
PATTERN_SYSLOG = re.compile(
    r"^(?P<month>\w{3})\s+(?P<day>\d{1,2})\s+(?P<time>\d{2}:\d{2}:\d{2})\s+"
    r"(?P<host>\S+)\s+(?P<app>[^\[:\s]+)(?:\[(?P<pid>\d+)\])?\s*:\s*(?P<message>.+)$"
)

# 3. Apache/Nginx access log  127.0.0.1 - - [06/Mar/2026:14:00:00 +0000] "GET / HTTP/1.1" 200 1234
PATTERN_APACHE = re.compile(
    r"^(?P<ip>\S+)\s+\S+\s+\S+\s+\[(?P<datetime>[^\]]+)\]\s+"
    r'"(?P<request>[^"]+)"\s+(?P<status>\d{3})\s+(?P<bytes>\S+)'
)

# 4. Standard app log  2026-03-06 14:22:01,123 [ERROR] module - message
PATTERN_STANDARD = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s*"
    r"[\[\(]?(?P<level>FATAL|CRITICAL|ERROR|WARNING|WARN|INFO|DEBUG|TRACE)[\]\)]?\s*"
    r"(?P<source>\S+)?\s*[-–|:]?\s*(?P<message>.+)$",
    re.IGNORECASE,
)

# 5. Python logging  ERROR:module:message  or  INFO:root:message
PATTERN_PYTHON_SIMPLE = re.compile(
    r"^(?P<level>FATAL|CRITICAL|ERROR|WARNING|WARN|INFO|DEBUG|TRACE):(?P<source>[^:]+):(?P<message>.+)$",
    re.IGNORECASE,
)

# 6. Java / Log4j  2026-03-06 14:22:01,123 ERROR com.example.App - message
PATTERN_LOG4J = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2},\d+)\s+"
    r"(?P<level>FATAL|CRITICAL|ERROR|WARN(?:ING)?|INFO|DEBUG|TRACE)\s+"
    r"(?P<source>\S+)\s+-\s+(?P<message>.+)$",
    re.IGNORECASE,
)

# 7. Kubernetes / Docker log  2026-03-06T14:22:01.123Z stderr F message
PATTERN_K8S = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)\s+"
    r"(?P<stream>stdout|stderr)\s+[FP]\s+(?P<message>.+)$"
)

# 8. Windows Event Log  Information  3/6/2026 2:22:01 PM  source  event  message
PATTERN_WINDOWS = re.compile(
    r"^(?P<level>Information|Warning|Error|Critical)\s+"
    r"(?P<datetime>\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)\s+"
    r"(?P<source>\S+)\s+(?P<event_id>\d+)?\s*(?P<message>.+)$",
    re.IGNORECASE,
)

# 9. CSV log — detected by header row analysis
# 10. Stacktrace continuation lines
PATTERN_STACKTRACE = re.compile(r'^\s+at\s+|^\s+File\s+"|Traceback|Exception|Error:')


# ─────────────────────────────────────────────
# NORMALISED LOG ENTRY SCHEMA
# ─────────────────────────────────────────────
def _empty_entry() -> dict:
    return {
        "timestamp": None,
        "level": "UNKNOWN",
        "source": "",
        "message": "",
        "raw": "",
        "format": "unknown",
        "extra": {},
    }


def _normalise_level(raw_level: str) -> str:
    mapping = {
        "warn": "WARNING",
        "warning": "WARNING",
        "err": "ERROR",
        "error": "ERROR",
        "crit": "CRITICAL",
        "critical": "CRITICAL",
        "fatal": "FATAL",
        "info": "INFO",
        "debug": "DEBUG",
        "trace": "TRACE",
        "information": "INFO",
    }
    return mapping.get(raw_level.lower().strip(), raw_level.upper())


def _parse_timestamp(ts_str: str) -> datetime | None:
    formats = [
        "%Y-%m-%d %H:%M:%S,%f",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%d/%b/%Y:%H:%M:%S %z",
        "%m/%d/%Y %I:%M:%S %p",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(ts_str.strip(), fmt)
        except ValueError:
            continue
    return None


# ─────────────────────────────────────────────
# SINGLE-LINE PARSERS
# ─────────────────────────────────────────────


def _try_json(line: str) -> dict | None:
    if not PATTERN_JSON.match(line):
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None

    entry = _empty_entry()
    entry["format"] = "json"
    entry["raw"] = line

    # Common field name variants across logging libraries
    for key in ("timestamp", "time", "@timestamp", "ts", "date", "datetime"):
        if key in obj:
            entry["timestamp"] = _parse_timestamp(str(obj[key]))
            break

    for key in ("level", "severity", "log_level", "lvl", "loglevel"):
        if key in obj:
            entry["level"] = _normalise_level(str(obj[key]))
            break

    for key in ("message", "msg", "text", "log", "body"):
        if key in obj:
            entry["message"] = str(obj[key])
            break

    for key in ("logger", "source", "module", "service", "app", "name"):
        if key in obj:
            entry["source"] = str(obj[key])
            break

    entry["extra"] = {
        k: v
        for k, v in obj.items()
        if k
        not in (
            "timestamp",
            "time",
            "@timestamp",
            "ts",
            "date",
            "datetime",
            "level",
            "severity",
            "log_level",
            "lvl",
            "loglevel",
            "message",
            "msg",
            "text",
            "log",
            "body",
            "logger",
            "source",
            "module",
            "service",
            "app",
            "name",
        )
    }
    return entry


def _try_syslog(line: str) -> dict | None:
    m = PATTERN_SYSLOG.match(line)
    if not m:
        return None
    entry = _empty_entry()
    entry["format"] = "syslog"
    entry["raw"] = line
    entry["source"] = m.group("app")
    entry["message"] = m.group("message")
    raw_msg = m.group("message").upper()
    for lvl in ("CRITICAL", "ERROR", "WARN", "WARNING", "INFO", "DEBUG"):
        if lvl in raw_msg:
            entry["level"] = _normalise_level(lvl)
            break
    else:
        entry["level"] = "INFO"
    try:
        ts_str = f"{m.group('month')} {m.group('day')} {m.group('time')} {datetime.now().year}"
        entry["timestamp"] = datetime.strptime(ts_str, "%b %d %H:%M:%S %Y")
    except ValueError:
        pass
    if m.group("pid"):
        entry["extra"]["pid"] = m.group("pid")
    entry["extra"]["host"] = m.group("host")
    return entry


def _try_apache(line: str) -> dict | None:
    m = PATTERN_APACHE.match(line)
    if not m:
        return None
    entry = _empty_entry()
    entry["format"] = "apache_access"
    entry["raw"] = line
    entry["source"] = "web_server"
    status = int(m.group("status"))
    entry["level"] = "ERROR" if status >= 500 else "WARNING" if status >= 400 else "INFO"
    entry["message"] = f"{m.group('request')} → {status}"
    entry["timestamp"] = _parse_timestamp(m.group("datetime").replace("/", "-", 2).replace(":", " ", 1))
    entry["extra"] = {
        "ip": m.group("ip"),
        "status": status,
        "bytes": m.group("bytes"),
        "request": m.group("request"),
    }
    return entry


def _try_log4j(line: str) -> dict | None:
    m = PATTERN_LOG4J.match(line)
    if not m:
        return None
    entry = _empty_entry()
    entry["format"] = "log4j"
    entry["raw"] = line
    entry["timestamp"] = _parse_timestamp(m.group("timestamp"))
    entry["level"] = _normalise_level(m.group("level"))
    entry["source"] = m.group("source")
    entry["message"] = m.group("message")
    return entry


def _try_standard(line: str) -> dict | None:
    m = PATTERN_STANDARD.match(line)
    if not m:
        return None
    entry = _empty_entry()
    entry["format"] = "standard"
    entry["raw"] = line
    entry["timestamp"] = _parse_timestamp(m.group("timestamp"))
    entry["level"] = _normalise_level(m.group("level"))
    entry["source"] = m.group("source") or ""
    entry["message"] = m.group("message")
    return entry


def _try_k8s(line: str) -> dict | None:
    m = PATTERN_K8S.match(line)
    if not m:
        return None
    entry = _empty_entry()
    entry["format"] = "kubernetes"
    entry["raw"] = line
    entry["timestamp"] = _parse_timestamp(m.group("timestamp"))
    entry["level"] = "ERROR" if m.group("stream") == "stderr" else "INFO"
    entry["message"] = m.group("message")
    entry["extra"] = {"stream": m.group("stream")}
    return entry


def _try_python_simple(line: str) -> dict | None:
    m = PATTERN_PYTHON_SIMPLE.match(line)
    if not m:
        return None
    entry = _empty_entry()
    entry["format"] = "python_simple"
    entry["raw"] = line
    entry["level"] = _normalise_level(m.group("level"))
    entry["source"] = m.group("source")
    entry["message"] = m.group("message")
    return entry


def _try_windows(line: str) -> dict | None:
    m = PATTERN_WINDOWS.match(line)
    if not m:
        return None
    entry = _empty_entry()
    entry["format"] = "windows_event"
    entry["raw"] = line
    entry["timestamp"] = _parse_timestamp(m.group("datetime"))
    entry["level"] = _normalise_level(m.group("level"))
    entry["source"] = m.group("source")
    entry["message"] = m.group("message")
    if m.group("event_id"):
        entry["extra"]["event_id"] = m.group("event_id")
    return entry


def _fallback(line: str) -> dict:
    """Last resort — keep raw line, try to sniff level from content."""
    entry = _empty_entry()
    entry["format"] = "plaintext"
    entry["raw"] = line
    entry["message"] = line.strip()
    upper = line.upper()
    for lvl in ("CRITICAL", "FATAL", "ERROR", "WARN", "WARNING", "INFO", "DEBUG", "TRACE"):
        if lvl in upper:
            entry["level"] = _normalise_level(lvl)
            break
    return entry


# ─────────────────────────────────────────────
# ORDERED PARSER CHAIN
# ─────────────────────────────────────────────
_PARSERS = [
    _try_json,
    _try_log4j,  # try before standard — more specific timestamp pattern
    _try_standard,
    _try_syslog,
    _try_apache,
    _try_k8s,
    _try_python_simple,
    _try_windows,
]


def parse_line(line: str) -> dict | None:
    """Parse a single log line. Returns None for blank/stacktrace continuation."""
    stripped = line.rstrip("\n\r")
    if not stripped.strip():
        return None
    if PATTERN_STACKTRACE.match(stripped):
        return None  # caller handles stacktrace appending
    for parser in _PARSERS:
        result = parser(stripped)
        if result:
            return result
    return _fallback(stripped)


# ─────────────────────────────────────────────
# CSV / TSV DETECTION
# ─────────────────────────────────────────────
def _try_parse_csv(content: str) -> pd.DataFrame | None:
    """Detect and parse CSV/TSV with log-like columns."""
    try:
        sample = content[:4096]
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t|;")
        reader = csv.DictReader(io.StringIO(content), dialect=dialect)
        rows = list(reader)
        if not rows:
            return None
        cols = {c.lower().strip() for c in rows[0].keys()}
        log_indicators = {"level", "severity", "message", "msg", "timestamp", "time", "log"}
        if len(cols & log_indicators) < 2:
            return None
        entries = []
        for row in rows:
            row_lower = {k.lower().strip(): v for k, v in row.items()}
            entry = _empty_entry()
            entry["format"] = "csv"
            entry["raw"] = str(row)
            for k in ("timestamp", "time", "date", "datetime", "@timestamp", "ts"):
                if k in row_lower and row_lower[k]:
                    entry["timestamp"] = _parse_timestamp(row_lower[k])
                    break
            for k in ("level", "severity", "log_level", "lvl"):
                if k in row_lower and row_lower[k]:
                    entry["level"] = _normalise_level(row_lower[k])
                    break
            for k in ("message", "msg", "text", "log", "body"):
                if k in row_lower and row_lower[k]:
                    entry["message"] = row_lower[k]
                    break
            for k in ("logger", "source", "module", "service", "app", "name"):
                if k in row_lower and row_lower[k]:
                    entry["source"] = row_lower[k]
                    break
            entries.append(entry)
        return pd.DataFrame(entries) if entries else None
    except Exception:
        return None


# ─────────────────────────────────────────────
# MAIN PUBLIC FUNCTION
# ─────────────────────────────────────────────
def parse_log_content(content: str) -> pd.DataFrame:
    """
    Parse an entire log file (as string) into a normalised DataFrame.

    Returns columns:
        timestamp, level, source, message, raw, format, extra
    """
    # Try CSV first (header-based detection)
    csv_df = _try_parse_csv(content)
    if csv_df is not None:
        return _enrich(csv_df)

    entries = []
    lines = content.splitlines()
    last_idx = None  # index of last successfully parsed entry (for stacktrace appending)

    for line in lines:
        if PATTERN_STACKTRACE.match(line) and last_idx is not None:
            # Append stacktrace to previous entry's message
            entries[last_idx]["message"] += "\n" + line.rstrip()
            entries[last_idx]["extra"]["has_stacktrace"] = True
            continue

        entry = parse_line(line)
        if entry:
            entries.append(entry)
            last_idx = len(entries) - 1

    if not entries:
        return pd.DataFrame(columns=["timestamp", "level", "source", "message", "raw", "format", "extra"])

    df = pd.DataFrame(entries)
    return _enrich(df)


def _enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Add derived columns useful for the dashboard."""
    df["severity_rank"] = df["level"].map(lambda l: SEVERITY_RANK.get(str(l).upper(), -1))
    df["level_icon"] = df["level"].map(lambda l: LEVEL_COLORS.get(str(l).upper(), "⚫"))
    df["is_error"] = df["level"].isin(["ERROR", "CRITICAL", "FATAL"])
    df["is_warning"] = df["level"] == "WARNING"

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    return df


# ─────────────────────────────────────────────
# SUMMARY HELPER
# ─────────────────────────────────────────────
def summarise(df: pd.DataFrame) -> dict:
    """Return a stats dict for the dashboard header cards."""
    if df.empty:
        return {}
    return {
        "total": len(df),
        "errors": int(df["is_error"].sum()),
        "warnings": int(df["is_warning"].sum()),
        "formats": df["format"].unique().tolist(),
        "time_range": (
            str(df["timestamp"].min())[:19] if "timestamp" in df.columns else "N/A",
            str(df["timestamp"].max())[:19] if "timestamp" in df.columns else "N/A",
        ),
        "top_sources": df["source"].value_counts().head(5).to_dict(),
        "level_counts": df["level"].value_counts().to_dict(),
    }
