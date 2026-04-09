import sqlite3
import os

db_path = 'data/knowledge/wisdom.db'
if not os.path.exists(db_path):
    print(f"DB not found at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
print(f"Tables: {cur.fetchall()}")

try:
    cur.execute("PRAGMA table_info(wisdom_entries)")
    cols = cur.fetchall()
    print(f"Columns: {cols}")
except Exception as e:
    print(f"Error: {e}")

conn.close()
