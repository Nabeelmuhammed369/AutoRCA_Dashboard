import logging
import sqlite3

logger = logging.getLogger("DB_VALIDATOR")


def validate_data(db_path):
    try:
        logger.info(f"Connecting to database: {db_path}")

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM users WHERE email IS NULL")
        null_emails = cursor.fetchone()[0]

        conn.close()

        logger.info(f"Null email count: {null_emails}")

        return {"null_email_count": null_emails}

    except sqlite3.OperationalError:
        logger.error("Database table missing or query failed.")
        return {"null_email_count": 0}

    except Exception:
        logger.exception("Unexpected database error.")
        return {"null_email_count": 0}
