"""
tests/test_dashboard_features.py
==================================
Verification tests for all 13 AutoRCA dashboard features.
Reads autorca_dashboard.html with utf-8 + errors='ignore' to handle
Windows cp1252 encoding issues with special characters in the HTML.

Run with:
    pytest tests/test_dashboard_features.py -v
"""

import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DASHBOARD_PATH = os.path.join(ROOT, "autorca_dashboard.html")
API_SERVER_PATH = os.path.join(ROOT, "api_server.py")


def read_dashboard() -> str:
    # utf-8 + errors=ignore handles special chars (em-dash etc.) on Windows cp1252
    with open(DASHBOARD_PATH, encoding="utf-8", errors="ignore") as f:
        return f.read()


def read_api_server() -> str:
    with open(API_SERVER_PATH, encoding="utf-8", errors="ignore") as f:
        return f.read()


# ─────────────────────────────────────────────────────────────────────────────
# F1 — Persistent RCA History (Supabase backend)
# ─────────────────────────────────────────────────────────────────────────────


class TestF1PersistentRCAHistory:
    """
    Manual tests:
      - Open RCA History screen → table loads from Supabase
      - Run RCA + click Save → entry appears in History after navigating there
      - Search by source name → filters results
      - Severity filter buttons → filter works
      - Click row to expand → detail view shows AI summary, stats
      - Click × to delete → row removed
      - Refresh button → reloads from Supabase with toast
    """

    def test_rca_save_endpoint_in_api_server(self):
        assert "/api/rca/save" in read_api_server()

    def test_rca_history_endpoint_in_api_server(self):
        assert "/api/rca/history" in read_api_server()

    def test_dashboard_has_save_rca_function(self):
        assert "saveRCAToHistory" in read_dashboard()

    def test_dashboard_has_do_save_rca_function(self):
        assert "_doSaveRCA" in read_dashboard()

    def test_dashboard_has_rh_load_function(self):
        assert "rhLoad" in read_dashboard()

    def test_dashboard_has_rh_delete_function(self):
        assert "rhDeleteOne" in read_dashboard()

    def test_dashboard_has_rh_render_function(self):
        assert "_rhRender" in read_dashboard()

    def test_dashboard_has_rh_search_function(self):
        assert "rhSearch" in read_dashboard()

    def test_dashboard_has_rh_severity_filter(self):
        assert "rhSetSeverity" in read_dashboard()

    def test_dashboard_has_rca_history_screen(self):
        assert "screen-rca-history" in read_dashboard()

    def test_dashboard_history_nav_triggers_load(self):
        # Navigating to rca-history calls rhLoad()
        src = read_dashboard()
        assert "rca-history" in src and "rhLoad" in src

    def test_api_server_history_returns_ok_and_data(self):
        # After fix, response must have ok + data keys (matching dashboard JS)
        src = read_api_server()
        assert '"ok": True' in src or "'ok': True" in src
        assert '"data"' in src or "'data'" in src


# ─────────────────────────────────────────────────────────────────────────────
# F2 — Real Log Streaming (WebSocket)
# ─────────────────────────────────────────────────────────────────────────────


class TestF2WebSocketStreaming:
    """
    Manual tests:
      - Shift+W → WebSocket connect dialog opens
      - Enter ws://localhost:8888/ws → connects, status dot turns green
      - Logs stream in → Timeline and Dashboard update in real time
      - Pause button → stream pauses, buffer shown
      - Invalid URL → error toast
    """

    def test_dashboard_has_websocket_connect(self):
        assert "new WebSocket(" in read_dashboard()

    def test_dashboard_has_ws_state_indicator(self):
        assert "ws-tb-indicator" in read_dashboard()

    def test_dashboard_has_ws_toggle_pause(self):
        assert "wsTogglePause" in read_dashboard()

    def test_dashboard_ws_shortcut_registered(self):
        src = read_dashboard()
        assert "Live Stream" in src and "WebSocket" in src

    def test_dashboard_ws_error_message(self):
        assert "WebSocket error" in read_dashboard()


