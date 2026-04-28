"""
Clear and re-assign blocks for every line in a contract using the
DLL-confirmed Traffic_Calendar SQL method (no Etere login required).

Usage:
    uv run python scripts/refresh_blocks_contract.py 2611
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from browser_automation.etere_direct_client import connect


def main():
    args = sys.argv[1:]
    positional = [a for a in args if not a.startswith("--")]
    contract_id = int(positional[0]) if positional else 2611

    from browser_automation.etere_direct_client import EtereDirectClient

    with connect() as conn:
        client = EtereDirectClient(conn, autocommit=False)

        line_ids = client.get_all_line_ids(contract_id)
        print(f"[INFO] Contract {contract_id}: {len(line_ids)} line(s) found")

        total_blocks = 0
        for line_id in line_ids:
            print(f"[INFO] Processing line {line_id} ...")
            n = client.assign_blocks_for_existing_line(line_id)
            if n >= 0:
                total_blocks += n

        conn.commit()
        print(f"\n[DONE] {len(line_ids)} line(s) refreshed, {total_blocks} block(s) assigned total.")


if __name__ == "__main__":
    main()
