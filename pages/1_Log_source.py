"""
pages/1_Log_Source.py — AutoRCA Log Source Connector
──────────────────────────────────────────────────────
This is the FIRST page users see. They either:
  [A] Upload a log file  (immediate, no config)
  [B] Connect an integration (Loki, Elasticsearch, S3, HTTP endpoint)

After connecting, all other pages (RCA Dashboard, Log Explorer) read
from st.session_state["log_source"] via log_source_manager.
"""

import gzip
import json

import requests
import streamlit as st

from log_source_manager import (
    clear_log_source,
    get_source_label,
    get_source_meta,
    init_log_source,
    is_connected,
    set_log_source_from_integration,
    set_log_source_from_upload,
)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Log Source · AutoRCA", page_icon="📡", layout="wide")
init_log_source()

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
[data-testid="stSidebar"] { background: #0f172a; }
.tab-card {
    background: #1e293b; border-radius: 12px;
    padding: 24px; border: 1px solid #334155;
}
.connected-banner {
    background: #16a34a18; border: 1px solid #16a34a;
    border-radius: 10px; padding: 16px 20px; margin-bottom: 16px;
}
.integration-card {
    background: #1e293b; border-radius: 10px; padding: 20px;
    border: 1px solid #334155; margin-bottom: 12px;
    transition: border-color 0.2s;
}
</style>
""",
    unsafe_allow_html=True,
)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
ACCEPTED_TYPES = ["log", "txt", "json", "csv", "tsv", "out", "gz"]


def _decode_file(uploaded_file) -> str:
    raw = uploaded_file.read()
    if uploaded_file.name.endswith(".gz"):
        try:
            raw = gzip.decompress(raw)
        except Exception as e:
            st.error(f"Could not decompress .gz: {e}")
            return ""
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _fetch_loki_logs(loki_url: str, query: str, hours: int, limit: int) -> str:
    """Query Grafana Loki HTTP API and return raw NDJSON lines."""
    import time

    end_ns = int(time.time() * 1e9)
    start_ns = end_ns - (hours * 3600 * int(1e9))
    endpoint = loki_url.rstrip("/") + "/loki/api/v1/query_range"
    try:
        resp = requests.get(
            endpoint,
            params={
                "query": query,
                "start": start_ns,
                "end": end_ns,
                "limit": limit,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        lines = []
        for stream in data.get("data", {}).get("result", []):
            for ts, msg in stream.get("values", []):
                lines.append(msg)
        return "\n".join(lines)
    except requests.RequestException as e:
        st.error(f"Loki connection error: {e}")
        return ""


def _fetch_elasticsearch_logs(es_url: str, index: str, query: str, size: int) -> str:
    """Query Elasticsearch and return NDJSON log lines."""
    endpoint = es_url.rstrip("/") + f"/{index}/_search"
    body = {
        "size": size,
        "sort": [{"@timestamp": {"order": "desc"}}],
        "query": {"query_string": {"query": query}} if query else {"match_all": {}},
    }
    try:
        resp = requests.post(endpoint, json=body, timeout=15, headers={"Content-Type": "application/json"})
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
        lines = [json.dumps(h.get("_source", {})) for h in hits]
        return "\n".join(lines)
    except requests.RequestException as e:
        st.error(f"Elasticsearch connection error: {e}")
        return ""


def _fetch_s3_logs(bucket: str, prefix: str, aws_key: str, aws_secret: str, region: str) -> str:
    """Download and concatenate log files from S3 prefix."""
    try:
        import boto3

        s3 = boto3.client(
            "s3",
            aws_access_key_id=aws_key,
            aws_secret_access_key=aws_secret,
            region_name=region,
        )
        objs = s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
        if not objs:
            st.warning("No objects found at that S3 path.")
            return ""
        lines = []
        for obj in objs[:20]:  # cap at 20 files
            body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
            try:
                lines.append(body.decode("utf-8"))
            except Exception:
                lines.append(body.decode("latin-1", errors="replace"))
        return "\n".join(lines)
    except ImportError:
        st.error("boto3 not installed. Run: pip install boto3")
        return ""
    except Exception as e:
        st.error(f"S3 error: {e}")
        return ""


def _fetch_http_logs(url: str, headers_raw: str, method: str) -> str:
    """Fetch logs from a custom HTTP endpoint."""
    try:
        hdrs = {}
        if headers_raw.strip():
            for line in headers_raw.strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    hdrs[k.strip()] = v.strip()
        fn = requests.get if method == "GET" else requests.post
        resp = fn(url, headers=hdrs, timeout=20)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        st.error(f"HTTP fetch error: {e}")
        return ""


# ─────────────────────────────────────────────
# CONNECTED BANNER
# ─────────────────────────────────────────────
def _render_connected_banner():
    meta = get_source_meta()
    st.markdown(
        f"""
    <div class="connected-banner">
        <strong style="color:#4ade80;">● Currently Connected:</strong>
        <span style="color:#e2e8f0; margin-left:8px;">{get_source_label()}</span>
        <span style="color:#64748b; font-size:12px; margin-left:16px;">
            {meta.get("lines", 0):,} lines · ingested {meta.get("ingested_at", "")}
        </span>
    </div>
    """,
        unsafe_allow_html=True,
    )
    if st.button("🔌 Disconnect & Clear", type="secondary"):
        clear_log_source()
        st.rerun()


# ─────────────────────────────────────────────
# TAB A — FILE UPLOAD
# ─────────────────────────────────────────────
def _render_upload_tab():
    st.markdown("### 📂 Upload Log File")
    st.markdown(
        "Upload a copy of your log file. Supports **9 formats** — the parser auto-detects and normalises everything.",
        help="Plain text, JSON/NDJSON, Log4j, Syslog, Apache/Nginx, Kubernetes, Python logging, Windows Event Log, CSV/TSV",
    )

    uploaded = st.file_uploader(
        "Drag & drop or click to browse",
        type=ACCEPTED_TYPES,
        label_visibility="collapsed",
    )

    if uploaded:
        with st.spinner(f"Parsing {uploaded.name}…"):
            content = _decode_file(uploaded)

        if content:
            set_log_source_from_upload(uploaded.name, content)
            st.success(f"✅ **{uploaded.name}** connected successfully!")
            st.rerun()

    st.divider()
    st.markdown("""
    **Supported file types**

    | Extension | Formats |
    |-----------|---------|
    | `.log`, `.txt` | Standard app logs, syslog, Log4j, Apache/Nginx, Python logging |
    | `.json` | JSON / NDJSON structured logs (one JSON object per line) |
    | `.csv`, `.tsv` | CSV/TSV exported logs with timestamp + level + message columns |
    | `.gz` | Any of the above, gzip-compressed |
    | `.out` | Process/service output files |
    """)


# ─────────────────────────────────────────────
# TAB B — INTEGRATIONS
# ─────────────────────────────────────────────
def _render_integrations_tab():
    st.markdown("### 🔗 Connect Your Log Warehouse")
    st.markdown("Connect AutoRCA directly to your centralized logging system. Logs are fetched live — no manual file export needed.")

    integration = st.selectbox(
        "Select your logging platform",
        options=[
            "— Select —",
            "Grafana Loki",
            "Elasticsearch / OpenSearch",
            "Amazon S3",
            "Custom HTTP Endpoint",
        ],
        key="integration_type",
    )

    st.divider()

    if integration == "Grafana Loki":
        _render_loki_form()

    elif integration == "Elasticsearch / OpenSearch":
        _render_elasticsearch_form()

    elif integration == "Amazon S3":
        _render_s3_form()

    elif integration == "Custom HTTP Endpoint":
        _render_http_form()

    else:
        # Show integration overview cards
        cols = st.columns(2)
        integrations = [
            (
                "🟠",
                "Grafana Loki",
                "Pull logs from Loki using LogQL queries. Works with Grafana Cloud and self-hosted.",
            ),
            (
                "🟡",
                "Elasticsearch",
                "Query any Elasticsearch or OpenSearch index. Supports KQL / Lucene query syntax.",
            ),
            (
                "🔵",
                "Amazon S3",
                "Pull log files from any S3 bucket or compatible storage (MinIO, R2, GCS).",
            ),
            (
                "⚪",
                "Custom HTTP Endpoint",
                "Fetch logs from any REST API — Splunk, Datadog, custom log server, or HTTP export.",
            ),
        ]
        for i, (icon, name, desc) in enumerate(integrations):
            with cols[i % 2]:
                st.markdown(
                    f"""
                <div class="integration-card">
                    <div style="font-size:24px; margin-bottom:8px;">{icon}</div>
                    <strong style="color:#e2e8f0;">{name}</strong>
                    <p style="color:#64748b; font-size:13px; margin:6px 0 0;">{desc}</p>
                </div>
                """,
                    unsafe_allow_html=True,
                )


def _render_loki_form():
    st.markdown("#### 🟠 Grafana Loki")
    with st.form("loki_form"):
        loki_url = st.text_input("Loki URL", placeholder="http://localhost:3100")
        query = st.text_input("LogQL Query", placeholder='{app="myapp"} |= "error"', value='{job="varlogs"}')
        c1, c2 = st.columns(2)
        hours = c1.number_input("Fetch last N hours", min_value=1, max_value=168, value=1)
        limit = c2.number_input("Max log lines", min_value=100, max_value=10000, value=1000)
        submitted = st.form_submit_button("🔌 Connect to Loki", type="primary")

    if submitted:
        if not loki_url:
            st.error("Loki URL is required.")
            return
        with st.spinner("Fetching logs from Loki…"):
            content = _fetch_loki_logs(loki_url, query, int(hours), int(limit))
        if content:
            set_log_source_from_integration("loki", f"🟠 Loki · {loki_url}", content, source_detail=f"Query: {query}")
            st.success("✅ Loki connected!")
            st.rerun()
        else:
            st.error("No logs returned. Check URL and query.")


def _render_elasticsearch_form():
    st.markdown("#### 🟡 Elasticsearch / OpenSearch")
    with st.form("es_form"):
        es_url = st.text_input("Elasticsearch URL", placeholder="http://localhost:9200")
        index = st.text_input("Index / Index Pattern", placeholder="logs-*", value="logs-*")
        query = st.text_input("Query (Lucene)", placeholder="level:ERROR", value="*")
        size = st.number_input("Max documents", min_value=100, max_value=10000, value=1000)
        submitted = st.form_submit_button("🔌 Connect to Elasticsearch", type="primary")

    if submitted:
        if not es_url:
            st.error("Elasticsearch URL is required.")
            return
        with st.spinner("Querying Elasticsearch…"):
            content = _fetch_elasticsearch_logs(es_url, index, query, int(size))
        if content:
            set_log_source_from_integration(
                "elasticsearch",
                f"🟡 ES · {index}",
                content,
                source_detail=f"Index: {index}, Query: {query}",
            )
            st.success("✅ Elasticsearch connected!")
            st.rerun()


def _render_s3_form():
    st.markdown("#### 🔵 Amazon S3")
    st.info("💡 For production, use IAM roles instead of access keys. Keys entered here are not stored.")
    with st.form("s3_form"):
        bucket = st.text_input("S3 Bucket Name", placeholder="my-log-bucket")
        prefix = st.text_input("Key Prefix (folder path)", placeholder="logs/2026/03/")
        region = st.text_input("AWS Region", value="us-east-1")
        aws_key = st.text_input("AWS Access Key ID", type="password")
        aws_secret = st.text_input("AWS Secret Access Key", type="password")
        submitted = st.form_submit_button("🔌 Connect to S3", type="primary")

    if submitted:
        if not bucket:
            st.error("Bucket name is required.")
            return
        with st.spinner("Fetching log files from S3…"):
            content = _fetch_s3_logs(bucket, prefix, aws_key, aws_secret, region)
        if content:
            set_log_source_from_integration("s3", f"🔵 S3 · {bucket}/{prefix}", content, source_detail=f"s3://{bucket}/{prefix}")
            st.success("✅ S3 connected!")
            st.rerun()


def _render_http_form():
    st.markdown("#### ⚪ Custom HTTP Endpoint")
    st.markdown("Use this to connect Splunk, Datadog, or any custom REST API that returns raw log text or JSON.")
    with st.form("http_form"):
        url = st.text_input("Endpoint URL", placeholder="https://logs.example.com/api/export")
        method = st.selectbox("Method", ["GET", "POST"])
        headers_raw = st.text_area(
            "Headers (one per line, Key: Value)",
            placeholder="Authorization: Bearer YOUR_TOKEN\nX-API-Key: abc123",
            height=100,
        )
        submitted = st.form_submit_button("🔌 Fetch Logs", type="primary")

    if submitted:
        if not url:
            st.error("URL is required.")
            return
        with st.spinner("Fetching logs…"):
            content = _fetch_http_logs(url, headers_raw, method)
        if content:
            set_log_source_from_integration("http", f"⚪ HTTP · {url[:40]}", content, source_detail=f"URL: {url}")
            st.success("✅ Logs fetched!")
            st.rerun()


# ─────────────────────────────────────────────
# TAB C — API PUSH (for log agents)
# ─────────────────────────────────────────────
def _render_api_push_tab():
    st.markdown("### 📡 Push Logs via API")
    st.markdown(
        "If you have a log agent (Fluentd, Logstash, Vector, Filebeat) or a custom pipeline, "
        "configure it to **POST logs directly** to AutoRCA's ingest endpoint."
    )

    st.divider()

    col1, col2 = st.columns([3, 2])

    with col1:
        st.markdown("#### Endpoint")
        st.code("POST http://your-autorca-server:8000/api/ingest", language="bash")

        st.markdown("#### Required Headers")
        st.code(
            """X-API-Key: your_autorca_api_key
Content-Type: text/plain""",
            language="http",
        )

        st.markdown("#### Request Body")
        st.markdown("Send raw log text as the request body — any supported format.")
        st.code(
            """curl -X POST http://localhost:8000/api/ingest \\
  -H "X-API-Key: YOUR_KEY" \\
  -H "Content-Type: text/plain" \\
  --data-binary @/var/log/app.log""",
            language="bash",
        )

        st.markdown("#### Fluentd Config Example")
        st.code(
            """<match app.**>
  @type http
  endpoint http://your-autorca-server:8000/api/ingest
  headers {"X-API-Key": "YOUR_KEY"}
  content_type text/plain
</match>""",
            language="xml",
        )

    with col2:
        st.markdown("#### Compatible Agents")
        agents = [
            ("📦", "Fluentd / Fluent Bit", "Use http output plugin"),
            ("📦", "Logstash", "Use http output plugin"),
            ("📦", "Vector", "Use http sink"),
            ("📦", "Filebeat", "Use logstash output → AutoRCA"),
            ("📦", "Promtail", "Use client → push_url"),
            ("📦", "Custom Script", "Any language with HTTP POST"),
        ]
        for icon, name, note in agents:
            st.markdown(
                f"""
            <div style="background:#1e293b; border-radius:8px; padding:10px 14px;
                        margin-bottom:8px; border:1px solid #334155;">
                <strong style="color:#e2e8f0;">{icon} {name}</strong>
                <div style="color:#64748b; font-size:12px;">{note}</div>
            </div>
            """,
                unsafe_allow_html=True,
            )

        st.markdown("#### Security")
        st.markdown("""
        - Set `AUTORCA_API_KEY` in your `.env` file
        - Use HTTPS in production (put AutoRCA behind nginx)
        - Rate limit: 10 pushes/minute per IP
        """)


# ─────────────────────────────────────────────
# MAIN PAGE RENDER
# ─────────────────────────────────────────────
st.title("📡 Log Source")
st.markdown("Connect AutoRCA to your log data — choose how you want to ingest logs.")

if is_connected():
    _render_connected_banner()

st.divider()

tab_upload, tab_integrate, tab_api = st.tabs(
    [
        "📂 Upload File",
        "🔗 Connect Integration",
        "📡 API Push (for agents)",
    ]
)

with tab_upload:
    _render_upload_tab()

with tab_integrate:
    _render_integrations_tab()

with tab_api:
    _render_api_push_tab()
