"""
Deep-dive: CONTRATTIFASCE structure + SPs that reference it.
Run from Windows: py scripts/discover_block_refresh2.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from browser_automation.etere_direct_client import connect

conn = connect()
cursor = conn.cursor()

# ── 1. CONTRATTIFASCE columns ─────────────────────────────────────────────────
print("=" * 60)
print("CONTRATTIFASCE columns")
print("=" * 60)
cursor.execute("""
    SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'CONTRATTIFASCE'
    ORDER BY ORDINAL_POSITION
""")
for r in cursor.fetchall():
    print(f"  {r[0]:40} {r[1]:15} {r[2]}")

# ── 2. Sample rows from CONTRATTIFASCE for a known contract ──────────────────
print("\n" + "=" * 60)
print("CONTRATTIFASCE sample (contract 2381, via CONTRATTIRIGHE join)")
print("=" * 60)
cursor.execute("""
    SELECT TOP 5 cf.*
    FROM CONTRATTIFASCE cf
    JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = cf.ID_CONTRATTIRIGHE
    WHERE cr.ID_CONTRATTITESTATA = 2381
""")
cols = [d[0] for d in cursor.description]
print("  Cols:", cols)
for row in cursor.fetchall():
    print(" ", dict(zip(cols, row)))

# ── 3. SPs that mention CONTRATTIFASCE ───────────────────────────────────────
print("\n" + "=" * 60)
print("SPs referencing CONTRATTIFASCE")
print("=" * 60)
cursor.execute("""
    SELECT ROUTINE_NAME
    FROM INFORMATION_SCHEMA.ROUTINES
    WHERE ROUTINE_TYPE = 'PROCEDURE'
      AND ROUTINE_DEFINITION LIKE '%CONTRATTIFASCE%'
    ORDER BY ROUTINE_NAME
""")
for r in cursor.fetchall():
    print(f"  {r[0]}")

# ── 4. FASCE table structure ──────────────────────────────────────────────────
print("\n" + "=" * 60)
print("FASCE columns")
print("=" * 60)
cursor.execute("""
    SELECT COLUMN_NAME, DATA_TYPE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'FASCE'
    ORDER BY ORDINAL_POSITION
""")
for r in cursor.fetchall():
    print(f"  {r[0]:40} {r[1]}")

# ── 5. Sample FASCE rows (NYC market blocks) ──────────────────────────────────
print("\n" + "=" * 60)
print("FASCE sample (market=1 i.e. NYC, top 10)")
print("=" * 60)
cursor.execute("""
    SELECT TOP 10 * FROM FASCE
    WHERE ID_STAZIONE = 1
    ORDER BY ORA_INIZIO
""")
cols = [d[0] for d in cursor.description]
print("  Cols:", cols)
for row in cursor.fetchall():
    d = dict(zip(cols, row))
    print(" ", {k: v for k, v in d.items() if v is not None and v != ''})

# ── 6. Does our test contract have any CONTRATTIFASCE rows? ──────────────────
print("\n" + "=" * 60)
print("CONTRATTIFASCE rows for most recent test contract (TEST LEXUS)")
print("=" * 60)
cursor.execute("""
    SELECT TOP 1 ID_CONTRATTITESTATA FROM CONTRATTITESTATA
    WHERE COD_CONTRATTO LIKE 'TEST LEXUS%'
    ORDER BY ID_CONTRATTITESTATA DESC
""")
row = cursor.fetchone()
if row:
    test_cid = row[0]
    print(f"  Contract ID: {test_cid}")
    cursor.execute("""
        SELECT COUNT(*) FROM CONTRATTIFASCE cf
        JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = cf.ID_CONTRATTIRIGHE
        WHERE cr.ID_CONTRATTITESTATA = ?
    """, test_cid)
    cnt = cursor.fetchone()[0]
    print(f"  CONTRATTIFASCE rows: {cnt}")
else:
    print("  No test contract found")

cursor.close()
conn.close()
