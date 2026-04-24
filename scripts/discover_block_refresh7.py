"""
Inspect web_wf_getblocks and related SPs to find the correct block-assignment logic.
Run from Windows: py scripts/discover_block_refresh7.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from browser_automation.etere_direct_client import connect

conn = connect()
cursor = conn.cursor()

SPS = ['web_wf_getblocks', 'web_getPriceListBlocks', 'web_sales_getcontractlineblocks',
       'web_sales_addblocks', 'web_addblocks', 'web_refreshblocks']

# ── 1. Parameters of candidate SPs ───────────────────────────────────────────
print("=" * 60)
print("Parameters of block-related SPs")
print("=" * 60)
for sp in SPS:
    cursor.execute("""
        SELECT PARAMETER_NAME, DATA_TYPE, PARAMETER_MODE
        FROM INFORMATION_SCHEMA.PARAMETERS
        WHERE SPECIFIC_NAME = ?
        ORDER BY ORDINAL_POSITION
    """, sp)
    rows = cursor.fetchall()
    if rows:
        print(f"\n  [{sp}]")
        for r in rows:
            print(f"    {r[2]:6} {r[0]:40} {r[1]}")
    else:
        print(f"\n  [{sp}] — not found or no params")

# ── 2. All web_ SPs that mention 'block' or 'fasc' ───────────────────────────
print("\n" + "=" * 60)
print("All web_ SPs mentioning block/fasc in name")
print("=" * 60)
cursor.execute("""
    SELECT ROUTINE_NAME
    FROM INFORMATION_SCHEMA.ROUTINES
    WHERE ROUTINE_TYPE = 'PROCEDURE'
      AND ROUTINE_NAME LIKE 'web%'
      AND (ROUTINE_NAME LIKE '%block%' OR ROUTINE_NAME LIKE '%fasc%'
           OR ROUTINE_NAME LIKE '%Block%' OR ROUTINE_NAME LIKE '%Block%')
    ORDER BY ROUTINE_NAME
""")
for r in cursor.fetchall():
    print(f"  {r[0]}")

# ── 3. Find SPs that INSERT into CONTRATTIFASCE (searching by body) ───────────
# Note: INFORMATION_SCHEMA.ROUTINES may truncate long SP bodies
print("\n" + "=" * 60)
print("SPs with body containing INSERT + CONTRATTIFASCE (via syscomments)")
print("=" * 60)
cursor.execute("""
    SELECT DISTINCT o.name
    FROM sys.objects o
    JOIN sys.syscomments c ON c.id = o.object_id
    WHERE o.type = 'P'
      AND c.text LIKE '%CONTRATTIFASCE%'
    ORDER BY o.name
""")
rows = cursor.fetchall()
if rows:
    for r in rows:
        print(f"  {r[0]}")
else:
    print("  (none found)")

# ── 4. Find SPs that INSERT into CONTRATTIFASCE (via sys.sql_modules) ─────────
print("\n" + "=" * 60)
print("SPs with body containing CONTRATTIFASCE (via sys.sql_modules)")
print("=" * 60)
cursor.execute("""
    SELECT o.name
    FROM sys.objects o
    JOIN sys.sql_modules m ON m.object_id = o.object_id
    WHERE o.type = 'P'
      AND m.definition LIKE '%CONTRATTIFASCE%'
    ORDER BY o.name
""")
rows = cursor.fetchall()
if rows:
    for r in rows:
        print(f"  {r[0]}")
else:
    print("  (none found)")

cursor.close()
conn.close()
