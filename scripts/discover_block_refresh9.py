"""
Reverse-engineer the block-assignment rule by comparing ORA_INIZIO/ORA_FINE
of contract lines against the actual trafficPalinse offsets of their id_fascia values.
Run from Windows: py scripts/discover_block_refresh9.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from browser_automation.etere_direct_client import connect

FRAMES = 29.97

conn = connect()
cursor = conn.cursor()

# Pull a sample of lines from a recent contract with known-good block assignments
# Use IW Lexus SEA 2583 (line IDs 73173-73196)
CONTRACT_ID = 2583

print("=" * 60)
print(f"Block assignment analysis for contract {CONTRACT_ID}")
print("=" * 60)
print(f"{'LineID':8} {'ORA_INI_h':10} {'ORA_FIN_h':10} {'id_fascia':10} {'tp_offset_h':12} {'tp_Date'}")
print("-" * 80)

cursor.execute("""
    SELECT
        cr.ID_CONTRATTIRIGHE,
        cr.ORA_INIZIO,
        cr.ORA_FINE,
        cr.DATA_INIZIO,
        cr.DATA_FINE,
        cr.COD_USER,
        cf.ID_FASCE
    FROM CONTRATTIRIGHE cr
    JOIN CONTRATTIFASCE cf ON cf.ID_CONTRATTIRIGHE = cr.ID_CONTRATTIRIGHE
    WHERE cr.ID_CONTRATTITESTATA = ?
    ORDER BY cr.ID_CONTRATTIRIGHE, cf.ID_FASCE
""", CONTRACT_ID)
line_blocks = cursor.fetchall()

# For each (line, fascia) pair, look up representative trafficPalinse offsets
seen = set()
for row in line_blocks:
    lid, ora_ini, ora_fin, d_from, d_to, cod_user, id_fascia = row
    key = (lid, id_fascia)
    if key in seen:
        continue
    seen.add(key)

    # Get sample trafficPalinse offsets for this id_fascia + Cod_User + date range
    cursor.execute("""
        SELECT MIN(offset) as min_off, MAX(offset) as max_off, COUNT(*) as cnt
        FROM trafficPalinse
        WHERE id_fascia = ?
          AND Cod_User = ?
          AND Date >= ? AND Date <= ?
    """, id_fascia, cod_user, d_from, d_to)
    tp = cursor.fetchone()
    if tp and tp[0] is not None:
        min_h = tp[0] / FRAMES / 3600
        max_h = tp[1] / FRAMES / 3600
        ini_h = (ora_ini or 0) / FRAMES / 3600
        fin_h = (ora_fin or 0) / FRAMES / 3600
        print(f"{lid:<8} {ini_h:9.2f}h {fin_h:9.2f}h {id_fascia:<10} {min_h:10.2f}h-{max_h:.2f}h  cnt={tp[2]}")
    else:
        ini_h = (ora_ini or 0) / FRAMES / 3600
        fin_h = (ora_fin or 0) / FRAMES / 3600
        print(f"{lid:<8} {ini_h:9.2f}h {fin_h:9.2f}h {id_fascia:<10} (no trafficPalinse rows in date range)")

# ── Also check: what trafficPalinse offsets got EXCLUDED? ──────────────────────
# For one line, show ALL trafficPalinse id_fascia values in the time+date window
# vs what was actually assigned
print("\n" + "=" * 60)
print("Line 73177 (ORA_INI=ORA_FIN=~22h): all trafficPalinse id_fascia in date range")
print("=" * 60)
cursor.execute("""
    SELECT cr.ORA_INIZIO, cr.ORA_FINE, cr.DATA_INIZIO, cr.DATA_FINE, cr.COD_USER
    FROM CONTRATTIRIGHE cr WHERE cr.ID_CONTRATTIRIGHE = 73177
""")
r = cursor.fetchone()
if r:
    ora_ini, ora_fin, d_from, d_to, cod_user = r
    print(f"  Line: ORA_INI={ora_ini/FRAMES/3600:.2f}h ORA_FIN={ora_fin/FRAMES/3600:.2f}h  {d_from} – {d_to}  Cod_User={cod_user}")

    # What's in CONTRATTIFASCE
    cursor.execute("SELECT ID_FASCE FROM CONTRATTIFASCE WHERE ID_CONTRATTIRIGHE = 73177")
    assigned = [r[0] for r in cursor.fetchall()]
    print(f"  Assigned id_fascia: {assigned}")

    # All trafficPalinse in the date range for this market
    window = round(1 * 3600 * FRAMES)  # 1-hour window around the point
    cursor.execute("""
        SELECT DISTINCT id_fascia, MIN(offset) as min_off, MAX(offset) as max_off
        FROM trafficPalinse
        WHERE Cod_User = ?
          AND Date >= ? AND Date <= ?
          AND offset >= ? AND offset <= ?
        GROUP BY id_fascia
        ORDER BY min_off
    """, cod_user, d_from, d_to, ora_ini - window, ora_ini + window)
    print(f"  All id_fascia with offset within ±1h of {ora_ini/FRAMES/3600:.2f}h:")
    for tp_row in cursor.fetchall():
        marker = " ← ASSIGNED" if tp_row[0] in assigned else ""
        print(f"    id_fascia={tp_row[0]}  offset={tp_row[1]/FRAMES/3600:.3f}h-{tp_row[2]/FRAMES/3600:.3f}h{marker}")

cursor.close()
conn.close()
