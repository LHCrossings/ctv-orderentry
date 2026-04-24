"""
Test: MIN(trafficPalinse.offset) per (id_fascia, Date) in [ORA_INI-9, ORA_FIN-9).
No TPALINSE join — works for both PGM-header and COM-only blocks.
Run from Windows: py scripts/discover_block_refresh17.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from browser_automation.etere_direct_client import connect

FRAMES = 29.97
FPH = round(FRAMES * 3600)   # 107892 frames per hour
PREROLL = 9                  # frames before the hour boundary

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

print("Testing: MIN(trafficPalinse.offset) per (id_fascia, Date) in [ORA_INI-9, ORA_FIN-9)")
print(f"  PREROLL={PREROLL}  FPH={FPH}")
print("=" * 70)

for lid, day_pat, ora_ini_h, ora_fin_h, cod_user, d_from, d_to, is_point in LINES:
    ora_ini = round(ora_ini_h * 3600 * FRAMES)
    ora_fin = round(ora_fin_h * 3600 * FRAMES)
    dws = DAY_PATTERNS[day_pat]
    dw_ph = ",".join("?" * len(dws))

    lb = ora_ini - PREROLL
    if is_point:
        ub = ora_ini + FPH - PREROLL   # 1-hour window
    else:
        ub = ora_fin - PREROLL

    cursor.execute("SELECT ID_FASCE FROM CONTRATTIFASCE WHERE ID_CONTRATTIRIGHE = ?", lid)
    assigned = set(r[0] for r in cursor.fetchall())

    cursor.execute(f"""
        SELECT DISTINCT tp.id_fascia
        FROM trafficPalinse tp
        WHERE tp.Cod_User = ?
          AND tp.Date >= ? AND tp.Date <= ?
          AND DATEPART(dw, tp.Date) IN ({dw_ph})
        GROUP BY tp.id_fascia, tp.Date
        HAVING MIN(tp.offset) >= ? AND MIN(tp.offset) < ?
    """, cod_user, d_from, d_to, *dws, lb, ub)
    predicted = set(r[0] for r in cursor.fetchall())

    missed = assigned - predicted
    extra  = predicted - assigned
    status = "PERFECT" if not missed and not extra else "MISMATCH"
    label = f"{'POINT' if is_point else 'RANGE'} {ora_ini_h:.1f}h–{ora_fin_h:.1f}h [{day_pat}]"
    print(f"\nLine {lid} {label}  [{status}]")
    print(f"  lb={lb} ({lb/FRAMES/3600:.4f}h)  ub={ub} ({ub/FRAMES/3600:.4f}h)")
    print(f"  Assigned:  {sorted(assigned)}")
    print(f"  Predicted: {sorted(predicted)}")
    if missed:
        print(f"  MISSED:    {sorted(missed)}")
    if extra:
        print(f"  EXTRA:     {sorted(extra)}")
        # Show MIN(offset) for extras to understand why they were included
        for fid in sorted(extra)[:6]:
            cursor.execute(f"""
                SELECT tp.Date, DATEPART(dw,tp.Date) as dw, MIN(tp.offset) as min_off
                FROM trafficPalinse tp
                WHERE tp.id_fascia = ? AND tp.Cod_User = ?
                  AND tp.Date >= ? AND tp.Date <= ?
                  AND DATEPART(dw, tp.Date) IN ({dw_ph})
                GROUP BY tp.Date, DATEPART(dw,tp.Date)
                HAVING MIN(tp.offset) >= ? AND MIN(tp.offset) < ?
            """, fid, cod_user, d_from, d_to, *dws, lb, ub)
            rows = cursor.fetchall()
            dows = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"]
            print(f"    extra {fid}: {[(str(r[0])[:10], dows[r[1]-1], f'{r[2]/FRAMES/3600:.3f}h') for r in rows[:4]]}")

cursor.close()
conn.close()
