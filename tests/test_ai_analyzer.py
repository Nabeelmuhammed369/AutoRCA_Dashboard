"""
tests/test_ai_analyzer.py — Tests for Core/ai_analyzer.py
───────────────────────────────────────────────────────────
Uses unittest.mock to intercept Groq API calls — no real API
key needed, no network calls made during testing.

Tests cover:
  ✅ explain_incident returns correct structure
  ✅ suggest_fixes returns correct structure + parses JSON
  ✅ generate_ticket_summary returns correct structure
  ✅ Missing GROQ_API_KEY returns error gracefully
  ✅ Groq API failure returns error gracefully
  ✅ Malformed JSON from AI is handled gracefully
  ✅ Empty exceptions list is handled
"""

import json
from unittest.mock import MagicMock, patch

# ── Shared test data ──────────────────────────────────────────────────────────

SAMPLE_CLASSIFICATION = "Data Integrity Issue"
SAMPLE_EXCEPTIONS = [
    "2026-03-05 ERROR [Database] DB_CONN_FAIL: Connection refused",
    "2026-03-05 ERROR [Database] DEADLOCK detected on table users",
]
SAMPLE_API_RESULT = {"status_code": 200, "response_time": 0.142}
SAMPLE_DB_RESULT = {"null_email_count": 2}

MOCK_EXPLANATION = (
    "The system experienced a data integrity issue caused by null email "
    "entries in the users table. The DB_CONN_FAIL errors suggest connection "
    "pool exhaustion. Users may have seen inconsistent data responses."
)

MOCK_STEPS_JSON = json.dumps(
    [
        {
            "step": "Check DB connection pool",
            "command": "mysql -u root -e 'SHOW STATUS LIKE Threads_connected'",
        },
        {"step": "Find null emails", "command": "SELECT * FROM users WHERE email IS NULL"},
        {"step": "Review recent ETL jobs", "command": "tail -100 /var/log/etl.log"},
        {"step": "Add NOT NULL constraint", "command": None},
    ]
)

MOCK_TICKET = """===GITHUB===
## Summary
Data integrity issue detected on 2026-03-05.

## Impact
Users with null emails cannot log in.

## Root Cause
DB connection pool exhaustion caused incomplete writes.

## Evidence
```
ERROR DB_CONN_FAIL: Connection refused
```

## Suggested Fix Steps
- Check connection pool
- Find null email rows
===SLACK===
🟡 *Incident: Data Integrity Issue* — 2026-03-05 10:00:00
API: 200 | DB anomalies: 2
DB connection pool may be exhausted. Check users table immediately.
cc: @on-call-engineer"""


def make_mock_groq(response_text: str):
    """Build a mock Groq client that returns the given text."""
    mock_choice = MagicMock()
    mock_choice.message.content = response_text
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response
    return mock_client


# ── explain_incident ──────────────────────────────────────────────────────────