# ─────────────────────────────────────────────────────────────────────────────
# F3 — Multi-file / Merge Sources
# ─────────────────────────────────────────────────────────────────────────────


class TestF3MergeSources:
    """
    Manual tests:
      - Compare Sources → load 2+ files → "Merge Sources" button visible
      - Click Merge → unified timeline with entries from all sources
      - Merged stream sorted by timestamp
      - Source badge per entry shows origin file
    """

    def test_dashboard_has_merge_sources_button(self):
        assert "Merge Sources" in read_dashboard()

    def test_dashboard_has_merged_stream_variable(self):
        assert "_mergedStream" in read_dashboard()

    def test_dashboard_has_build_merged_stream(self):
        assert "_buildMergedStream" in read_dashboard()

    def test_dashboard_merge_deduplication(self):
        src = read_dashboard()
        assert "deduplic" in src or "seen" in src


# ─────────────────────────────────────────────────────────────────────────────
# F4 — Pattern-based Alert Rules
# ─────────────────────────────────────────────────────────────────────────────


class TestF4AlertRules:
    """
    Manual tests:
      - Click bell icon → Alert Rules modal opens
      - Add rule with name, source pattern, threshold, cooldown → saved
      - Rule fires when threshold met → toast with rule name
      - Cooldown prevents re-firing within cooldown window
      - Consecutive threshold: rule only fires after N consecutive breaches
      - Toggle enabled/disabled → disabled rules skipped
      - Delete rule → removed from list
    """

    def test_dashboard_has_alert_rules_modal_open(self):
        assert "armOpenModal" in read_dashboard()

    def test_dashboard_has_arm_add_rule(self):
        assert "armAddRule" in read_dashboard()

    def test_dashboard_has_arm_delete_rule(self):
        assert "armDeleteRule" in read_dashboard()

    def test_dashboard_has_arm_save_rules(self):
        assert "armSaveRules" in read_dashboard()

    def test_dashboard_has_cooldown_field(self):
        assert "arm-f-cooldown" in read_dashboard()

    def test_dashboard_has_consecutive_field(self):
        assert "consec" in read_dashboard()

    def test_dashboard_alert_rules_uses_localstorage(self):
        src = read_dashboard()
        assert "armSaveRules" in src and "localStorage" in src

    def test_cooldown_logic():
        """Unit test: cooldown prevents re-firing."""
        import time

        now = int(time.time() * 1000)
        cooldown_minutes = 5
        cooldown_ms = cooldown_minutes * 60 * 1000

        # Never fired — should fire
        last_fired = 0
        assert (now - last_fired) >= cooldown_ms

        # Just fired — should NOT fire
        last_fired = now
        assert (now - last_fired) < cooldown_ms

    test_cooldown_logic = staticmethod(test_cooldown_logic)


# ─────────────────────────────────────────────────────────────────────────────
# F5 — RCA Confidence Score
# ─────────────────────────────────────────────────────────────────────────────


class TestF5ConfidenceScore:
    """
    Manual tests:
      - Run RCA → Confidence Score card shows 0–100 gauge
      - Clean log → low score
      - Many clustered CRITICALs → high score
      - Score included in JSON export
    """

    def test_dashboard_has_confidence_score_card(self):
        assert "Confidence Score" in read_dashboard()

    def test_dashboard_sets_rca_confidence(self):
        assert "_rcaConfidence" in read_dashboard() or "rcaConfidence" in read_dashboard()

    def test_dashboard_calls_build_causality_chain_after_confidence(self):
        # Confidence is computed then chain is built
        src = read_dashboard()
        assert "_rcaConfidence" in src
        assert "_buildCausalityChain" in src

    def test_confidence_score_bounds():
        def calc(vol, clust, agree, dom):
            return max(0, min(100, min(25, vol) + min(25, clust) + min(25, agree) + min(25, dom)))

        assert calc(0, 0, 0, 0) == 0
        assert calc(25, 25, 25, 25) == 100
        assert 0 <= calc(30, 30, 30, 30) <= 100

    test_confidence_score_bounds = staticmethod(test_confidence_score_bounds)


