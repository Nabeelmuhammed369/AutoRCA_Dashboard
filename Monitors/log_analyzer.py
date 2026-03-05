import logging
import os

logger = logging.getLogger("LOG_ANALYZER")

def analyze_logs(log_file):
    try:
        logger.info(f"Analyzing log file: {log_file}")

        if not os.path.exists(log_file):
            logger.error("Log file not found.")
            return {"total_errors": 0, "db_errors": 0}

        error_count = 0
        db_errors = 0

        with open(log_file, "r") as file:
            lines = file.readlines()

        for line in lines:
            if "ERROR" in line:
                error_count += 1
            if "DB_CONN_FAIL" in line:
                db_errors += 1

        logger.info(f"Total errors found: {error_count}")

        return {
            "total_errors": error_count,
            "db_errors": db_errors
        }

    except Exception:
        logger.exception("Log analysis failed.")
        return {"total_errors": 0, "db_errors": 0}