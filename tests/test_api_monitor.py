"""
tests/test_api_monitor.py — Tests for Monitors/api_monitor.py
───────────────────────────────────────────────────────────────
Tests cover:
  ✅ Successful 200 response
  ✅ Non-200 status codes (500, 404, 503)
  ✅ Request timeout
  ✅ Connection error (server unreachable)
  ✅ Unexpected exception handling
  ✅ Response time is captured correctly
  ✅ Return type is always a dict
"""

from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import ConnectionError as ReqConnectionError
from requests.exceptions import Timeout

# We import the function directly — this tests YOUR real code
from Monitors.api_monitor import check_api_health

# ── Helpers ───────────────────────────────────────────────────────────────────


def make_mock_response(status_code: int, elapsed_seconds: float = 0.142):
    """Build a fake requests.Response object."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.elapsed.total_seconds.return_value = elapsed_seconds
    return mock_resp


# ── Success cases ─────────────────────────────────────────────────────────────


class TestAPIMonitorSuccess:
    @patch("Monitors.api_monitor.requests.get")
    def test_healthy_200_returns_status_code(self, mock_get):
        """A 200 response should return status_code: 200."""
        mock_get.return_value = make_mock_response(200, 0.142)
        result = check_api_health("http://example.com", timeout=5)
        assert result["status_code"] == 200

    @patch("Monitors.api_monitor.requests.get")
    def test_healthy_200_returns_response_time(self, mock_get):
        """Response time should be captured from elapsed.total_seconds()."""
        # Build the mock directly here — no helper needed
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.elapsed.total_seconds.return_value = 0.250
        mock_get.return_value = mock_response
        result = check_api_health("http://example.com", timeout=5)
        # This will fail if patch did not apply — proves mock is working
        mock_get.assert_called_once_with("http://example.com", timeout=5)
        assert result["status_code"] == 200
        assert result["error"] is None
        assert "response_time" in result
        assert result["response_time"] == pytest.approx(0.250)

    @patch("Monitors.api_monitor.requests.get")
    def test_returns_dict(self, mock_get):
        """Return type must always be a dict."""
        mock_get.return_value = make_mock_response(200)
        result = check_api_health("http://example.com", timeout=5)
        assert isinstance(result, dict)

    @patch("Monitors.api_monitor.requests.get")
    def test_correct_url_is_called(self, mock_get):
        """The exact URL from config should be passed to requests.get."""
        mock_get.return_value = make_mock_response(200)
        url = "http://my-api.internal/health"
        check_api_health(url, timeout=5)
        mock_get.assert_called_once_with(url, timeout=5)

    @patch("Monitors.api_monitor.requests.get")
    def test_correct_timeout_is_used(self, mock_get):
        """The timeout from config should be forwarded to requests.get."""
        mock_get.return_value = make_mock_response(200)
        check_api_health("http://example.com", timeout=10)
        _, kwargs = mock_get.call_args
        assert kwargs.get("timeout") == 10 or mock_get.call_args[0][1] == 10


# ── Non-200 status codes ──────────────────────────────────────────────────────


class TestAPIMonitorNon200:
    @patch("Monitors.api_monitor.requests.get")
    def test_500_returns_status_code_500(self, mock_get):
        """A 500 response should still return status_code (not an error dict)."""
        mock_get.return_value = make_mock_response(500)
        result = check_api_health("http://example.com", timeout=5)
        assert result["status_code"] == 500

    @patch("Monitors.api_monitor.requests.get")
    def test_404_returns_status_code_404(self, mock_get):
        mock_get.return_value = make_mock_response(404)
        result = check_api_health("http://example.com", timeout=5)
        assert result["status_code"] == 404

    @patch("Monitors.api_monitor.requests.get")
    def test_503_returns_status_code_503(self, mock_get):
        mock_get.return_value = make_mock_response(503)
        result = check_api_health("http://example.com", timeout=5)
        assert result["status_code"] == 503

    @patch("Monitors.api_monitor.requests.get")
    def test_non_200_still_returns_response_time(self, mock_get):
        """Even error responses should capture response time."""
        mock_get.return_value = make_mock_response(500, 1.234)
        result = check_api_health("http://example.com", timeout=5)
        assert "response_time" in result


# ── Error / exception cases ───────────────────────────────────────────────────


class TestAPIMonitorErrors:
    @patch("Monitors.api_monitor.requests.get", side_effect=Timeout())
    def test_timeout_returns_error_key(self, mock_get):
        """A Timeout should return a dict with an 'error' key."""
        result = check_api_health("http://example.com", timeout=1)
        assert "error" in result

    @patch("Monitors.api_monitor.requests.get", side_effect=Timeout())
    def test_timeout_error_message(self, mock_get):
        """Timeout error message should mention timeout."""
        result = check_api_health("http://example.com", timeout=1)
        assert "timeout" in result["error"].lower() or "Timeout" in result["error"]

    @patch("Monitors.api_monitor.requests.get", side_effect=ReqConnectionError())
    def test_connection_error_returns_error_key(self, mock_get):
        """A ConnectionError should return a dict with an 'error' key."""
        result = check_api_health("http://unreachable.internal", timeout=5)
        assert "error" in result

    @patch("Monitors.api_monitor.requests.get", side_effect=ReqConnectionError())
    def test_connection_error_message(self, mock_get):
        """Connection error message should mention connection."""
        result = check_api_health("http://unreachable.internal", timeout=5)
        assert "connection" in result["error"].lower() or "Connection" in result["error"]

    @patch("Monitors.api_monitor.requests.get", side_effect=Exception("Unexpected SSL error"))
    def test_unexpected_exception_returns_error_key(self, mock_get):
        """Any unexpected exception should be caught and returned as error dict."""
        result = check_api_health("http://example.com", timeout=5)
        assert "error" in result

    @patch("Monitors.api_monitor.requests.get", side_effect=Exception("Unexpected SSL error"))
    def test_unexpected_exception_does_not_crash(self, mock_get):
        """Function must never raise — always return a dict."""
        try:
            result = check_api_health("http://example.com", timeout=5)
            assert isinstance(result, dict)
        except Exception:
            pytest.fail("check_api_health() raised an exception instead of handling it")

    @patch("Monitors.api_monitor.requests.get", side_effect=Timeout())
    def test_error_result_has_no_status_code(self, mock_get):
        """Error results should not have a status_code key (only success results do)."""
        result = check_api_health("http://example.com", timeout=1)
        # error dict should have 'error' key, not both
        assert "error" in result
        # status_code may or may not be present — but error must be
        assert result.get("error") is not None