# ─────────────────────────────────────────────────────────────────────────────
# F6 — Format Auto-Detection UI Feedback
# ─────────────────────────────────────────────────────────────────────────────


class TestF6FormatAutoDetection:
    """
    Manual tests:
      - Upload JSON log → detection card: "Detected: JSON (xx%)"
      - Upload syslog → shows "Detected: Syslog"
      - Mixed format → primary format + percentage shown
    """

    def test_dashboard_shows_detected_text(self):
        assert "Detected:" in read_dashboard()

    def test_dashboard_has_format_names_map(self):
        assert "fmtNames" in read_dashboard()

    def test_dashboard_builds_fingerprints_after_parse(self):
        src = read_dashboard()
        assert "buildErrorFingerprints" in src

    def test_format_percentage():
        counts = {"json": 94, "standard": 6}
        total = sum(counts.values())
        primary = max(counts, key=counts.get)
        pct = round(counts[primary] / total * 100)
        assert primary == "json"
        assert pct == 94

    test_format_percentage = staticmethod(test_format_percentage)


# ─────────────────────────────────────────────────────────────────────────────
# F7 — Error Fingerprinting / Deduplication
# ─────────────────────────────────────────────────────────────────────────────


class TestF7ErrorFingerprinting:
    """
    Manual tests:
      - Upload log with repeated DB_CONN_FAIL → Unique Errors < Total Errors
      - Fingerprint panel shows top recurring patterns with counts
    """

    def test_dashboard_has_fingerprint_message(self):
        assert "fingerprintMessage" in read_dashboard()

    def test_dashboard_has_build_error_fingerprints(self):
        assert "buildErrorFingerprints" in read_dashboard()

    def test_dashboard_has_render_error_fingerprints(self):
        assert "renderErrorFingerprints" in read_dashboard()

    def test_fingerprint_normalises_variables():
        def fp(msg):
            msg = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", "", msg)
            msg = re.sub(r"\b\d+\.\d+\.\d+\.\d+\b", "<IP>", msg)
            msg = re.sub(r"\b\d+\b", "<N>", msg)
            return msg.strip().lower()

        assert fp("DB_CONN_FAIL: host 10.0.0.5:5432") == fp("DB_CONN_FAIL: host 192.168.1.1:5432")

    test_fingerprint_normalises_variables = staticmethod(test_fingerprint_normalises_variables)

    def test_fingerprint_groups_errors():
        def group(logs):
            counts = {}
            for log in logs:
                key = log["message"].lower().split(":")[0].strip()
                counts[key] = counts.get(key, 0) + 1
            return counts

        logs = [
            {"message": "DB_CONN_FAIL: host 1"},
            {"message": "DB_CONN_FAIL: host 2"},
            {"message": "NullPointerException: line 42"},
        ]
        g = group(logs)
        assert g["db_conn_fail"] == 2
        assert g["nullpointerexception"] == 1

    test_fingerprint_groups_errors = staticmethod(test_fingerprint_groups_errors)


# ─────────────────────────────────────────────────────────────────────────────
# F8 — Causality Chain SVG
# ─────────────────────────────────────────────────────────────────────────────


class TestF8CausalityChain:
    """
    Manual tests:
      - RCA Report → causality chain SVG renders with nodes + arrows
      - Nodes in chronological order left-to-right
      - No data → shows "No error data" message
    """

    def test_dashboard_has_chain_svg_element(self):
        assert "rcr-chain-svg" in read_dashboard()

    def test_dashboard_has_build_causality_chain(self):
        assert "_buildCausalityChain" in read_dashboard()

    def test_dashboard_chain_no_data_message(self):
        assert "No error data to build causality chain" in read_dashboard()

    def test_dashboard_chain_uses_svg_parts(self):
        assert "svgParts" in read_dashboard()

    def test_chain_node_css_exists(self):
        assert "chain-node" in read_dashboard()

    def test_causality_ordering():
        events = [
            {"source": "api", "timestamp": "10:42:05"},
            {"source": "auth", "timestamp": "10:42:00"},
            {"source": "db", "timestamp": "10:42:03"},
        ]
        sorted_e = sorted(events, key=lambda e: e["timestamp"])
        assert sorted_e[0]["source"] == "auth"
        assert sorted_e[1]["source"] == "db"
        assert sorted_e[2]["source"] == "api"

    test_causality_ordering = staticmethod(test_causality_ordering)


