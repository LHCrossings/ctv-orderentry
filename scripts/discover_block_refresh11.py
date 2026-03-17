"""
Test TPALINSE.ORA as the block-assignment filter instead of trafficPalinse.offset.
Run from Windows: py scripts/discover_block_refresh11.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from browser_automation.etere_direct_client import connect

FRAMES = 29.97

conn = connect()
cursor = conn.cursor()

LINES = [
    (73173, 20*3600*FRAMES, 21*3600*FRAMES, 5, "2026-01-05", "2026-01-11"),
    (73175, 21*3600*FRAMES, 22*3600*FRAMES, 5, "2026-01-05", "2026-01-11"),
    (73177, 22*3600*FRAMES, 22*3600*FRAMES, 5, "2026-03-16", "2026-03-22"),
    (73178, 22*3600*FRAMES, 23.5*3600*FRAMES, 5, "2026-03-16", "2026-03-29"),
]

print("Testing TPALINSE.ORA filter (ORA_INI <= TPALINSE.ORA < ORA_FIN, or = ORA_INI for point-in-time)")
print("=" * 70)

for lid, ora_ini, ora_fin, cod_user, d_from, d_to in LINES:
    ora_ini_r = round(ora_ini)
    ora_fin_r = round(ora_fin)
    is_point = (ora_ini_r == ora_fin_r)

    # What's assigned
    cursor.execute("SELECT ID_FASCE FROM CONTRATTIFASCE WHERE ID_CONTRATTIRIGHE = ?", lid)
    assigned = set(r[0] for r in cursor.fetchall())

    # Rule: include id_fascia if at least one TPALINSE.ORA is in [ORA_INI, ORA_FIN)
    # For point-in-time: ORA_INI <= TPALINSE.ORA <= ORA_INI + 2h (generous window)
    if is_point:
        ub = ora_ini_r + round(2 * 3600 * FRAMES)
        cursor.execute("""
            SELECT DISTINCT tp.id_fascia
            FROM trafficPalinse tp
            JOIN TPALINSE t ON t.ID_TPALINSE = tp.id_tpalinse
            WHERE tp.Cod_User = ?
              AND tp.Date >= ? AND tp.Date <= ?
              AND t.ORA >= ? AND t.ORA < ?
        """, cod_user, d_from, d_to, ora_ini_r, ub)
    else:
        cursor.execute("""
            SELECT DISTINCT tp.id_fascia
            FROM trafficPalinse tp
            JOIN TPALINSE t ON t.ID_TPALINSE = tp.id_tpalinse
            WHERE tp.Cod_User = ?
              AND tp.Date >= ? AND tp.Date <= ?
              AND t.ORA >= ? AND t.ORA < ?
        """, cod_user, d_from, d_to, ora_ini_r, ora_fin_r)

    predicted = set(r[0] for r in cursor.fetchall())
    correct = assigned & predicted
    missed  = assigned - predicted
    extra   = predicted - assigned

    print(f"\nLine {lid} ({'POINT' if is_point else 'RANGE'} {ora_ini_r/FRAMES/3600:.1f}h–{ora_fin_r/FRAMES/3600:.1f}h) {d_from}–{d_to}")
    print(f"  Assigned:  {sorted(assigned)}")
    print(f"  Predicted: {sorted(predicted)}")
    print(f"  MISSED:    {sorted(missed)}")
    print(f"  EXTRA:     {sorted(extra)}")

# ── Verify TPALINSE.ORA for 9940 in January date range ────────────────────────
print("\n" + "=" * 70)
print("TPALINSE.ORA for id_fascia=9940 in Jan 2026 (73175 date range)")
print("=" * 70)
cursor.execute("""
    SELECT DISTINCT t.ORA
    FROM trafficPalinse tp
    JOIN TPALINSE t ON t.ID_TPALINSE = tp.id_tpalinse
    WHERE tp.id_fascia = 9940
      AND tp.Cod_User = 5
      AND tp.Date >= '2026-01-05' AND tp.Date <= '2026-01-11'
    ORDER BY t.ORA
""")
rows = cursor.fetchall()
print(f"  Distinct TPALINSE.ORA: {[f'{r[0]/FRAMES/3600:.3f}h' for r in rows[:10]]}")

cursor.close()
conn.close()
