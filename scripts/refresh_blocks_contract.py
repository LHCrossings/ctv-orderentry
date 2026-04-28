"""
Clear and re-assign blocks for every line in a contract.

Usage:
    uv run python scripts/refresh_blocks_contract.py 2611        # HTTP (default)
    uv run python scripts/refresh_blocks_contract.py 2611 --sql  # pure SQL, no login
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from browser_automation.etere_direct_client import (
    EtereDirectClient,
    connect,
    etere_web_login,
    etere_web_logout,
)


def main():
    args = sys.argv[1:]
    use_sql = "--sql" in args
    positional = [a for a in args if not a.startswith("--")]
    contract_id = int(positional[0]) if positional else 2611

    if use_sql:
        print("[INFO] SQL mode — no Etere login required")
        session = None
    else:
        print("[INFO] Logging into Etere web UI ...")
        session = etere_web_login()

    try:
        with connect() as conn:
            client = EtereDirectClient(conn, autocommit=False)
            if session:
                client.set_http_session(session)

            line_ids = client.get_all_line_ids(contract_id)
            print(f"[INFO] Contract {contract_id}: {len(line_ids)} line(s) found")

            total_blocks = 0
            for line_id in line_ids:
                print(f"[INFO] Processing line {line_id} ...")
                n = client.assign_blocks_for_existing_line(line_id, use_sql=use_sql)
                if n >= 0:
                    total_blocks += n

            conn.commit()
            print(f"\n[DONE] {len(line_ids)} line(s) refreshed, {total_blocks} block(s) assigned total.")
    finally:
        if session:
            etere_web_logout(session)


if __name__ == "__main__":
    main()
