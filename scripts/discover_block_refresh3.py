"""
Find the real blocks table that CONTRATTIFASCE.ID_FASCE references.
Run from Windows: py scripts/discover_block_refresh3.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from browser_automation.etere_direct_client import connect

FRAMES = 29.97
# Known ID_FASCE values from contract 2381
KNOWN_IDS = [11202, 11208, 11958, 11959, 9898, 13530, 11205, 11206, 9764, 9754]
KNOWN_IDS_STR = ",".join(str(i) for i in KNOWN_IDS)

conn = connect()
cursor = conn.cursor()

# ── 1. Find which table contains these IDs ────────────────────────────────────
print("=" * 60)
print(f"Tables containing ID_FASCE values {KNOWN_IDS[:3]}...")
print("=" * 60)

candidate_tables = ['tlfasceorarie', 'tlfasceorarieh', 'ffasce', 'FASCE', 'SLOTS', 'PROPSLOTS']
for tbl in candidate_tables:
    try:
        cursor.execute(f"SELECT COUNT(*) FROM {tbl}")
        total = cursor.fetchone()[0]
        # Try to find our known IDs
        cursor.execute(f"SELECT TOP 1 * FROM {tbl}")
        cols = [d[0] for d in cursor.description]
        id_col = next((c for c in cols if 'fasc' in c.lower() or c.upper() in ('ID', 'ID_FASCE', 'ID_FASCIA')), cols[0])
        cursor.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {id_col} IN ({KNOWN_IDS_STR})")
        matches = cursor.fetchone()[0]
        print(f"  {tbl:25} rows={total:6}  cols={cols[:4]}  matches_on_{id_col}={matches}")
    except Exception as e:
        print(f"  {tbl:25} ERROR: {e}")

# ── 2. tlfasceorarie structure + sample ───────────────────────────────────────
print("\n" + "=" * 60)
print("tlfasceorarie structure + sample")
print("=" * 60)
try:
    cursor.execute("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'tlfasceorarie'
        ORDER BY ORDINAL_POSITION
    """)
    for r in cursor.fetchall():
        print(f"  {r[0]:40} {r[1]}")

    print("\n  Sample rows (top 5):")
    cursor.execute("SELECT TOP 5 * FROM tlfasceorarie ORDER BY 1")
    cols = [d[0] for d in cursor.description]
    for row in cursor.fetchall():
        print(" ", dict(zip(cols, row)))

    # blocks overlapping 14:00-15:00
    start_f = round(14 * 3600 * FRAMES)
    end_f   = round(15 * 3600 * FRAMES)
    ora_ini_col = next((c for c in cols if 'ini' in c.lower() or 'start' in c.lower() or 'begin' in c.lower()), None)
    ora_fin_col = next((c for c in cols if 'fin' in c.lower() or 'end' in c.lower()), None)
    if ora_ini_col and ora_fin_col:
        print(f"\n  Blocks overlapping 14:00-15:00 (frames {start_f}-{end_f}):")
        cursor.execute(f"""
            SELECT TOP 10 * FROM tlfasceorarie
            WHERE {ora_ini_col} < ? AND {ora_fin_col} > ?
            ORDER BY {ora_ini_col}
        """, end_f, start_f)
        for row in cursor.fetchall():
            print(" ", dict(zip(cols, row)))
except Exception as e:
    print(f"  ERROR: {e}")

# ── 3. Look up our known FASCE IDs in tlfasceorarie ──────────────────────────
print("\n" + "=" * 60)
print("tlfasceorarie rows for known ID_FASCE values")
print("=" * 60)
try:
    cursor.execute(f"SELECT TOP 5 * FROM tlfasceorarie WHERE id_tlfasceorarie IN ({KNOWN_IDS_STR})")
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    if rows:
        for row in rows:
            print(" ", dict(zip(cols, row)))
    else:
        print("  (no matches on id_tlfasceorarie)")
        # Try first column
        cursor.execute("SELECT TOP 1 * FROM tlfasceorarie")
        first_col = cursor.description[0][0]
        cursor.execute(f"SELECT TOP 5 * FROM tlfasceorarie WHERE {first_col} IN ({KNOWN_IDS_STR})")
        rows = cursor.fetchall()
        if rows:
            for row in rows:
                print(" ", dict(zip(cols, row)))
        else:
            print(f"  (no matches on {first_col} either)")
except Exception as e:
    print(f"  ERROR: {e}")

cursor.close()
conn.close()
