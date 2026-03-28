import logging

import requests

logger = logging.getLogger("API_MONITOR")


def check_api_health(url, timeout):
    try:
        logger.info(f"Checking API health: {url}")

        response = requests.get(url, timeout=timeout)

        logger.info(f"API responded with status {response.status_code}")

        elapsed = response.elapsed.total_seconds()
        return {
            "status_code": response.status_code,
            "response_time": elapsed,   # primary key (tests expect this)
            "latency_ms": elapsed,      # alias kept for backward compat
            "error": None,
        }

    except requests.exceptions.Timeout:
        logger.error("API request timed out.")
        return {"error": "Timeout"}

    except requests.exceptions.ConnectionError:
        logger.error("API connection failed.")
        return {"error": "Connection Error"}

    except Exception as e:
        logger.exception("Unexpected API error occurred.")
        return {"error": str(e)}