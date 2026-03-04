import sqlite3

def validate_data(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM users WHERE email IS NULL")
    null_emails = cursor.fetchone()[0]

    conn.close()

    return {
        "null_email_count": null_emails
    }