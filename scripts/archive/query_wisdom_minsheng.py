import sqlite3

def check_wisdom():
    conn = sqlite3.connect("data/knowledge/wisdom.db")
    cursor = conn.cursor()
    # Find any entry related to 600016
    cursor.execute("SELECT * FROM wisdom WHERE ts_code='600016.SH' OR ts_code='600016'")
    rows = cursor.fetchall()
    if rows:
        print(f"Found {len(rows)} entries in wisdom.db:")
        for row in rows:
            print(row)
    else:
        print("No entry in wisdom.db for 600016.")
    conn.close()

if __name__ == "__main__":
    check_wisdom()
