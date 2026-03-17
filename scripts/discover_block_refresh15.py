"""
Test: NEWTYPE='PGM' + (ORA_INI-1, ORA_FIN-1) bounds + day-of-week filter.
Hypothesis: Etere stores program starts as (hour*107892 - 1); adjusting bounds
and filtering on PGM-only gives perfect block assignment.
Run from Windows: py scripts/discover_block_refresh15.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from browser_automation.etere_direct_client import connect

FRAMES = 29.97
FPH = round(FRAMES * 3600)  # 107892 frames per hour

# SQL Server DATEPART(dw): 1=Sun,2=Mon,3=Tue,4=Wed,5=Thu,6=Fri,7=Sat
DAY_PATTERNS = {
    "M-F":  {2, 3, 4, 5, 6},
    "M-Sa": {2, 3, 4, 5, 6, 7},
    "M-Su": {1, 2, 3, 4, 5, 6, 7},
}

conn = connect()
cursor = conn.cursor()

# ── Show exact stored ORA integers for key id_fascia PGM records ─────────────
print("=" * 70)
print("Exact stored ORA (integer) for PGM records — Jan 5-11, Cod_User=5")
print(f"  FPH={FPH}  20h={20*FPH}  21h={21*FPH}  22h={22*FPH}")
print("=" * 70)
for fid, label in [(9923, "CORRECT-73173"), (9940, "EXTRA-73173/73175"), (11229, "CORRECT-73175"), (13432, "CORRECT-73177"), (9942, "EXTRA-73177"), (11230, "EXTRA-73177")]:
    cursor.execute("""
        SELECT DISTINCT t.ORA, t.NEWTYPE, DATEPART(dw, tp.Date) as dw
        FROM trafficPalinse tp
        JOIN TPALINSE t ON t.ID_TPALINSE = tp.id_tpalinse
        WHERE tp.id_fascia = ? AND tp.Cod_User = 5
          AND tp.Date >= '2026-01-05' AND tp.Date <= '2026-03-22'
          AND t.NEWTYPE = 'PGM'
        ORDER BY t.ORA
    """, fid)
    rows = cursor.fetchall()[:6]
    print(f"\n  id_fascia={fid} [{label}]: PGM ORA values (first 6)")
    for r in rows:
        ora_h = r[0] / FRAMES / 3600
        dw_name = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"][r[2]-1]
        print(f"    ORA={r[0]}  ({ora_h:.4f}h)  dw={dw_name}  mod_FPH={r[0] % FPH}")

# ── Main test: NEWTYPE=PGM + shifted bounds + day-of-week ────────────────────
LINES = [
    (73173, "M-F",  20.0, 21.0, 5, "2026-01-05", "2026-01-11", False),
    (73175, "M-F",  21.0, 22.0, 5, "2026-01-05", "2026-01-11", False),
    (73177, "M-Su", 22.0, 22.0, 5, "2026-03-16", "2026-03-22", True),
    (73178, "M-Su", 22.0, 23.5, 5, "2026-03-16", "2026-03-29", False),
]

print("\n" + "=" * 70)
print("Testing: NEWTYPE='PGM' + ORA in [ORA_INI-1, ORA_FIN-1) + DW filter")
print("=" * 70)

for lid, day_pat, ora_ini_h, ora_fin_h, cod_user, d_from, d_to, is_point in LINES:
    ora_ini = round(ora_ini_h * 3600 * FRAMES)
    ora_fin = round(ora_fin_h * 3600 * FRAMES)
    dws = DAY_PATTERNS[day_pat]
    dw_ph = ",".join("?" * len(dws))

    lb = ora_ini - 1      # lower bound (inclusive)
    if is_point:
        ub = ora_ini + round(2 * 3600 * FRAMES) - 1   # upper bound (exclusive)
    else:
        ub = ora_fin - 1  # upper bound (exclusive)

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
    print(f"  Assigned:  {sorted(assigned)}")
    print(f"  Predicted: {sorted(predicted)}")
    if missed: print(f"  MISSED:    {sorted(missed)}")
    if extra:  print(f"  EXTRA:     {sorted(extra)}")

cursor.close()
conn.close()
