"""
Query CONTRATTIRIGHE for one or more contract numbers and print all line details.

Usage:
    uv run python scripts/check_contract_lines.py 2630 2631 2632
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from browser_automation.etere_direct_client import connect

contract_ids = [int(a) for a in sys.argv[1:]]
if not contract_ids:
    print("Usage: uv run python scripts/check_contract_lines.py <contract_id> [...]")
    sys.exit(1)

conn = connect()
cursor = conn.cursor()

for cid in contract_ids:
    cursor.execute("""
        SELECT
            ID_CONTRATTIRIGHE,
            DATA_INIZIO, DATA_FINE,
            N_PUNTATE, PASSAGGI_SETTIMANALI, PASSAGGI_GIORNALIERI,
            IMPORTO, DESCRIZIONE,
            ORA_INIZIO, ORA_FINE
        FROM CONTRATTIRIGHE
        WHERE ID_CONTRATTITESTATA = ?
        ORDER BY ID_CONTRATTIRIGHE
    """, [cid])
    rows = cursor.fetchall()

    print(f"\n{'='*70}")
    print(f"Contract {cid}  —  {len(rows)} line(s)")
    print(f"{'='*70}")
    if not rows:
        print("  (no lines found)")
        continue

    total_n_puntate = sum(r[3] or 0 for r in rows)
    total_passaggi  = sum(r[4] or 0 for r in rows)

    for i, r in enumerate(rows, 1):
        line_id, start, end, n_puntate, pw, pd, rate, desc, ora_in, ora_fin = r
        print(
            f"  Line {i} (ID {line_id}): {start} – {end}"
            f"  N_PUNTATE={n_puntate}  PASSAGGI_SETT={pw}  PASSAGGI_GIORN={pd}"
            f"  ${rate or 0:.2f}  [{desc or ''}]"
        )

    print(f"  Totals: N_PUNTATE={total_n_puntate}  PASSAGGI_SETTIMANALI_sum={total_passaggi}")

conn.close()
