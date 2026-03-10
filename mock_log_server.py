"""
mock_log_server.py
──────────────────
Simulates 4 different log source endpoints so you can test AutoRCA's
integrations without Docker, cloud accounts, or any external dependencies.

Run:  python mock_log_server.py
Then point AutoRCA's integration panel to the URLs printed on startup.
"""

import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# ── Sample log data ───────────────────────────────────────────────────────────

PLAIN_LOGS = """\
2026-03-08 10:00:00 ERROR [Database] DB_CONN_FAIL: Connection refused at localhost:5432
2026-03-08 10:00:01 CRITICAL [Database] Complete database outage detected
2026-03-08 10:00:02 ERROR [Network] Connection timed out after 30s
2026-03-08 10:00:03 ERROR [API] Gateway returned 502 Bad Gateway
2026-03-08 10:00:04 ERROR [Firewall] Access denied — rule violation on port 443
2026-03-08 10:00:05 ERROR [ActiveDirectory] LDAP bind failed for user admin
2026-03-08 10:00:06 ERROR NullPointerException in UserService.java:88
2026-03-08 10:00:07 WARNING High memory usage detected — 91% utilised
2026-03-08 10:00:08 ERROR [Database] DEADLOCK detected on table users
2026-03-08 10:00:09 ERROR [API] Upstream service /api/v1/rca returned 503
2026-03-08 10:00:10 INFO  Health check passed
2026-03-08 10:00:11 ERROR [Network] DNS resolution failed for db.internal
"""

JSON_LOGS = json.dumps({
    "data": {
        "result": [
            {
                "stream": {"app": "autorca", "level": "error"},
                "values": [
                    [str(int(time.time() * 1e9)),
                     "ERROR [Database] DB_CONN_FAIL: Connection refused"],
                    [str(int(time.time() * 1e9) + 1),
                     "CRITICAL [Database] Complete outage detected"],
                    [str(int(time.time() * 1e9) + 2),
                     "ERROR [Network] Connection timed out after 30s"],
                    [str(int(time.time() * 1e9) + 3),
                     "ERROR [API] Gateway returned 502 Bad Gateway"],
                    [str(int(time.time() * 1e9) + 4),
                     "ERROR NullPointerException in UserService.java:88"],
                ]
            }
        ]
    },
    "status": "success"
})

ELASTICSEARCH_RESPONSE = json.dumps({
    "hits": {
        "total": {"value": 6},
        "hits": [
            {"_source": {
                "@timestamp": "2026-03-08T10:00:00Z",
                "level": "ERROR",
                "message": "DB_CONN_FAIL: Connection refused at localhost:5432",
                "service": "database"
            }},
            {"_source": {
                "@timestamp": "2026-03-08T10:00:01Z",
                "level": "CRITICAL",
                "message": "Complete database outage",
                "service": "database"
            }},
            {"_source": {
                "@timestamp": "2026-03-08T10:00:02Z",
                "level": "ERROR",
                "message": "Connection timed out after 30s",
                "service": "network"
            }},
            {"_source": {
                "@timestamp": "2026-03-08T10:00:03Z",
                "level": "ERROR",
                "message": "Gateway returned 502 Bad Gateway",
                "service": "api"
            }},
            {"_source": {
                "@timestamp": "2026-03-08T10:00:04Z",
                "level": "ERROR",
                "message": "NullPointerException in UserService.java:88",
                "service": "app"
            }},
            {"_source": {
                "@timestamp": "2026-03-08T10:00:05Z",
                "level": "ERROR",
                "message": "LDAP bind failed for user admin",
                "service": "active-directory"
            }},
        ]
    }
})

S3_LOG_CONTENT = PLAIN_LOGS  # S3 returns raw file content

# ── Request handler ───────────────────────────────────────────────────────────

class MockLogHandler(BaseHTTPRequestHandler):

    ROUTES = {
        # Custom HTTP endpoint — plain text logs
        "/logs":                   ("text/plain",       PLAIN_LOGS),

        # Loki-style query range endpoint
        "/loki/api/v1/query_range": ("application/json", JSON_LOGS),

        # Elasticsearch search endpoint
        "/app-logs/_search":        ("application/json", ELASTICSEARCH_RESPONSE),

        # S3-style object GET
        "/autorca-logs/app.log":    ("text/plain",       S3_LOG_CONTENT),

        # Health check
        "/health":                  ("application/json", '{"status": "ok"}'),
    }

    def do_GET(self):
        path = urlparse(self.path).path
        if path in self.ROUTES:
            content_type, body = self.ROUTES[path]
            self._respond(200, content_type, body)
        else:
            self._respond(404, "text/plain", f"Unknown endpoint: {path}\n"
                          f"Available: {list(self.ROUTES.keys())}")

    def do_OPTIONS(self):
        """Handle CORS preflight requests from the browser."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Authorization")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_POST(self):
        # Accept POST for Loki push and Elasticsearch POST search
        path = urlparse(self.path).path
        content_length = int(self.headers.get("Content-Length", 0))
        _ = self.rfile.read(content_length)  # read but ignore body

        if path in ("/loki/api/v1/push",):
            self._respond(204, "text/plain", "")  # Loki push returns 204
        elif path in self.ROUTES:
            content_type, body = self.ROUTES[path]
            self._respond(200, content_type, body)
        else:
            self._respond(404, "text/plain", f"Unknown: {path}")

    def _respond(self, status, content_type, body):
        body_bytes = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body_bytes)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Authorization")
        self.end_headers()
        if body_bytes:  # Don't write body for 204
            self.wfile.write(body_bytes)

    def log_message(self, fmt, *args):
        # Custom log format — cleaner output
        print(f"  [{self.address_string()}] {fmt % args}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    PORT = 8888
    server = HTTPServer(("localhost", PORT), MockLogHandler)

    print("\n" + "=" * 60)
    print("  AutoRCA Mock Log Server")
    print("=" * 60)
    print(f"\n  Server running at: http://localhost:{PORT}")
    print("\n  Configure AutoRCA integrations using these URLs:")
    print()
    print("  ┌─ Custom HTTP Endpoint ─────────────────────────────────┐")
    print(f"  │  URL:  http://localhost:{PORT}/logs                      │")
    print("  │  Returns plain text logs                               │")
    print("  └────────────────────────────────────────────────────────┘")
    print()
    print("  ┌─ Grafana Loki ─────────────────────────────────────────┐")
    print(f"  │  URL:    http://localhost:{PORT}                          │")
    print("  │  Query:  {app=\"autorca\"}                               │")
    print("  └────────────────────────────────────────────────────────┘")
    print()
    print("  ┌─ Elasticsearch ────────────────────────────────────────┐")
    print(f"  │  URL:    http://localhost:{PORT}                          │")
    print("  │  Index:  app-logs                                      │")
    print("  │  Query:  level:ERROR OR level:CRITICAL                 │")
    print("  └────────────────────────────────────────────────────────┘")
    print()
    print("  ┌─ Amazon S3 / MinIO ────────────────────────────────────┐")
    print(f"  │  Endpoint:  http://localhost:{PORT}                       │")
    print("  │  Bucket:    autorca-logs                               │")
    print("  │  File:      app.log                                    │")
    print("  └────────────────────────────────────────────────────────┘")
    print()
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")