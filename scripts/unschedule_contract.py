"""
Unschedule all lines in a contract by calling Etere's Change Data endpoint.

This releases each line from its scheduled blocks so data can be modified.
After editing, use Block Refresh to re-assign blocks.

Usage:
    uv run python scripts/unschedule_contract.py 2611
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from browser_automation.etere_direct_client import (
    ETERE_WEB_URL,
    EtereDirectClient,
    connect,
    etere_web_login,
    etere_web_logout,
)


def main():
    contract_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
    if not contract_id:
        print("Usage: uv run python scripts/unschedule_contract.py <contract_id>")
        sys.exit(1)

    print("[INFO] Logging into Etere web UI ...")
    session = etere_web_login()

    try:
        with connect() as conn:
            client = EtereDirectClient(conn, autocommit=False)

            line_ids = client.get_all_line_ids(contract_id)
            print(f"[INFO] Contract {contract_id}: {len(line_ids)} line(s) found")

            ok = 0
            fail = 0
            for line_id in line_ids:
                resp = session.post(
                    f"{ETERE_WEB_URL}/sales/changedatacontractline",
                    json={"idContractLine": str(line_id)},
                    headers={"X-Requested-With": "XMLHttpRequest"},
                    timeout=15,
                )
                if resp.ok:
                    print(f"[INFO] Line {line_id}: unscheduled OK")
                    ok += 1
                else:
                    print(f"[WARN] Line {line_id}: HTTP {resp.status_code}")
                    fail += 1

        status = f"{ok} line(s) unscheduled"
        if fail:
            status += f", {fail} failed"
        print(f"\n[DONE] {status}.")
    finally:
        etere_web_logout(session)


if __name__ == "__main__":
    main()
