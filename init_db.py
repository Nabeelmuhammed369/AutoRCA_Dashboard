import sqlite3

# Connect to the database file defined in your config
conn = sqlite3.connect("app.db")
cursor = conn.cursor()

print("Creating 'users' table...")
# 1. Create the table structure
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT,
    email TEXT
)
""")

# 2. Insert sample data (one valid, one with a NULL email to trigger the RCA)
cursor.execute("INSERT INTO users (name, email) VALUES ('Admin User', 'admin@company.com')")
cursor.execute("INSERT INTO users (name, email) VALUES ('Broken Record', NULL)")

conn.commit()
conn.close()
print("✅ Database initialized with 'users' table and sample data!")