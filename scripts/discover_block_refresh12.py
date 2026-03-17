"""
Check trafficPalinse.EVENTTYPE for assigned vs extra id_fascia values.
Run from Windows: py scripts/discover_block_refresh12.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from browser_automation.etere_direct_client import connect

FRAMES = 29.97

conn = connect()
cursor = conn.cursor()

# ── Line 73173 (20h-21h SEA, Jan 5-11): assigned=9923, extra=[9940, 11229, 14274]
print("=" * 60)
print("EVENTTYPE for line 73173 (20h-21h, Jan 5-11, Cod_User=5)")
print("=" * 60)
for fid, label in [(9923, "CORRECT"), (9940, "EXTRA"), (11229, "EXTRA"), (14274, "EXTRA")]:
    cursor.execute("""
        SELECT EVENTTYPE, COUNT(*) as cnt
        FROM trafficPalinse
        WHERE id_fascia = ? AND Cod_User = 5
          AND Date >= '2026-01-05' AND Date <= '2026-01-11'
        GROUP BY EVENTTYPE
        ORDER BY EVENTTYPE
    """, fid)
    rows = cursor.fetchall()
    print(f"  id_fascia={fid} [{label}]: EVENTTYPE = {[(r[0], r[1]) for r in rows]}")

# ── Line 73175 (21h-22h SEA, Jan 5-11): assigned=11229, extra=[9940, 13432, 14274, ...]
print("\n" + "=" * 60)
print("EVENTTYPE for line 73175 (21h-22h, Jan 5-11, Cod_User=5)")
print("=" * 60)
for fid, label in [(11229, "CORRECT"), (9940, "EXTRA"), (13432, "EXTRA"), (14274, "EXTRA")]:
    cursor.execute("""
        SELECT EVENTTYPE, COUNT(*) as cnt
        FROM trafficPalinse
        WHERE id_fascia = ? AND Cod_User = 5
          AND Date >= '2026-01-05' AND Date <= '2026-01-11'
        GROUP BY EVENTTYPE
        ORDER BY EVENTTYPE
    """, fid)
    rows = cursor.fetchall()
    print(f"  id_fascia={fid} [{label}]: EVENTTYPE = {[(r[0], r[1]) for r in rows]}")

# ── Line 73177 (22h-22h SEA, Mar 16-22): assigned=[9940,13432,14317,15337,15349], extra=[9942,11230]
print("\n" + "=" * 60)
print("EVENTTYPE for line 73177 (22h pt, Mar 16-22, Cod_User=5)")
print("=" * 60)
for fid, label in [(9940,"CORRECT"),(13432,"CORRECT"),(15349,"CORRECT"),(9942,"EXTRA"),(11230,"EXTRA")]:
    cursor.execute("""
        SELECT EVENTTYPE, COUNT(*) as cnt
        FROM trafficPalinse
        WHERE id_fascia = ? AND Cod_User = 5
          AND Date >= '2026-03-16' AND Date <= '2026-03-22'
        GROUP BY EVENTTYPE
        ORDER BY EVENTTYPE
    """, fid)
    rows = cursor.fetchall()
    print(f"  id_fascia={fid} [{label}]: EVENTTYPE = {[(r[0], r[1]) for r in rows]}")

# ── All distinct EVENTTYPE values in trafficPalinse
print("\n" + "=" * 60)
print("All distinct EVENTTYPE values in trafficPalinse")
print("=" * 60)
cursor.execute("SELECT DISTINCT EVENTTYPE, COUNT(*) as cnt FROM trafficPalinse GROUP BY EVENTTYPE ORDER BY EVENTTYPE")
for r in cursor.fetchall():
    print(f"  EVENTTYPE={r[0]}  count={r[1]}")

cursor.close()
conn.close()