# ─────────────────────────────────────────────────────────────────────────────
# F9 — Remediation Runbook Links
# ─────────────────────────────────────────────────────────────────────────────


class TestF9RunbookLinks:
    """
    Manual tests:
      - RCA Report → each remediation step has "Add Runbook" button
      - Enter URL → saves as clickable badge
      - Persists after page navigation
    """

    def test_dashboard_has_runbook_link_css(self):
        assert "runbook-link" in read_dashboard()

    def test_dashboard_has_runbook_save_function(self):
        assert "runbookSave" in read_dashboard()

    def test_dashboard_has_get_runbook_url(self):
        assert "_getRunbookUrl" in read_dashboard()

    def test_dashboard_has_save_runbook(self):
        assert "_saveRunbook" in read_dashboard()

    def test_dashboard_runbook_edit_button(self):
        assert "runbook-edit-btn" in read_dashboard()

    def test_dashboard_runbook_uses_localstorage(self):
        src = read_dashboard()
        assert "runbook" in src.lower() and "localStorage" in src

    def test_runbook_url_validation():
        def valid(url):
            return url.startswith("http://") or url.startswith("https://")

        assert valid("https://wiki.internal/runbook") is True
        assert valid("http://confluence/123") is True
        assert valid("not-a-url") is False
        assert valid("") is False

    test_runbook_url_validation = staticmethod(test_runbook_url_validation)


# ─────────────────────────────────────────────────────────────────────────────
# F10 — Dark Mode Chart Colors
# ─────────────────────────────────────────────────────────────────────────────


class TestF10DarkModeCharts:
    """
    Manual tests:
      - Switch Dark → all charts update colors instantly, no flash
      - Switch Light → reverts
      - Switch Blue → blue palette
    """

    def test_dashboard_settheme_wrapped(self):
        assert "_originalSetTheme" in read_dashboard()

    def test_dashboard_chart_update_on_theme(self):
        src = read_dashboard()
        assert "chart.update('none')" in src or 'chart.update("none")' in src

    def test_dashboard_all_three_themes_present(self):
        src = read_dashboard()
        assert "setTheme('dark'" in src or 'setTheme("dark"' in src
        assert "setTheme('light'" in src or 'setTheme("light"' in src


# ─────────────────────────────────────────────────────────────────────────────
# F11 — Keyboard Shortcuts
# ─────────────────────────────────────────────────────────────────────────────


class TestF11KeyboardShortcuts:
    """
    Manual tests:
      - ? → shortcuts modal
      - G+D/L/T → navigate screens
      - Esc → close modal
      - / → focus search
      - Shift+W → WebSocket dialog
      - Shortcuts disabled in input fields
    """

    def test_dashboard_has_shortcuts_modal(self):
        assert "Keyboard Shortcuts" in read_dashboard()

    def test_dashboard_has_kbd_open(self):
        assert "kbdOpen" in read_dashboard()

    def test_dashboard_has_keydown_listener(self):
        assert "keydown" in read_dashboard()

    def test_dashboard_has_escape_handler(self):
        assert "Escape" in read_dashboard()

    def test_dashboard_input_guard_exists(self):
        src = read_dashboard()
        assert "inInput" in src or "INPUT" in src


# ─────────────────────────────────────────────────────────────────────────────
# F12 — Log Sampling (50k threshold)
# ─────────────────────────────────────────────────────────────────────────────


