"""
Call web_getPriceListBlocks to see what it returns, then compare to CONTRATTIFASCE.
Run from Windows: py scripts/discover_block_refresh8.py
"""
import sys, math
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent.parent))
from browser_automation.etere_direct_client import connect, NEWTYPE_PAID

FRAMES = 29.97

conn = connect()
cursor = conn.cursor()

# Use a known SEA Lexus contract line (73173): M-F 8PM-9PM
# ORA_INIZIO=2157840 → 20:00, ORA_FINE=2265732 → 21:00, Cod_User=5 (SEA)
LINE_ID   = 73173
COD_USER  = 5
ORA_INI   = 2157840
ORA_FIN   = 2265732
DATE_FROM = datetime(2026, 1, 5)
DATE_TO   = datetime(2026, 1, 11)

# ── 1. What's in CONTRATTIFASCE for this line? ────────────────────────────────
print("=" * 60)
print(f"CONTRATTIFASCE for line {LINE_ID}")
print("=" * 60)
cursor.execute("SELECT ID_FASCE FROM CONTRATTIFASCE WHERE ID_CONTRATTIRIGHE = ?", LINE_ID)
existing = [r[0] for r in cursor.fetchall()]
print(f"  Existing id_fascia: {existing}")

# ── 2. Call web_getPriceListBlocks (page 1, 100 items) ───────────────────────
print("\n" + "=" * 60)
print("web_getPriceListBlocks results")
print("=" * 60)
cursor.execute("""
    EXEC web_getPriceListBlocks
        @fromdate      = ?,
        @todate        = ?,
        @page          = 1,
        @nItemPerPage  = 100,
        @coduser       = ?,
        @newtype       = ?,
        @dom           = 0,
        @lun           = 1,
        @mar           = 1,
        @mer           = 1,
        @gio           = 1,
        @ven           = 1,
        @sab           = 0
""", DATE_FROM, DATE_TO, COD_USER, NEWTYPE_PAID)
cols = [d[0] for d in cursor.description]
print(f"  Cols: {cols}")
rows = cursor.fetchall()
print(f"  Total rows: {len(rows)}")
for row in rows[:20]:
    d = dict(zip(cols, row))
    print(" ", d)

# ── 3. Which of those rows match our time window? ────────────────────────────
print("\n" + "=" * 60)
print(f"Filtering by time window {ORA_INI}–{ORA_FIN} ({ORA_INI/FRAMES/3600:.2f}h–{ORA_FIN/FRAMES/3600:.2f}h)")
print("=" * 60)
for row in rows:
    d = dict(zip(cols, row))
    # Find offset/time columns
    for time_col in ['offset', 'ora', 'ORA', 'ORA_INI', 'starttime', 'id_fascia']:
        if time_col in d:
            val = d[time_col]
            if isinstance(val, (int, float)) and ORA_INI <= val <= ORA_FIN:
                print(f"  MATCH on {time_col}={val}: {d}")
                break

cursor.close()
conn.close()
