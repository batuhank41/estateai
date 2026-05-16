import sqlite3
import os

db_path = "emlak.db"
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print(f"Tables: {tables}")
    for table in tables:
        tname = table[0]
        cursor.execute(f"PRAGMA table_info({tname});")
        info = cursor.fetchall()
        print(f"Table {tname} info: {info}")
    conn.close()
else:
    print("DB not found.")
