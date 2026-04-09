"""
Set separation intervals on all lines in a contract.

Separation values are in minutes. Etere stores them as SMPTE frame counts
(29.97 fps). All three intervals default to 0 if not supplied.

Usage:
    uv run python scripts/update_separation_contract.py <contract_id> \
        [--customer MINUTES] [--event MINUTES] [--order MINUTES]

Examples:
    uv run python scripts/update_separation_contract.py 2611 --customer 15
    uv run python scripts/update_separation_contract.py 2611 --customer 25 --event 0 --order 0
"""
import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from browser_automation.etere_direct_client import connect

FPS = 29.97


def minutes_to_frames(minutes: int) -> int:
    return round(minutes * 60 * FPS)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("contract_id", type=int)
    parser.add_argument("--customer", type=int, default=0, metavar="MINUTES")
    parser.add_argument("--event",    type=int, default=0, metavar="MINUTES")
    parser.add_argument("--order",    type=int, default=0, metavar="MINUTES")
    args = parser.parse_args()

    customer_frames = minutes_to_frames(args.customer)
    event_frames    = minutes_to_frames(args.event)
    order_frames    = minutes_to_frames(args.order)

    print(f"[INFO] Contract {args.contract_id}: setting separation "
          f"({args.customer}, {args.event}, {args.order}) min ...")

    with connect() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*) FROM CONTRATTIRIGHE
            WHERE ID_CONTRATTITESTATA = ?
        """, [args.contract_id])
        count = cursor.fetchone()[0]

        if not count:
            print(f"[WARN] No lines found for contract {args.contract_id}.")
            sys.exit(1)

        print(f"[INFO] {count} line(s) found.")

        cursor.execute("""
            UPDATE CONTRATTIRIGHE
            SET    Interv_Committente = ?,
                   INTERVALLO         = ?,
                   INTERV_CONTRATTO   = ?
            WHERE  ID_CONTRATTITESTATA = ?
        """, [customer_frames, event_frames, order_frames, args.contract_id])

        conn.commit()
        print(f"\n[DONE] {cursor.rowcount} line(s) updated — "
              f"separation ({args.customer}, {args.event}, {args.order}) min.")


if __name__ == "__main__":
    main()
