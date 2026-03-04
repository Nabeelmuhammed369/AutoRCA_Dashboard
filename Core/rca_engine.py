def classify_issue(api_result, log_result, db_result):

    if "error" in api_result:
        return "Infrastructure Issue"

    if api_result.get("status_code") >= 500:
        return "Code Issue"

    if db_result.get("null_email_count") > 0:
        return "Data Integrity Issue"

    if log_result.get("db_errors") > 5:
        return "Database Connectivity Issue"

    return "System Healthy"