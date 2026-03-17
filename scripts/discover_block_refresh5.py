"""
Find what table ID_FASCE values actually reference.
Run from Windows: py scripts/discover_block_refresh5.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from browser_automation.etere_direct_client import connect

KNOWN_IDS = [11202, 11208, 11958, 11959, 9898, 13530]
KNOWN_IDS_STR = ",".join(str(i) for i in KNOWN_IDS)

conn = connect()
cursor = conn.cursor()

# ── 1. TPALINSE structure ─────────────────────────────────────────────────────
print("=" * 60)
print("TPALINSE columns")
print("=" * 60)
cursor.execute("""
    SELECT COLUMN_NAME, DATA_TYPE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'TPALINSE'
    ORDER BY ORDINAL_POSITION
""")
cols_info = cursor.fetchall()
for r in cols_info:
    print(f"  {r[0]:40} {r[1]}")

# ── 2. Do any of our known IDs exist in TPALINSE? ────────────────────────────
print("\n" + "=" * 60)
print("TPALINSE: count and known ID matches")
print("=" * 60)
cursor.execute("SELECT COUNT(*) FROM TPALINSE")
print(f"  Total rows: {cursor.fetchone()[0]}")

cursor.execute(f"SELECT COUNT(*) FROM TPALINSE WHERE ID_TPALINSE IN ({KNOWN_IDS_STR})")
print(f"  Matches for known IDs: {cursor.fetchone()[0]}")

# ── 3. Sample TPALINSE rows ───────────────────────────────────────────────────
print("\n  Sample rows (top 3):")
cursor.execute("SELECT TOP 3 * FROM TPALINSE ORDER BY ID_TPALINSE")
cols = [d[0] for d in cursor.description]
print(f"  Cols: {cols}")
for row in cursor.fetchall():
    d = dict(zip(cols, row))
    print(" ", {k: v for k, v in d.items() if v is not None and v != '' and v != 0})

# ── 4. Brute-force: which tables have a column containing value 11202? ────────
print("\n" + "=" * 60)
print("Hunting for table containing ID_FASCE=11202")
print("=" * 60)
cursor.execute("""
    SELECT t.TABLE_NAME, c.COLUMN_NAME
    FROM INFORMATION_SCHEMA.TABLES t
    JOIN INFORMATION_SCHEMA.COLUMNS c ON c.TABLE_NAME = t.TABLE_NAME
    WHERE t.TABLE_TYPE = 'BASE TABLE'
      AND c.COLUMN_NAME LIKE '%fasc%'
    ORDER BY t.TABLE_NAME, c.COLUMN_NAME
""")
for r in cursor.fetchall():
    tbl, col = r[0], r[1]
    try:
        cursor.execute(f"SELECT COUNT(*) FROM [{tbl}] WHERE [{col}] = 11202")
        cnt = cursor.fetchone()[0]
        cursor.execute(f"SELECT COUNT(*) FROM [{tbl}]")
        total = cursor.fetchone()[0]
        print(f"  {tbl:40} .{col:30} total={total:6}  matches={cnt}")
    except Exception as e:
        print(f"  {tbl}.{col} ERROR: {e}")

cursor.close()
conn.close()
