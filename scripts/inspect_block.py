"""
Inspect a specific block ID across all markets in trafficPalinse.

Usage:
    python scripts/inspect_block.py 11205
    python scripts/inspect_block.py 11205 13172 9397
"""

import sys
from pathlib import Path

_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root))

from browser_automation.etere_direct_client import connect as db_connect, MARKET_USER_IDS

USER_ID_TO_MARKET = {v: k for k, v in MARKET_USER_IDS.items()}

block_ids = [int(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else [11205]

with db_connect() as conn:
    cursor = conn.cursor()
    placeholders = ",".join("?" * len(block_ids))
    cursor.execute(f"""
        SELECT tp.id_fascia, tp.Cod_User, tp.Date, tp.offset,
               tb.Name
        FROM   trafficPalinse tp
        LEFT JOIN Traffic_Block tb ON tb.ID_TrafficBlock = tp.id_fascia
        WHERE  tp.id_fascia IN ({placeholders})
        ORDER BY tp.id_fascia, tp.Cod_User, tp.Date
    """, block_ids)
    rows = cursor.fetchall()

if not rows:
    print("No rows found.")
    sys.exit(0)

current = None
for block_id, cod_user, date, offset, name in rows:
    market = USER_ID_TO_MARKET.get(cod_user, f"?{cod_user}")
    if block_id != current:
        current = block_id
        print(f"\nBlock {block_id}: {name}")
        print(f"  {'Market':<6} {'Date':<14} {'Offset':>10}")
        print(f"  {'-'*35}")
    print(f"  {market:<6} {str(date):<14} {offset:>10}")
