import logging

logger = logging.getLogger("RCA_ENGINE")


def classify_issue(api_result, log_result, db_result):

    logger.info("Classifying root cause...")

    if "error" in api_result:
        logger.warning("Infrastructure issue detected.")
        return "Infrastructure Issue"

    if api_result.get("status_code", 200) >= 500:
        logger.warning("Code issue detected.")
        return "Code Issue"

    if db_result.get("null_email_count", 0) > 0:
        logger.warning("Data integrity issue detected.")
        return "Data Integrity Issue"

    if log_result.get("db_errors", 0) > 5:
        logger.warning("Database connectivity issue detected.")
        return "Database Connectivity Issue"

    logger.info("System is healthy.")
    return "System Healthy"