class TestExplainIncident:
    @patch("Core.ai_analyzer.GROQ_API_KEY", "fake-test-key")
    @patch("groq.Groq")
    def test_returns_dict(self, mock_groq_class):
        mock_groq_class.return_value = make_mock_groq(MOCK_EXPLANATION)
        from Core.ai_analyzer import explain_incident

        result = explain_incident(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert isinstance(result, dict)

    @patch("Core.ai_analyzer.GROQ_API_KEY", "fake-test-key")
    @patch("groq.Groq")
    def test_success_true_on_valid_response(self, mock_groq_class):
        mock_groq_class.return_value = make_mock_groq(MOCK_EXPLANATION)
        from Core.ai_analyzer import explain_incident

        result = explain_incident(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert result["success"] is True

    @patch("Core.ai_analyzer.GROQ_API_KEY", "fake-test-key")
    @patch("groq.Groq")
    def test_explanation_is_string(self, mock_groq_class):
        mock_groq_class.return_value = make_mock_groq(MOCK_EXPLANATION)
        from Core.ai_analyzer import explain_incident

        result = explain_incident(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert isinstance(result["explanation"], str)
        assert len(result["explanation"]) > 0

    @patch("Core.ai_analyzer.GROQ_API_KEY", "fake-test-key")
    @patch("groq.Groq")
    def test_error_is_none_on_success(self, mock_groq_class):
        mock_groq_class.return_value = make_mock_groq(MOCK_EXPLANATION)
        from Core.ai_analyzer import explain_incident

        result = explain_incident(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert result["error"] is None

    @patch("Core.ai_analyzer.GROQ_API_KEY", "fake-test-key")
    @patch("groq.Groq")
    def test_empty_exceptions_handled(self, mock_groq_class):
        """Empty exceptions list should not crash — just use fallback text."""
        mock_groq_class.return_value = make_mock_groq(MOCK_EXPLANATION)
        from Core.ai_analyzer import explain_incident

        result = explain_incident(SAMPLE_CLASSIFICATION, [], SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert result["success"] is True

    @patch("Core.ai_analyzer.GROQ_API_KEY", None)
    def test_missing_api_key_returns_error(self):
        """No GROQ_API_KEY should return error dict, not raise."""
        from Core.ai_analyzer import explain_incident

        result = explain_incident(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert result["success"] is False
        assert result["error"] is not None

    @patch("Core.ai_analyzer.GROQ_API_KEY", "fake-test-key")
    @patch("groq.Groq")
    def test_groq_exception_returns_error(self, mock_groq_class):
        """If Groq raises an exception, return error dict — don't crash."""
        mock_groq_class.return_value.chat.completions.create.side_effect = Exception("Rate limit exceeded")
        from Core.ai_analyzer import explain_incident

        result = explain_incident(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert result["success"] is False
        assert "Rate limit" in result["error"]


# ── suggest_fixes ─────────────────────────────────────────────────────────────


class TestSuggestFixes:
    @patch("Core.ai_analyzer.GROQ_API_KEY", "fake-test-key")
    @patch("groq.Groq")
    def test_returns_dict(self, mock_groq_class):
        mock_groq_class.return_value = make_mock_groq(MOCK_STEPS_JSON)
        from Core.ai_analyzer import suggest_fixes

        result = suggest_fixes(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert isinstance(result, dict)

    @patch("Core.ai_analyzer.GROQ_API_KEY", "fake-test-key")
    @patch("groq.Groq")
    def test_success_true_on_valid_json(self, mock_groq_class):
        mock_groq_class.return_value = make_mock_groq(MOCK_STEPS_JSON)
        from Core.ai_analyzer import suggest_fixes

        result = suggest_fixes(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert result["success"] is True

    @patch("Core.ai_analyzer.GROQ_API_KEY", "fake-test-key")
    @patch("groq.Groq")
    def test_steps_is_list(self, mock_groq_class):
        mock_groq_class.return_value = make_mock_groq(MOCK_STEPS_JSON)
        from Core.ai_analyzer import suggest_fixes

        result = suggest_fixes(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert isinstance(result["steps"], list)

    @patch("Core.ai_analyzer.GROQ_API_KEY", "fake-test-key")
    @patch("groq.Groq")
    def test_steps_have_correct_keys(self, mock_groq_class):
        mock_groq_class.return_value = make_mock_groq(MOCK_STEPS_JSON)
        from Core.ai_analyzer import suggest_fixes

        result = suggest_fixes(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        for step in result["steps"]:
            assert "step" in step
            assert "command" in step

    @patch("Core.ai_analyzer.GROQ_API_KEY", "fake-test-key")
    @patch("groq.Groq")
    def test_returns_4_steps(self, mock_groq_class):
        mock_groq_class.return_value = make_mock_groq(MOCK_STEPS_JSON)
        from Core.ai_analyzer import suggest_fixes

        result = suggest_fixes(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert len(result["steps"]) == 4

    @patch("Core.ai_analyzer.GROQ_API_KEY", "fake-test-key")
    @patch("groq.Groq")
    def test_handles_json_with_markdown_fences(self, mock_groq_class):
        """AI sometimes wraps JSON in ```json fences — must strip them."""
        fenced = "```json\n" + MOCK_STEPS_JSON + "\n```"
        mock_groq_class.return_value = make_mock_groq(fenced)
        from Core.ai_analyzer import suggest_fixes

        result = suggest_fixes(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert result["success"] is True
        assert isinstance(result["steps"], list)

    @patch("Core.ai_analyzer.GROQ_API_KEY", "fake-test-key")
    @patch("groq.Groq")
    def test_malformed_json_returns_error(self, mock_groq_class):
        """If AI returns invalid JSON, return error dict — don't crash."""
        mock_groq_class.return_value = make_mock_groq("This is not JSON at all")
        from Core.ai_analyzer import suggest_fixes

        result = suggest_fixes(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert result["success"] is False
        assert result["steps"] == []

    @patch("Core.ai_analyzer.GROQ_API_KEY", None)
    def test_missing_api_key_returns_error(self):
        from Core.ai_analyzer import suggest_fixes

        result = suggest_fixes(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert result["success"] is False


# ── generate_ticket_summary ───────────────────────────────────────────────────


class TestGenerateTicketSummary:
    @patch("Core.ai_analyzer.GROQ_API_KEY", "fake-test-key")
    @patch("groq.Groq")
    def test_returns_dict(self, mock_groq_class):
        mock_groq_class.return_value = make_mock_groq(MOCK_TICKET)
        from Core.ai_analyzer import generate_ticket_summary

        result = generate_ticket_summary(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert isinstance(result, dict)

    @patch("Core.ai_analyzer.GROQ_API_KEY", "fake-test-key")
    @patch("groq.Groq")
    def test_success_true(self, mock_groq_class):
        mock_groq_class.return_value = make_mock_groq(MOCK_TICKET)
        from Core.ai_analyzer import generate_ticket_summary

        result = generate_ticket_summary(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert result["success"] is True

    @patch("Core.ai_analyzer.GROQ_API_KEY", "fake-test-key")
    @patch("groq.Groq")
    def test_github_key_present(self, mock_groq_class):
        mock_groq_class.return_value = make_mock_groq(MOCK_TICKET)
        from Core.ai_analyzer import generate_ticket_summary

        result = generate_ticket_summary(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert "github" in result
        assert isinstance(result["github"], str)
        assert len(result["github"]) > 0

    @patch("Core.ai_analyzer.GROQ_API_KEY", "fake-test-key")
    @patch("groq.Groq")
    def test_slack_key_present(self, mock_groq_class):
        mock_groq_class.return_value = make_mock_groq(MOCK_TICKET)
        from Core.ai_analyzer import generate_ticket_summary

        result = generate_ticket_summary(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert "slack" in result
        assert isinstance(result["slack"], str)
        assert len(result["slack"]) > 0

    @patch("Core.ai_analyzer.GROQ_API_KEY", "fake-test-key")
    @patch("groq.Groq")
    def test_github_contains_summary_section(self, mock_groq_class):
        mock_groq_class.return_value = make_mock_groq(MOCK_TICKET)
        from Core.ai_analyzer import generate_ticket_summary

        result = generate_ticket_summary(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert "## Summary" in result["github"]

    @patch("Core.ai_analyzer.GROQ_API_KEY", "fake-test-key")
    @patch("groq.Groq")
    def test_fallback_when_delimiters_missing(self, mock_groq_class):
        """If AI doesn't use ===GITHUB=== format, use full text as github + build slack fallback."""
        mock_groq_class.return_value = make_mock_groq("Some unformatted response text")
        from Core.ai_analyzer import generate_ticket_summary

        result = generate_ticket_summary(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert result["success"] is True
        assert len(result["github"]) > 0
        assert len(result["slack"]) > 0

    @patch("Core.ai_analyzer.GROQ_API_KEY", None)
    def test_missing_api_key_returns_error(self):
        from Core.ai_analyzer import generate_ticket_summary

        result = generate_ticket_summary(SAMPLE_CLASSIFICATION, SAMPLE_EXCEPTIONS, SAMPLE_API_RESULT, SAMPLE_DB_RESULT)
        assert result["success"] is False
        assert result["github"] == ""
        assert result["slack"] == ""
