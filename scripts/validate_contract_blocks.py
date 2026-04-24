"""
Validate that each contract line's assigned blocks belong to the correct market.

A block is considered "wrong" only if trafficPalinse has ZERO entries for that
block with the line's market user ID within the line's flight date range.
Blocks that exist in multiple markets are NOT flagged — that's normal.

Usage (from project root, Windows):
    python scripts/validate_contract_blocks.py 2646
"""

import sys
from pathlib import Path

_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root))

CONTRACT_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 2646

from browser_automation.etere_direct_client import MARKET_USER_IDS
from browser_automation.etere_direct_client import connect as db_connect

USER_ID_TO_MARKET = {v: k for k, v in MARKET_USER_IDS.items()}

# Fetch all lines and their assigned block IDs
LINES_QUERY = """
    SELECT
        r.ID_CONTRATTIRIGHE   AS line_id,
        r.COD_USER            AS line_market_id,
        r.DESCRIZIONE         AS line_desc,
        r.DATA_INIZIO         AS date_from,
        r.DATA_FINE           AS date_to,
        f.ID_FASCE            AS block_id
    FROM   CONTRATTIRIGHE r
    JOIN   CONTRATTIFASCE f ON f.ID_CONTRATTIRIGHE = r.ID_CONTRATTIRIGHE
    WHERE  r.ID_CONTRATTITESTATA = ?
    ORDER BY r.ID_CONTRATTIRIGHE, f.ID_FASCE
"""

# Check whether a specific block has ANY entry in the expected market within the date range
BLOCK_CHECK_QUERY = """
    SELECT COUNT(*)
    FROM   trafficPalinse
    WHERE  id_fascia = ?
      AND  Cod_User  = ?
      AND  Date BETWEEN ? AND ?
"""

with db_connect() as conn:
    cursor = conn.cursor()
    cursor.execute(LINES_QUERY, [CONTRACT_ID])
    rows = cursor.fetchall()

    if not rows:
        print(f"No block assignments found for contract {CONTRACT_ID}.")
        sys.exit(0)

    # Group by line, then check each block
    lines: dict[int, dict] = {}
    for row in rows:
        line_id, line_mkt_id, desc, df, dt, block_id = row
        if line_id not in lines:
            lines[line_id] = {
                'desc': desc,
                'line_market': USER_ID_TO_MARKET.get(line_mkt_id, f"?{line_mkt_id}"),
                'line_market_id': line_mkt_id,
                'date_from': df,
                'date_to': dt,
                'blocks': [],
            }
        lines[line_id]['blocks'].append(block_id)

    # For each line, find blocks with no matching-market entries in the date range
    line_results = {}
    for line_id, info in lines.items():
        wrong_blocks = []
        for block_id in info['blocks']:
            cursor.execute(BLOCK_CHECK_QUERY, [
                block_id, info['line_market_id'],
                info['date_from'], info['date_to'],
            ])
            count = cursor.fetchone()[0]
            if count == 0:
                wrong_blocks.append(block_id)
        line_results[line_id] = wrong_blocks

total_lines = len(lines)
bad_lines   = sum(1 for w in line_results.values() if w)
total_wrong = sum(len(w) for w in line_results.values())

print(f"\nContract {CONTRACT_ID} — {total_lines} lines with block assignments\n")
print(f"{'Line ID':<10} {'Market':<6} {'Description':<40} {'Blocks':>6} {'Wrong':>6}")
print("-" * 75)

for line_id, info in lines.items():
    wrong = line_results[line_id]
    status = f"  ✗ {len(wrong)} no-match" if wrong else ""
    print(f"{line_id:<10} {info['line_market']:<6} {info['desc'][:40]:<40} "
          f"{len(info['blocks']):>6} {len(wrong):>6}{status}")
    for block_id in wrong:
        print(f"           ↳ block {block_id} has no {info['line_market']} entries in date range")

print("-" * 75)
if bad_lines:
    print(f"\n✗ {bad_lines}/{total_lines} lines have blocks with no matching-market schedule ({total_wrong} total)")
    print("  Run block refresh to fix.")
else:
    print(f"\n✓ All {total_lines} lines have correctly scheduled blocks")
