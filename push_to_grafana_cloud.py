"""
push_to_grafana_cloud.py
────────────────────────
Pushes realistic test logs to your FREE Grafana Cloud Loki instance.
Sign up free at: https://grafana.com/auth/sign-up

After signing up:
  1. Go to your Grafana Cloud stack
  2. Click "Details" next to Loki
  3. Copy: URL, User (numeric ID), and generate an API token

Usage — set env vars then run:
  set LOKI_URL=https://logs-prod-006.grafana.net
  set LOKI_USER=123456
  set LOKI_TOKEN=glc_xxxx...
  python push_to_grafana_cloud.py
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request

# ── Credentials — read from environment variables ─────────────────────────────
# Set these in your shell before running. Never hardcode real tokens here.
LOKI_URL = os.getenv("LOKI_URL", "https://logs-prod-XXX.grafana.net")
LOKI_USER = os.getenv("LOKI_USER", "123456")
LOKI_TOKEN = os.getenv("LOKI_TOKEN", "")  # no default — must be set via env
# ─────────────────────────────────────────────────────────────────────────────

TEST_LOGS = [
    (0, "ERROR [Database] DB_CONN_FAIL: Connection refused at localhost:5432"),
    (1, "CRITICAL [Database] Complete database outage detected — all queries failing"),
    (2, "ERROR [Network] Connection timed out after 30s — host db.internal unreachable"),
    (3, "ERROR [API] Gateway returned 502 Bad Gateway on /api/v1/users"),
    (4, "ERROR [Firewall] Access denied — rule violation on port 443 from 10.0.0.5"),
    (5, "ERROR [ActiveDirectory] LDAP bind failed for user admin@corp.local"),
    (6, "ERROR NullPointerException in UserService.java:88"),
    (7, "WARNING High memory usage detected — 91% of 8GB utilised"),
    (8, "ERROR [Database] DEADLOCK detected on table users — transaction rolled back"),
    (9, "ERROR [API] Upstream /api/v1/rca returned 503 Service Unavailable"),
    (10, "INFO  Health check passed — system recovering"),
    (11, "ERROR [Network] DNS resolution failed for db.internal"),
]


def _post(url: str, data: bytes, headers: dict) -> tuple:
    """POST via urllib. URL is always LOKI_URL from env — not user-controlled input."""
    req = urllib.request.Request(url=url, data=data, headers=headers, method="POST")  # noqa: S310
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        return resp.status, resp.read().decode()


def push_logs() -> None:
    now_ns = int(time.time() * 1_000_000_000)
    payload = {
        "streams": [
            {
                "stream": {"app": "autorca-test", "env": "testing", "service": "rca-engine"},
                "values": [[str(now_ns + i * 1_000_000), msg] for i, msg in TEST_LOGS],
            }
        ]
    }
    body = json.dumps(payload).encode()
    credentials = base64.b64encode(f"{LOKI_USER}:{LOKI_TOKEN}".encode()).decode()
    headers = {"Content-Type": "application/json", "Authorization": f"Basic {credentials}"}

    try:
        status, _ = _post(f"{LOKI_URL}/loki/api/v1/push", body, headers)
        print(f"✅ Pushed {len(TEST_LOGS)} log entries — Status: {status}")
    except urllib.error.HTTPError as e:
        resp_body = e.read().decode()
        print(f"❌ HTTP {e.code}: {resp_body}")
        if e.code == 401:
            print("   → Check LOKI_USER and LOKI_TOKEN")
        elif e.code == 404:
            print("   → Check LOKI_URL")
    except urllib.error.URLError as e:
        print(f"❌ Connection failed: {e.reason}")


def print_autorca_config() -> None:
    print("\n" + "=" * 60)
    print("  AutoRCA Grafana Loki Configuration")
    print("=" * 60)
    print(f"\n  URL:   {LOKI_URL}")
    print(f"  User:  {LOKI_USER}")
    print('  Query: {app="autorca-test"}')
    print("\n  Enter these in AutoRCA → Log Source → Grafana Loki")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    missing = [
        v
        for v, val in [
            ("LOKI_URL", "XXX" in LOKI_URL),
            ("LOKI_TOKEN", not LOKI_TOKEN),
            ("LOKI_USER", LOKI_USER == "123456"),
        ]
        if val
    ]

    if missing:
        print(f"⚠️  Set these env vars first: {', '.join(missing)}")
        print("   export LOKI_URL=https://logs-prod-006.grafana.net")
        print("   export LOKI_USER=<numeric ID>")
        print("   export LOKI_TOKEN=<API token>")
        print("   Sign up: https://grafana.com/auth/sign-up")
    else:
        push_logs()
        print_autorca_config()
