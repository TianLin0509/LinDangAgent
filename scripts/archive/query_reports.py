import sqlite3
import json

def query_reports(term):
    db_path = 'app/storage/reports.db'
    if not os.path.exists(db_path):
        print(f"DB not found: {db_path}")
        return
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # List tables
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cur.fetchall()
    print(f"Tables in reports.db: {tables}")
    
    for table_tuple in tables:
        table = table_tuple[0]
        try:
            cur.execute(f"PRAGMA table_info({table})")
            cols = [c[1] for c in cur.fetchall()]
            where_clause = " OR ".join([f"{col} LIKE ?" for col in cols])
            query = f"SELECT * FROM {table} WHERE {where_clause}"
            cur.execute(query, tuple([f'%{term}%'] * len(cols)))
            rows = cur.fetchall()
            for row in rows:
                print(f"Match in {table}: {row}")
        except Exception as e:
            print(f"Error querying {table}: {e}")
    conn.close()

import os
if __name__ == "__main__":
    query_reports("600763")
    query_reports("通策")