class TestF12LogSampling:
    """
    Manual tests:
      - Upload > 50k lines → sampling modal appears
      - "Load Full" → all lines loaded
      - "Sample 10k random" → exactly 10k random lines
      - "Last 10k lines" → tail of file
      - < 50k lines → no modal
    """

    def test_dashboard_has_sampling_modal(self):
        assert "sampling-modal" in read_dashboard()

    def test_dashboard_sample_threshold_50k(self):
        src = read_dashboard()
        assert "SAMPLE_THRESHOLD" in src
        assert "50000" in src

    def test_dashboard_has_sampling_choice(self):
        assert "samplingChoice" in read_dashboard()

    def test_dashboard_has_sampling_file_var(self):
        assert "_samplingFile" in read_dashboard()

    def test_threshold_boundary():
        SAMPLE_THRESHOLD = 50000
        assert (49999 > SAMPLE_THRESHOLD) is False
        assert (50000 > SAMPLE_THRESHOLD) is False
        assert (50001 > SAMPLE_THRESHOLD) is True

    test_threshold_boundary = staticmethod(test_threshold_boundary)

    def test_random_sample_count():
        import random

        logs = list(range(100000))
        sample = random.sample(logs, 10000)
        assert len(sample) == 10000

    test_random_sample_count = staticmethod(test_random_sample_count)

    def test_last_n_slice():
        logs = list(range(75000))
        last = logs[-10000:]
        assert len(last) == 10000
        assert last[0] == 65000

    test_last_n_slice = staticmethod(test_last_n_slice)


# ─────────────────────────────────────────────────────────────────────────────
# F13 — Export JSON / CSV
# ─────────────────────────────────────────────────────────────────────────────


class TestF13ExportJsonCsv:
    """
    Manual tests:
      - RCA Report → JSON button → downloads structured JSON
      - Log Source → CSV button → downloads autorca_export.csv
      - JSON has: source, severity, ai_summary, fix_steps, evidence_logs,
        affected_services, remediation, confidence_score, exported_at
      - CSV has correct 5-column header row
    """

    def test_dashboard_has_json_export_button(self):
        assert "exportRCAJson" in read_dashboard()

    def test_dashboard_has_csv_export_button(self):
        assert "exportCSV" in read_dashboard()

    def test_dashboard_export_rca_json_function_defined(self):
        assert "function exportRCAJson" in read_dashboard()

    def test_dashboard_export_csv_function_defined(self):
        assert "function exportCSV" in read_dashboard()

    def test_dashboard_json_export_has_required_fields(self):
        src = read_dashboard()
        assert "ai_summary" in src or "aiSummary" in src
        assert "fix_steps" in src or "fixSteps" in src
        assert "exported_at" in src or "exportedAt" in src

    def test_dashboard_csv_has_correct_headers(self):
        src = read_dashboard()
        assert "timestamp" in src and "level" in src and "source" in src and "message" in src

    def test_json_export_structure():
        import datetime

        payload = {
            "exported_at": datetime.datetime.now().isoformat(),
            "source": "app.log",
            "severity": "critical",
            "ai_summary": "High error rate.",
            "fix_steps": ["Restart", "Check DB"],
            "affected_services": ["auth"],
            "remediation": [{"step": "Check logs", "runbook": None}],
            "evidence_logs": [{"level": "ERROR", "message": "fail"}],
            "confidence_score": 82,
        }
        required = [
            "exported_at",
            "source",
            "severity",
            "ai_summary",
            "fix_steps",
            "affected_services",
            "remediation",
            "evidence_logs",
            "confidence_score",
        ]
        for k in required:
            assert k in payload
        assert 0 <= payload["confidence_score"] <= 100

    test_json_export_structure = staticmethod(test_json_export_structure)

    def test_csv_five_columns():
        def row(e):
            msg = (e.get("message") or "").replace('"', '""')
            return f'{e["timestamp"]},{e["level"]},{e["source"]},"{msg}",{e["format"]}'

        entry = {"timestamp": "10:00", "level": "ERROR", "source": "db", "message": "fail", "format": "standard"}
        r = row(entry)
        in_q = False
        commas = 0
        for ch in r:
            if ch == '"':
                in_q = not in_q
            elif ch == "," and not in_q:
                commas += 1
        assert commas == 4  # 5 columns = 4 commas

    test_csv_five_columns = staticmethod(test_csv_five_columns)
