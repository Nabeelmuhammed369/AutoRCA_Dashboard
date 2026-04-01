#!/usr/bin/env python3
"""
autorca_setup.py — Quick setup helper for AutoRCA
Run this once before starting api_server.py to ensure all required files exist.
"""

import os
import sqlite3

# Create empty log file if missing
if not os.path.exists("app.log"):
    with open("app.log", "w") as f:
        f.write("2026-03-09 00:00:00 INFO [System] AutoRCA started\n")
    print("✓ Created app.log")
else:
    print("✓ app.log exists")

# Create empty SQLite DB if missing
if not os.path.exists("autorca.db"):
    conn = sqlite3.connect("autorca.db")
    conn.execute("""CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, level TEXT, source TEXT, message TEXT
    )""")
    conn.commit()
    conn.close()
    print("✓ Created autorca.db")
else:
    print("✓ autorca.db exists")

# Check .env
if not os.path.exists(".env"):
    with open(".env", "w") as f:
        f.write("AUTORCA_API_KEY=dev-key-change-me\n")
        f.write("ALLOWED_ORIGIN=\n")
    print("✓ Created .env with default API key: dev-key-change-me")
    print("  ⚠ Set AUTORCA_API_KEY and ALLOWED_ORIGIN before production use!")
else:
    print("✓ .env exists")

# Check config.yaml
if not os.path.exists("config.yaml"):
    print("✗ config.yaml missing — copy the one provided!")
else:
    print("✓ config.yaml exists")

print("\n✅ Setup complete. Now run:")
print("   python api_server.py")
print("   python mock_log_server.py  (in a separate terminal, for testing)")
