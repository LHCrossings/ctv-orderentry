"""
Test: TPALINSE.ORA time window + DATEPART(dw) day filter combined.
Hypothesis: this gives exactly the CONTRATTIFASCE assignments (no missed, no extra).
Run from Windows: py scripts/discover_block_refresh14.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from browser_automation.etere_direct_client import connect

FRAMES = 29.97

# SQL Server DATEPART(dw) with default DATEFIRST=7:
#   1=Sun, 2=Mon, 3=Tue, 4=Wed, 5=Thu, 6=Fri, 7=Sat
# Day-pattern -> set of dw values
DAY_PATTERNS = {
    "M-F":  {2, 3, 4, 5, 6},
    "M-Sa": {2, 3, 4, 5, 6, 7},
    "M-Su": {1, 2, 3, 4, 5, 6, 7},
    "Sa":   {7},
    "Su":   {1},
}

conn = connect()
cursor = conn.cursor()

LINES = [
    # (line_id, day_pattern, ora_ini_h, ora_fin_h, cod_user, date_from, date_to, point_in_time)
    (73173, "M-F",  20.0, 21.0, 5, "2026-01-05", "2026-01-11", False),
    (73175, "M-F",  21.0, 22.0, 5, "2026-01-05", "2026-01-11", False),
    (73177, "M-Su", 22.0, 22.0, 5, "2026-03-16", "2026-03-22", True),
    (73178, "M-Su", 22.0, 23.5, 5, "2026-03-16", "2026-03-29", False),
]

print("Testing: TPALINSE.ORA + DATEPART(dw) combined filter")
print("=" * 70)

for lid, day_pat, ora_ini_h, ora_fin_h, cod_user, d_from, d_to, is_point in LINES:
    ora_ini = round(ora_ini_h * 3600 * FRAMES)
    ora_fin = round(ora_fin_h * 3600 * FRAMES)
    dws = DAY_PATTERNS[day_pat]
    dw_ph = ",".join("?" * len(dws))

    # For point-in-time, use a 2h window after ORA_INI
    if is_point:
        ora_ub = ora_ini + round(2 * 3600 * FRAMES)
    else:
        ora_ub = ora_fin

    cursor.execute("SELECT ID_FASCE FROM CONTRATTIFASCE WHERE ID_CONTRATTIRIGHE = ?", lid)
    assigned = set(r[0] for r in cursor.fetchall())

    cursor.execute(f"""
        SELECT DISTINCT tp.id_fascia
        FROM trafficPalinse tp
        JOIN TPALINSE t ON t.ID_TPALINSE = tp.id_tpalinse
        WHERE tp.Cod_User = ?
          AND tp.Date >= ? AND tp.Date <= ?
          AND t.ORA >= ? AND t.ORA < ?
          AND DATEPART(dw, tp.Date) IN ({dw_ph})
    """, cod_user, d_from, d_to, ora_ini, ora_ub, *dws)
    predicted = set(r[0] for r in cursor.fetchall())

    missed = assigned - predicted
    extra  = predicted - assigned

    label = f"{'POINT' if is_point else 'RANGE'} {ora_ini_h:.1f}h–{ora_fin_h:.1f}h [{day_pat}] {d_from}–{d_to}"
    status = "PERFECT" if not missed and not extra else "MISMATCH"
    print(f"\nLine {lid} {label}  [{status}]")
    print(f"  Assigned:  {sorted(assigned)}")
    print(f"  Predicted: {sorted(predicted)}")
    if missed: print(f"  MISSED:    {sorted(missed)}")
    if extra:  print(f"  EXTRA:     {sorted(extra)}")

# ── Also verify: what DW does each id_fascia occur on for 9940/11229 in Jan? ──
print("\n" + "=" * 70)
print("Per-day breakdown: 9940 and 11229 in Jan 5-11 with TPALINSE.ORA 20-22h")
print("=" * 70)
ORA_20 = round(20 * 3600 * FRAMES)
ORA_22 = round(22 * 3600 * FRAMES)
for fid in [9923, 9940, 11229]:
    cursor.execute("""
        SELECT tp.Date, DATEPART(dw, tp.Date) as dw, t.ORA, t.NEWTYPE
        FROM trafficPalinse tp
        JOIN TPALINSE t ON t.ID_TPALINSE = tp.id_tpalinse
        WHERE tp.id_fascia = ? AND tp.Cod_User = 5
          AND tp.Date >= '2026-01-05' AND tp.Date <= '2026-01-11'
          AND t.ORA >= ? AND t.ORA < ?
        ORDER BY tp.Date, t.ORA
    """, fid, ORA_20, ORA_22)
    rows = cursor.fetchall()
    days_seen = {}
    for r in rows:
        d, dw, ora, nt = r
        ora_h = ora / FRAMES / 3600
        key = (str(d)[:10], dw)
        if key not in days_seen:
            days_seen[key] = (ora_h, nt)
    print(f"\n  id_fascia={fid}:")
    for (date, dw), (ora_h, nt) in sorted(days_seen.items()):
        dow = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"][dw-1] if dw<=7 else "?"
        print(f"    {date} ({dow}) ORA={ora_h:.3f}h NEWTYPE={nt}")

cursor.close()
conn.close()
