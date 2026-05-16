import sqlite3
import os

db_path = "emlak.db"
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM system_management").fetchall()
    print("--- SYSTEM MANAGEMENT TABLE ---")
    for row in rows:
        print(f"Key: {row[0]}, Value: {row[1]}")
    conn.close()
else:
    print("DB not found.")
