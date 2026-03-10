"""
push_to_grafana_cloud.py
────────────────────────
Pushes realistic test logs to your FREE Grafana Cloud Loki instance.
Sign up free at: https://grafana.com/auth/sign-up

After signing up:
  1. Go to your Grafana Cloud stack
  2. Click "Details" next to Loki
  3. Copy: URL, User (numeric ID), and generate an API token

Fill in the 3 variables below and run:
  python push_to_grafana_cloud.py
"""

import json
import time
import urllib.request
import urllib.error
import base64

# ── Fill these in from your Grafana Cloud Loki details ───────────────────────
LOKI_URL   = "https://logs-prod-XXX.grafana.net"   # e.g. logs-prod-006.grafana.net
LOKI_USER  = "123456"                               # numeric user ID
LOKI_TOKEN = "your-grafana-api-token-here"          # generated API token
# ─────────────────────────────────────────────────────────────────────────────

# Test log entries — realistic AutoRCA test data
TEST_LOGS = [
    (0,  "ERROR [Database] DB_CONN_FAIL: Connection refused at localhost:5432"),
    (1,  "CRITICAL [Database] Complete database outage detected — all queries failing"),
    (2,  "ERROR [Network] Connection timed out after 30s — host db.internal unreachable"),
    (3,  "ERROR [API] Gateway returned 502 Bad Gateway on /api/v1/users"),
    (4,  "ERROR [Firewall] Access denied — rule violation on port 443 from 10.0.0.5"),
    (5,  "ERROR [ActiveDirectory] LDAP bind failed for user admin@corp.local"),
    (6,  "ERROR NullPointerException in UserService.java:88"),
    (7,  "WARNING High memory usage detected — 91% of 8GB utilised"),
    (8,  "ERROR [Database] DEADLOCK detected on table users — transaction rolled back"),
    (9,  "ERROR [API] Upstream /api/v1/rca returned 503 Service Unavailable"),
    (10, "INFO  Health check passed — system recovering"),
    (11, "ERROR [Network] DNS resolution failed for db.internal"),
]


def push_logs():
    now_ns = int(time.time() * 1_000_000_000)

    payload = {
        "streams": [
            {
                "stream": {
                    "app": "autorca-test",
                    "env": "testing",
                    "service": "rca-engine"
                },
                "values": [
                    [str(now_ns + i * 1_000_000), msg]
                    for i, msg in TEST_LOGS
                ]
            }
        ]
    }

    body = json.dumps(payload).encode()
    credentials = base64.b64encode(f"{LOKI_USER}:{LOKI_TOKEN}".encode()).decode()

    req = urllib.request.Request(
        url=f"{LOKI_URL}/loki/api/v1/push",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {credentials}",
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req) as resp:
            print(f"✅ Pushed {len(TEST_LOGS)} log entries to Grafana Cloud Loki")
            print(f"   Status: {resp.status}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"❌ Failed: HTTP {e.code}")
        print(f"   Response: {body}")
        if e.code == 401:
            print("   → Check your LOKI_USER and LOKI_TOKEN")
        elif e.code == 404:
            print("   → Check your LOKI_URL")
    except urllib.error.URLError as e:
        print(f"❌ Connection failed: {e.reason}")
        print("   → Check your LOKI_URL and internet connection")


def print_autorca_config():
    print("\n" + "=" * 60)
    print("  AutoRCA Grafana Loki Configuration")
    print("=" * 60)
    print(f"\n  URL:      {LOKI_URL}")
    print(f"  Username: {LOKI_USER}")
    print(f"  Password: {LOKI_TOKEN}")
    print(f"  Query:    {{app=\"autorca-test\"}}")
    print("\n  Enter these values in AutoRCA's Log Source → Grafana Loki tab")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    if "XXX" in LOKI_URL or LOKI_TOKEN == "your-grafana-api-token-here":
        print("⚠️  Please fill in LOKI_URL, LOKI_USER, and LOKI_TOKEN first.")
        print("   Sign up free at: https://grafana.com/auth/sign-up")
    else:
        push_logs()
        print_autorca_config()