"""
Discover stored procedures related to block/schedule refresh in Etere.
Run from Windows: py scripts/discover_block_refresh.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from browser_automation.etere_direct_client import connect

conn = connect()
cursor = conn.cursor()

keywords = ['refresh', 'block', 'schedul', 'program', 'fasce', 'blocch', 'aggiorn', 'ricalcol']

print("Stored procedures matching block/refresh keywords:")
print("=" * 60)
for kw in keywords:
    cursor.execute("""
        SELECT ROUTINE_NAME
        FROM INFORMATION_SCHEMA.ROUTINES
        WHERE ROUTINE_TYPE = 'PROCEDURE'
          AND ROUTINE_NAME LIKE ?
        ORDER BY ROUTINE_NAME
    """, f"%{kw}%")
    rows = cursor.fetchall()
    if rows:
        print(f"\n  [{kw}]")
        for r in rows:
            print(f"    {r[0]}")

# Also look at what tables store block assignments
print("\n\nTables related to blocks/programming:")
print("=" * 60)
for kw in ['block', 'fasc', 'program', 'schedul', 'slot', 'palins']:
    cursor.execute("""
        SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_NAME LIKE ?
        ORDER BY TABLE_NAME
    """, f"%{kw}%")
    rows = cursor.fetchall()
    if rows:
        print(f"\n  [{kw}]")
        for r in rows:
            print(f"    {r[0]}")

cursor.close()
conn.close()
