"""
mock_log_server.py — FastAPI version
─────────────────────────────────────
Simulates 4 log source endpoints for AutoRCA integration testing.
Runs on uvicorn — compatible with Render, Railway, and any cloud host.

Deploy start command:  uvicorn mock_log_server:app --host 0.0.0.0 --port $PORT
Local run:             uvicorn mock_log_server:app --port 8888 --reload
"""

import os
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

app = FastAPI(title="AutoRCA Mock Log Server", version="1.0.0")

# CORS — allow requests from any origin (dashboard, Netlify, localhost)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ── Sample log data ───────────────────────────────────────────────────────────

PLAIN_LOGS = """\
2026-03-08 10:00:00 ERROR [Database] DB_CONN_FAIL: Connection refused at localhost:5432
2026-03-08 10:00:01 CRITICAL [Database] Complete database outage detected
2026-03-08 10:00:02 ERROR [Network] Connection timed out after 30s
2026-03-08 10:00:03 ERROR [API] Gateway returned 502 Bad Gateway
2026-03-08 10:00:04 ERROR [Firewall] Access denied - rule violation on port 443
2026-03-08 10:00:05 ERROR [ActiveDirectory] LDAP bind failed for user admin
2026-03-08 10:00:06 ERROR NullPointerException in UserService.java:88
2026-03-08 10:00:07 WARNING High memory usage detected - 91% utilised
2026-03-08 10:00:08 ERROR [Database] DEADLOCK detected on table users
2026-03-08 10:00:09 ERROR [API] Upstream service /api/v1/rca returned 503
2026-03-08 10:00:10 INFO  Health check passed
2026-03-08 10:00:11 ERROR [Network] DNS resolution failed for db.internal
"""

ELASTICSEARCH_RESPONSE = {
    "hits": {
        "total": {"value": 6},
        "hits": [
            {
                "_source": {
                    "@timestamp": "2026-03-08T10:00:00Z",
                    "level": "ERROR",
                    "message": "DB_CONN_FAIL: Connection refused",
                    "service": "database",
                }
            },
            {
                "_source": {
                    "@timestamp": "2026-03-08T10:00:01Z",
                    "level": "CRITICAL",
                    "message": "Complete database outage",
                    "service": "database",
                }
            },
            {
                "_source": {
                    "@timestamp": "2026-03-08T10:00:02Z",
                    "level": "ERROR",
                    "message": "Connection timed out after 30s",
                    "service": "network",
                }
            },
            {
                "_source": {
                    "@timestamp": "2026-03-08T10:00:03Z",
                    "level": "ERROR",
                    "message": "Gateway returned 502 Bad Gateway",
                    "service": "api",
                }
            },
            {
                "_source": {
                    "@timestamp": "2026-03-08T10:00:04Z",
                    "level": "ERROR",
                    "message": "NullPointerException in UserService.java:88",
                    "service": "app",
                }
            },
            {
                "_source": {
                    "@timestamp": "2026-03-08T10:00:05Z",
                    "level": "ERROR",
                    "message": "LDAP bind failed for user admin",
                    "service": "active-directory",
                }
            },
        ],
    }
}


def _loki_response():
    now = int(time.time() * 1e9)
    return {
        "data": {
            "result": [
                {
                    "stream": {"app": "autorca", "level": "error"},
                    "values": [
                        [str(now), "ERROR [Database] DB_CONN_FAIL: Connection refused"],
                        [str(now + 1), "CRITICAL [Database] Complete outage detected"],
                        [str(now + 2), "ERROR [Network] Connection timed out after 30s"],
                        [str(now + 3), "ERROR [API] Gateway returned 502 Bad Gateway"],
                        [str(now + 4), "ERROR NullPointerException in UserService.java:88"],
                    ],
                }
            ]
        },
        "status": "success",
    }


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok", "service": "autorca-mock-logs"}


@app.get("/logs", response_class=PlainTextResponse)
async def logs():
    return PLAIN_LOGS


@app.get("/loki/api/v1/query_range")
async def loki_query(query: str = '{app="autorca"}', limit: int = 5000, start: str = None, end: str = None):
    return JSONResponse(_loki_response())


@app.post("/loki/api/v1/push")
async def loki_push(request: Request):
    return JSONResponse(content=None, status_code=204)


@app.get("/app-logs/_search")
@app.post("/app-logs/_search")
async def elasticsearch_search(request: Request):
    return JSONResponse(ELASTICSEARCH_RESPONSE)


@app.get("/autorca-logs/app.log", response_class=PlainTextResponse)
async def s3_object():
    return PLAIN_LOGS


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8888"))
    uvicorn.run("mock_log_server:app", host="0.0.0.0", port=port, reload=True)
