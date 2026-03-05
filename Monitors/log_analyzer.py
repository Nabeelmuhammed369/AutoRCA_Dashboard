import logging
import os

logger = logging.getLogger("LOG_ANALYZER")

def analyze_logs(log_file):
    # Initialize structured stats with all required industry categories
    stats = {
        "total_errors": 0,
        "categories": {
            "Database": 0,
            "Network": 0,
            "API/Gateway": 0,
            "Security/Firewall": 0,
            "ActiveDirectory": 0,
            "Application": 0
        },
        "critical_issues": [] # Store high-priority alerts
    }

    try:
        logger.info(f"Analyzing log file: {log_file}")

        if not os.path.exists(log_file):
            logger.error(f"Log file not found: {log_file}")
            return stats

        with open(log_file, "r") as file:
            for line in file:
                # 1. Detect if the line is an ERROR or CRITICAL status
                if "ERROR" in line or "CRITICAL" in line:
                    stats["total_errors"] += 1
                    
                    # 2. Categorize the error based on the module tags
                    if "[Database]" in line or "DB_CONN_FAIL" in line or "DEADLOCK" in line:
                        stats["categories"]["Database"] += 1
                    elif "[Network]" in line:
                        stats["categories"]["Network"] += 1
                    elif "[API]" in line or "[Gateway]" in line:
                        stats["categories"]["API/Gateway"] += 1
                    elif "[Firewall]" in line or "[Access]" in line:
                        stats["categories"]["Security/Firewall"] += 1
                    elif "[ActiveDirectory]" in line:
                        stats["categories"]["ActiveDirectory"] += 1
                    else:
                        stats["categories"]["Application"] += 1

                    # 3. Specifically track CRITICAL logs for the "Recent Alerts" UI
                    if "CRITICAL" in line:
                        stats["critical_issues"].append(line.strip())

        logger.info(f"Analysis complete. Found {stats['total_errors']} errors across {len(stats['categories'])} categories.")
        return stats

    except Exception:
        logger.exception("Log analysis failed due to an unexpected error.")
        return stats