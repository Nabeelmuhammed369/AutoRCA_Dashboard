import logging
import time

import requests

logger = logging.getLogger("API_MONITOR")


def check_api_health(url, timeout):
    try:
        logger.info(f"Checking API health: {url}")

        start = time.time()
        response = requests.get(url, timeout=timeout)
        elapsed = time.time() - start  # use time.time() — response.elapsed breaks with mocks

        logger.info(f"API responded with status {response.status_code}")

        return {
            "status_code": response.status_code,
            "response_time": elapsed,  # consistent key for ALL status codes (200, 500, etc.)
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
