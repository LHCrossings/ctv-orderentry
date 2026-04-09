"""
Clear and re-assign blocks for every line in a contract.

Logs into Etere headlessly (no browser needed) using credentials.env,
then refreshes blocks via Etere's own HTTP API.

Usage:
    uv run python scripts/refresh_blocks_contract.py 2611
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from browser_automation.etere_direct_client import EtereDirectClient, connect, etere_web_login, etere_web_logout


def main():
    contract_id = int(sys.argv[1]) if len(sys.argv) > 1 else 2611

    print(f"[INFO] Logging into Etere web UI ...")
    session = etere_web_login()

    try:
        with connect() as conn:
            client = EtereDirectClient(conn, autocommit=False)
            client.set_http_session(session)

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
    finally:
        etere_web_logout(session)


if __name__ == "__main__":
    main()
