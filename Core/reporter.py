import logging
from datetime import datetime

logger = logging.getLogger("REPORTER")

def generate_report(api, logs, db, classification):

    try:
        report = f"""
===== AUTO RCA REPORT =====
Timestamp: {datetime.now()}

API Status Code: {api.get('status_code')}
API Response Time: {api.get('response_time')}

Total Log Errors: {logs.get('total_errors')}
DB Error Logs: {logs.get('db_errors')}

Null Emails in DB: {db.get('null_email_count')}

FINAL CLASSIFICATION:
>>> {classification}
"""

        print(report)

        with open("rca_report.txt", "w") as file:
            file.write(report)

        logger.info("RCA report generated successfully.")

    except Exception:
        logger.exception("Failed to generate report.")