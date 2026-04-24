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
        tp.Date,
        tp.offset,
        tp.Cod_User,
        tp.id_palinsesto,
        tp.scadenza
    FROM CONTRATTIFASCE cf
    JOIN trafficPalinse tp ON tp.id_fascia = cf.ID_FASCE
    WHERE cf.ID_CONTRATTIRIGHE IN ({KNOWN_LINE_IDS_STR})
    ORDER BY cf.ID_CONTRATTIRIGHE, tp.Date
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

# ── 5. Find distinct id_fascia values for NYC market, 2PM-3PM, future dates ───
print("\n" + "=" * 60)
print("Distinct id_fascia for Cod_User=1 (NYC), offset≈14:00-15:00, future dates")
print("=" * 60)
FRAMES = 29.97
start_f = round(14 * 3600 * FRAMES)
end_f   = round(15 * 3600 * FRAMES)
cursor.execute("""
    SELECT DISTINCT TOP 20 id_fascia, id_palinsesto, Cod_User, MIN(Date) as first_date, MAX(Date) as last_date
    FROM trafficPalinse
    WHERE Cod_User = 1
      AND offset >= ? AND offset < ?
      AND Date >= '2026-03-01'
    GROUP BY id_fascia, id_palinsesto, Cod_User
    ORDER BY first_date
""", start_f, end_f)
cols = [d[0] for d in cursor.description]
for row in cursor.fetchall():
    print(" ", dict(zip(cols, row)))

# ── 6. What id_fascia values does a real NYC line use (recent contract)? ──────
print("\n" + "=" * 60)
print("CONTRATTIFASCE for a recent NYC contract (CONTRATTITESTATA like IW Lexus%)")
print("=" * 60)
cursor.execute("""
    SELECT TOP 1 ct.ID_CONTRATTITESTATA, ct.COD_CONTRATTO
    FROM CONTRATTITESTATA ct
    WHERE ct.COD_CONTRATTO LIKE 'IW Lexus%'
    ORDER BY ct.ID_CONTRATTITESTATA DESC
""")
row = cursor.fetchone()
if row:
    cid, code = row
    print(f"  Contract: {code} (id={cid})")
    cursor.execute(f"""
        SELECT cf.ID_CONTRATTIRIGHE, cf.ID_FASCE,
               cr.ORA_INIZIO, cr.ORA_FINE, cr.LUNEDI, cr.MARTEDI, cr.VENERDI
        FROM CONTRATTIFASCE cf
        JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = cf.ID_CONTRATTIRIGHE
        WHERE cr.ID_CONTRATTITESTATA = {cid}
        ORDER BY cf.ID_CONTRATTIRIGHE, cf.ID_FASCE
    """)
    cols = [d[0] for d in cursor.description]
    for row in cursor.fetchall():
        print(" ", dict(zip(cols, row)))

cursor.close()
conn.close()
