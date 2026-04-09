import sqlite3
import json

def query_wisdom(term):
    conn = sqlite3.connect('data/knowledge/wisdom.db')
    cur = conn.cursor()
    query = "SELECT wisdom, context, tags FROM wisdom_entries WHERE wisdom LIKE ? OR context LIKE ? OR tags LIKE ?"
    cur.execute(query, (f'%{term}%', f'%{term}%', f'%{term}%'))
    rows = cur.fetchall()
    for row in rows:
        print(f"Wisdom: {row[0]}")
        print(f"Context: {row[1]}")
        print(f"Tags: {row[2]}")
        print("-" * 20)
    conn.close()

if __name__ == "__main__":
    query_wisdom("通策")
    query_wisdom("600763")
