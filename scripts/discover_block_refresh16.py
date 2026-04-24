"""
Test: NEWTYPE='PGM' + exact -9 frame shift (Etere stores program starts 9 frames
before the hour boundary: ORA = hour*107892 - 9).
Rule: ORA >= (ORA_INI - 9) AND ORA < (ORA_FIN - 9)
For point-in-time: ORA < (ORA_INI + 1h - 9)
Run from Windows: py scripts/discover_block_refresh16.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from browser_automation.etere_direct_client import connect

FRAMES = 29.97
FPH = round(FRAMES * 3600)   # 107892 frames per hour
PREROLL = 9                  # frames before the hour boundary

# DATEPART(dw): 1=Sun,2=Mon,3=Tue,4=Wed,5=Thu,6=Fri,7=Sat
DAY_PATTERNS = {
    "M-F":  {2, 3, 4, 5, 6},
    "M-Sa": {2, 3, 4, 5, 6, 7},
    "M-Su": {1, 2, 3, 4, 5, 6, 7},
}

conn = connect()
cursor = conn.cursor()

LINES = [
    (73173, "M-F",  20.0, 21.0, 5, "2026-01-05", "2026-01-11", False),
    (73175, "M-F",  21.0, 22.0, 5, "2026-01-05", "2026-01-11", False),
    (73177, "M-Su", 22.0, 22.0, 5, "2026-03-16", "2026-03-22", True),
    (73178, "M-Su", 22.0, 23.5, 5, "2026-03-16", "2026-03-29", False),
]

print("Testing: NEWTYPE='PGM' + ORA in [ORA_INI-9, ORA_FIN-9) + DW filter")
print(f"  PREROLL={PREROLL} frames  FPH={FPH}")
print("=" * 70)

for lid, day_pat, ora_ini_h, ora_fin_h, cod_user, d_from, d_to, is_point in LINES:
    ora_ini = round(ora_ini_h * 3600 * FRAMES)
    ora_fin = round(ora_fin_h * 3600 * FRAMES)
    dws = DAY_PATTERNS[day_pat]
    dw_ph = ",".join("?" * len(dws))

    lb = ora_ini - PREROLL
    if is_point:
        ub = ora_ini + FPH - PREROLL    # 1-hour window
    else:
        ub = ora_fin - PREROLL

    cursor.execute("SELECT ID_FASCE FROM CONTRATTIFASCE WHERE ID_CONTRATTIRIGHE = ?", lid)
    assigned = set(r[0] for r in cursor.fetchall())

    cursor.execute(f"""
        SELECT DISTINCT tp.id_fascia
        FROM trafficPalinse tp
        JOIN TPALINSE t ON t.ID_TPALINSE = tp.id_tpalinse
        WHERE tp.Cod_User = ?
          AND tp.Date >= ? AND tp.Date <= ?
          AND t.NEWTYPE = 'PGM'
          AND t.ORA >= ? AND t.ORA < ?
          AND DATEPART(dw, tp.Date) IN ({dw_ph})
    """, cod_user, d_from, d_to, lb, ub, *dws)
    predicted = set(r[0] for r in cursor.fetchall())

    missed = assigned - predicted
    extra  = predicted - assigned
    status = "PERFECT" if not missed and not extra else "MISMATCH"
    label = f"{'POINT' if is_point else 'RANGE'} {ora_ini_h:.1f}h–{ora_fin_h:.1f}h [{day_pat}]"
    print(f"\nLine {lid} {label}  [{status}]")
    print(f"  lb={lb}  ub={ub}  (window: {lb/FRAMES/3600:.4f}h – {ub/FRAMES/3600:.4f}h)")
    print(f"  Assigned:  {sorted(assigned)}")
    print(f"  Predicted: {sorted(predicted)}")
    if missed:
        print(f"  MISSED:    {sorted(missed)}")
    if extra:
        print(f"  EXTRA:     {sorted(extra)}")
    # Show ORA detail for EXTRA blocks
    if extra:
        print("  EXTRA detail:")
        for fid in sorted(extra)[:5]:
            cursor.execute(f"""
                SELECT DISTINCT t.ORA, DATEPART(dw, tp.Date) as dw
                FROM trafficPalinse tp
                JOIN TPALINSE t ON t.ID_TPALINSE = tp.id_tpalinse
                WHERE tp.id_fascia = ? AND tp.Cod_User = ?
                  AND tp.Date >= ? AND tp.Date <= ?
                  AND t.NEWTYPE = 'PGM'
                  AND t.ORA >= ? AND t.ORA < ?
                  AND DATEPART(dw, tp.Date) IN ({dw_ph})
            """, fid, cod_user, d_from, d_to, lb, ub, *dws)
            rows = cursor.fetchall()
            print(f"    id_fascia={fid}: {[(r[0], ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][r[1]-1]) for r in rows[:4]]}")
    # Show ORA detail for MISSED blocks
    if missed:
        print("  MISSED detail (ORA in broader window):")
        for fid in sorted(missed)[:5]:
            cursor.execute("""
                SELECT DISTINCT t.ORA, DATEPART(dw, tp.Date) as dw, t.NEWTYPE
                FROM trafficPalinse tp
                JOIN TPALINSE t ON t.ID_TPALINSE = tp.id_tpalinse
                WHERE tp.id_fascia = ? AND tp.Cod_User = ?
                  AND tp.Date >= ? AND tp.Date <= ?
                  AND t.ORA >= ? AND t.ORA < ?
                ORDER BY t.ORA
            """, fid, cod_user, d_from, d_to, lb - FPH, ub + FPH)
            rows = cursor.fetchall()[:6]
            print(f"    id_fascia={fid}: {[(r[0], ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][r[1]-1], r[2]) for r in rows]}")

cursor.close()
conn.close()
