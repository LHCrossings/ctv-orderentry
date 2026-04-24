"""
Test the MAX(offset) >= ORA_INI rule and check TPALINSE.ORA for known fascia values.
Run from Windows: py scripts/discover_block_refresh10.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from browser_automation.etere_direct_client import connect

FRAMES = 29.97

conn = connect()
cursor = conn.cursor()

# ── Test rule: include id_fascia if MAX(offset) >= ORA_INI AND MIN(offset) < ORA_FIN ──
# Versus actual CONTRATTIFASCE for contract 2583 lines 73173-73179
LINES = [
    (73173, 20*3600*FRAMES, 21*3600*FRAMES, 5, "2026-01-05", "2026-01-11"),  # 20h-21h SEA
    (73175, 21*3600*FRAMES, 22*3600*FRAMES, 5, "2026-01-05", "2026-01-11"),  # 21h-22h SEA
    (73177, 22*3600*FRAMES, 22*3600*FRAMES, 5, "2026-03-16", "2026-03-22"),  # 22h-22h SEA
    (73178, 22*3600*FRAMES, 23.5*3600*FRAMES, 5, "2026-03-16", "2026-03-29"),# 22h-23.5h SEA
]

for lid, ora_ini, ora_fin, cod_user, d_from, d_to in LINES:
    ora_ini_r = round(ora_ini)
    ora_fin_r = round(ora_fin)

    # What's assigned
    cursor.execute("SELECT ID_FASCE FROM CONTRATTIFASCE WHERE ID_CONTRATTIRIGHE = ?", lid)
    assigned = set(r[0] for r in cursor.fetchall())

    # Rule: MAX(offset) >= ORA_INI AND MIN(offset) <= ORA_FIN (range overlap)
    cursor.execute("""
        SELECT id_fascia, MIN(offset) as min_off, MAX(offset) as max_off
        FROM trafficPalinse
        WHERE Cod_User = ?
          AND Date >= ? AND Date <= ?
        GROUP BY id_fascia
        HAVING MAX(offset) >= ? AND MIN(offset) <= ?
    """, cod_user, d_from, d_to, ora_ini_r, ora_fin_r)
    predicted = {r[0]: (r[1], r[2]) for r in cursor.fetchall()}

    correct   = assigned & set(predicted.keys())
    missed    = assigned - set(predicted.keys())
    extra     = set(predicted.keys()) - assigned

    print(f"\nLine {lid} ({ora_ini_r/FRAMES/3600:.1f}h–{ora_fin_r/FRAMES/3600:.1f}h) {d_from}–{d_to}")
    print(f"  Assigned:  {sorted(assigned)}")
    print(f"  Predicted: {sorted(predicted.keys())}")
    print(f"  Correct:   {sorted(correct)}")
    print(f"  MISSED:    {sorted(missed)}")
    print(f"  EXTRA:     {sorted(extra)}")
    if extra:
        for fid in sorted(extra)[:5]:
            mn, mx = predicted[fid]
            print(f"    extra id_fascia={fid}: offset {mn/FRAMES/3600:.3f}h–{mx/FRAMES/3600:.3f}h")

# ── Check TPALINSE.ORA for id_fascia 9940 vs 11229 ─────────────────────────────
print("\n" + "=" * 60)
print("TPALINSE.ORA for id_fascia 9940 vs 11229 (line 73177 date range)")
print("=" * 60)
for fid in [9940, 11229]:
    cursor.execute("""
        SELECT DISTINCT tp2.ORA
        FROM trafficPalinse tp
        JOIN TPALINSE tp2 ON tp2.ID_TPALINSE = tp.id_tpalinse
        WHERE tp.id_fascia = ?
          AND tp.Date >= '2026-03-16' AND tp.Date <= '2026-03-22'
          AND tp.Cod_User = 5
        ORDER BY tp2.ORA
    """, fid)
    oras = [r[0]/FRAMES/3600 for r in cursor.fetchall()]
    print(f"  id_fascia={fid}: TPALINSE.ORA values = {[f'{h:.3f}h' for h in oras[:10]]}")

cursor.close()
conn.close()
