"""
Compare ALL fields of TPALINSE and trafficPalinse for CORRECT vs EXTRA id_fascia values.
Focus on id_palinsesto and any other distinguishing columns.
Run from Windows: py scripts/discover_block_refresh13.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from browser_automation.etere_direct_client import connect

FRAMES = 29.97

conn = connect()
cursor = conn.cursor()

# ── 0. What columns does TPALINSE have? ───────────────────────────────────────
print("=" * 60)
print("TPALINSE columns")
print("=" * 60)
cursor.execute("SELECT TOP 1 * FROM TPALINSE")
print("  Cols:", [d[0] for d in cursor.description])

# ── 1. TPALINSE rows for 9923 (CORRECT, 20h-21h, Jan) vs 9940 (EXTRA) ────────
print("\n" + "=" * 60)
print("TPALINSE detail: 9923 (CORRECT) vs 9940 (EXTRA) — Jan 5-11")
print("=" * 60)
for fid, label in [(9923, "CORRECT"), (9940, "EXTRA")]:
    cursor.execute("""
        SELECT DISTINCT t.*
        FROM trafficPalinse tp
        JOIN TPALINSE t ON t.ID_TPALINSE = tp.id_tpalinse
        WHERE tp.id_fascia = ?
          AND tp.Cod_User = 5
          AND tp.Date >= '2026-01-05' AND tp.Date <= '2026-01-11'
        ORDER BY t.ORA
    """, fid)
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    print(f"\n  id_fascia={fid} [{label}]: {len(rows)} distinct TPALINSE rows")
    for r in rows[:5]:
        d = dict(zip(cols, r))
        ora_h = (d.get('ORA') or 0) / FRAMES / 3600
        print(f"    ORA={ora_h:.3f}h  {d}")

# ── 2. trafficPalinse fields for 9923 vs 9940 in Jan ─────────────────────────
print("\n" + "=" * 60)
print("trafficPalinse detail: id_palinsesto for 9923 vs 9940 — Jan 5-11")
print("=" * 60)
for fid, label in [(9923, "CORRECT"), (9940, "EXTRA")]:
    cursor.execute("""
        SELECT DISTINCT id_palinsesto, id_tpalinse, offset, EVENTTYPE
        FROM trafficPalinse
        WHERE id_fascia = ?
          AND Cod_User = 5
          AND Date >= '2026-01-05' AND Date <= '2026-01-11'
        ORDER BY offset
    """, fid)
    rows = cursor.fetchall()
    print(f"  id_fascia={fid} [{label}]: distinct (palinsesto, tpalinse, offset, eventtype)")
    for r in rows[:5]:
        off_h = r[2] / FRAMES / 3600
        print(f"    id_palinsesto={r[0]}  id_tpalinse={r[1]}  offset={off_h:.3f}h  EVENTTYPE={r[3]}")

# ── 3. What table does id_palinsesto reference? ───────────────────────────────
print("\n" + "=" * 60)
print("Tables containing 'palinsesto' in name")
print("=" * 60)
cursor.execute("""
    SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_NAME LIKE '%palinsest%' OR TABLE_NAME LIKE '%PALINSEST%'
    ORDER BY TABLE_NAME
""")
for r in cursor.fetchall():
    print(f"  {r[0]}")

# ── 4. Look up id_palinsesto values for 9923 vs 9940 in PALINSESTI (if exists)
print("\n" + "=" * 60)
print("Distinct id_palinsesto for key id_fascia in Jan (Cod_User=5)")
print("=" * 60)
for fid, label in [(9923, "CORRECT-73173"), (9940, "EXTRA-73173/73175"), (11229, "CORRECT-73175")]:
    cursor.execute("""
        SELECT DISTINCT id_palinsesto
        FROM trafficPalinse
        WHERE id_fascia = ? AND Cod_User = 5
          AND Date >= '2026-01-05' AND Date <= '2026-01-11'
    """, fid)
    pals = [r[0] for r in cursor.fetchall()]
    print(f"  id_fascia={fid} [{label}]: id_palinsesto = {pals}")

# ── 5. For lines 73173 and 73175: what id_palinsesto values do ALL assigned blocks share?
print("\n" + "=" * 60)
print("id_palinsesto for ALL ASSIGNED blocks of lines 73173 and 73175")
print("=" * 60)
for lid, d_from, d_to in [(73173, "2026-01-05", "2026-01-11"), (73175, "2026-01-05", "2026-01-11")]:
    cursor.execute("SELECT ID_FASCE FROM CONTRATTIFASCE WHERE ID_CONTRATTIRIGHE = ?", lid)
    assigned = [r[0] for r in cursor.fetchall()]
    print(f"\n  Line {lid} assigned: {assigned}")
    for fid in assigned:
        cursor.execute("""
            SELECT DISTINCT id_palinsesto
            FROM trafficPalinse
            WHERE id_fascia = ? AND Cod_User = 5
              AND Date >= ? AND Date <= ?
        """, fid, d_from, d_to)
        pals = [r[0] for r in cursor.fetchall()]
        print(f"    id_fascia={fid}: id_palinsesto = {pals}")

# ── 6. ORA range for 9940 in Jan vs March ─────────────────────────────────────
print("\n" + "=" * 60)
print("TPALINSE.ORA for id_fascia=9940: Jan vs March (Cod_User=5)")
print("=" * 60)
for d_from, d_to, label in [
    ("2026-01-05", "2026-01-11", "Jan (73173/73175 range)"),
    ("2026-03-16", "2026-03-22", "Mar (73177 range)"),
]:
    cursor.execute("""
        SELECT DISTINCT t.ORA
        FROM trafficPalinse tp
        JOIN TPALINSE t ON t.ID_TPALINSE = tp.id_tpalinse
        WHERE tp.id_fascia = 9940 AND tp.Cod_User = 5
          AND tp.Date >= ? AND tp.Date <= ?
        ORDER BY t.ORA
    """, d_from, d_to)
    oras = [f"{r[0]/FRAMES/3600:.3f}h" for r in cursor.fetchall()]
    print(f"  {label}: ORA = {oras}")

cursor.close()
conn.close()
