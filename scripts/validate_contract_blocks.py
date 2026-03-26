"""
Validate that each contract line's assigned blocks belong to the correct market.

After block refresh, duplicated lines sometimes retain blocks from the source
market (CMP) instead of picking up the destination market's shows. This script
detects those mismatches.

Usage (from project root, Windows):
    python scripts/validate_contract_blocks.py 2646
"""

import sys
from pathlib import Path

_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "browser_automation"))

CONTRACT_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 2646

from browser_automation.etere_direct_client import connect as db_connect, MARKET_USER_IDS

# Reverse map: user_id → market code
USER_ID_TO_MARKET = {v: k for k, v in MARKET_USER_IDS.items()}

QUERY = """
    SELECT
        r.ID_CONTRATTIRIGHE   AS line_id,
        r.COD_USER            AS line_market_id,
        r.DESCRIZIONE         AS line_desc,
        r.DATA_INIZIO         AS date_from,
        r.DATA_FINE           AS date_to,
        f.ID_FASCE            AS block_id,
        tp.Cod_User           AS block_market_id,
        MIN(tp.offset)        AS block_offset
    FROM   CONTRATTIRIGHE r
    JOIN   CONTRATTIFASCE f  ON f.ID_CONTRATTIRIGHE = r.ID_CONTRATTIRIGHE
    JOIN   trafficPalinse tp ON tp.id_fascia = f.ID_FASCE
    WHERE  r.ID_CONTRATTITESTATA = ?
    GROUP BY
        r.ID_CONTRATTIRIGHE, r.COD_USER, r.DESCRIZIONE,
        r.DATA_INIZIO, r.DATA_FINE, f.ID_FASCE, tp.Cod_User
    ORDER BY r.ID_CONTRATTIRIGHE, f.ID_FASCE
"""

with db_connect() as conn:
    cursor = conn.cursor()
    cursor.execute(QUERY, [CONTRACT_ID])
    rows = cursor.fetchall()

if not rows:
    print(f"No block assignments found for contract {CONTRACT_ID}.")
    sys.exit(0)

# Group by line
from collections import defaultdict
lines: dict[int, dict] = {}
for row in rows:
    line_id, line_mkt_id, desc, df, dt, block_id, block_mkt_id, offset = row
    if line_id not in lines:
        lines[line_id] = {
            'desc': desc,
            'line_market': USER_ID_TO_MARKET.get(line_mkt_id, f"?{line_mkt_id}"),
            'date_from': df,
            'date_to': dt,
            'blocks': [],
        }
    lines[line_id]['blocks'].append({
        'block_id': block_id,
        'block_market': USER_ID_TO_MARKET.get(block_mkt_id, f"?{block_mkt_id}"),
        'ok': block_mkt_id == line_mkt_id,
    })

# Report
total_lines = len(lines)
bad_lines   = 0
total_wrong = 0

print(f"\nContract {CONTRACT_ID} — {total_lines} lines with block assignments\n")
print(f"{'Line ID':<10} {'Market':<6} {'Description':<40} {'Blocks':>6} {'Wrong':>6}")
print("-" * 75)

for line_id, info in lines.items():
    wrong = [b for b in info['blocks'] if not b['ok']]
    total_wrong += len(wrong)
    if wrong:
        bad_lines += 1

    status = f"  ✗ {len(wrong)} wrong-market" if wrong else ""
    print(f"{line_id:<10} {info['line_market']:<6} {info['desc'][:40]:<40} "
          f"{len(info['blocks']):>6} {len(wrong):>6}{status}")

    if wrong:
        for b in wrong:
            print(f"           ↳ block {b['block_id']} is {b['block_market']} (expected {info['line_market']})")

print("-" * 75)
if bad_lines:
    print(f"\n✗ {bad_lines}/{total_lines} lines have wrong-market blocks ({total_wrong} total wrong blocks)")
    print("  Run block refresh to fix.")
else:
    print(f"\n✓ All {total_lines} lines have correct-market blocks")
