"""
Understand trafficPalinse structure to build block-refresh INSERT logic.
Run from Windows: py scripts/discover_block_refresh6.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from browser_automation.etere_direct_client import connect

KNOWN_IDS = [11202, 11208, 11958, 11959, 9898, 13530]
KNOWN_IDS_STR = ",".join(str(i) for i in KNOWN_IDS)

# Known contract line IDs from contract 2381 → their CONTRATTIFASCE entries
KNOWN_LINE_IDS = [70380, 70381, 70382, 70383, 70394, 70395, 70396, 70397]
KNOWN_LINE_IDS_STR = ",".join(str(i) for i in KNOWN_LINE_IDS)

conn = connect()
cursor = conn.cursor()

# ── 1. trafficPalinse columns ────────────────────────────────────────────────
print("=" * 60)
print("trafficPalinse columns")
print("=" * 60)
cursor.execute("""
    SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'trafficPalinse'
    ORDER BY ORDINAL_POSITION
""")
for r in cursor.fetchall():
    print(f"  {r[0]:40} {r[1]:15} {r[2]}")

# ── 2. Sample matching rows ───────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"trafficPalinse rows where id_fascia IN {KNOWN_IDS[:4]} (top 5)")
print("=" * 60)
cursor.execute(f"SELECT TOP 5 * FROM trafficPalinse WHERE id_fascia IN ({KNOWN_IDS_STR})")
cols = [d[0] for d in cursor.description]
print(f"  Cols: {cols}")
for row in cursor.fetchall():
    d = dict(zip(cols, row))
    print(" ", {k: v for k, v in d.items() if v is not None and v != 0 and v != ''})

# ── 3. Join CONTRATTIFASCE + trafficPalinse for contract 2381 ─────────────────
print("\n" + "=" * 60)
print("CONTRATTIFASCE joined to trafficPalinse for contract 2381 (top 5)")
print("=" * 60)
cursor.execute(f"""
    SELECT TOP 5
        cf.ID_CONTRATTIRIGHE,
        cf.ID_FASCE,
        tp.id_fascia,
        tp.data,
        tp.ora,
        tp.COD_USER,
        tp.NEWTYPE,
        tp.TITLE
    FROM CONTRATTIFASCE cf
    JOIN trafficPalinse tp ON tp.id_fascia = cf.ID_FASCE
    WHERE cf.ID_CONTRATTIRIGHE IN ({KNOWN_LINE_IDS_STR})
    ORDER BY cf.ID_CONTRATTIRIGHE, tp.data
""")
cols = [d[0] for d in cursor.description]
for row in cursor.fetchall():
    print(" ", dict(zip(cols, row)))

# ── 4. How many trafficPalinse rows per line? (pattern check) ─────────────────
print("\n" + "=" * 60)
print("CONTRATTIFASCE: blocks per line for contract 2381")
print("=" * 60)
cursor.execute(f"""
    SELECT cf.ID_CONTRATTIRIGHE, COUNT(*) as block_count
    FROM CONTRATTIFASCE cf
    WHERE cf.ID_CONTRATTIRIGHE IN ({KNOWN_LINE_IDS_STR})
    GROUP BY cf.ID_CONTRATTIRIGHE
    ORDER BY cf.ID_CONTRATTIRIGHE
""")
for row in cursor.fetchall():
    print(f"  line {row[0]}: {row[1]} blocks")

# ── 5. What does a trafficPalinse "block" row look like (non-ad events)? ──────
print("\n" + "=" * 60)
print("trafficPalinse: distinct NEWTYPE values (sample)")
print("=" * 60)
cursor.execute("SELECT DISTINCT TOP 20 NEWTYPE FROM trafficPalinse ORDER BY NEWTYPE")
for row in cursor.fetchall():
    print(f"  {row[0]!r}")

cursor.close()
conn.close()
