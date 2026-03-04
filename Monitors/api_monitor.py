import requests

def check_api_health(url, timeout):
    try:
        response = requests.get(url, timeout=timeout)
        status = "Success" if response.status_code == 200 else "Failed"
        return {
            "status_code": response.status_code,
            "response_time": response.elapsed.total_seconds(),
            "history": [{"endpoint": url, "status": status, "code": response.status_code}]
        }
    except Exception as e:
        return {
            "error": str(e),
            "history": [{"endpoint": url, "status": "Failed", "code": "TIMEOUT/ERR"}]
        }