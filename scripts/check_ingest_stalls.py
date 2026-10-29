"""Ingest / Datamover stall check — is a file dropped into WIP actually moving?

Lists every open workflow step that has waited longer than the Datamover should need
(ingest step > 20 min, restore download > 45 min) and the instances looping in the
SQL Agent abort/restart cycle. Read-only; the same scan runs every few minutes in
Broadcast Health and turns the header dot amber (business_logic/services/ingest_watchdog.py).

    uv run python scripts/check_ingest_stalls.py          # stalls + looping summary
    uv run python scripts/check_ingest_stalls.py --all    # every open non-Wait step

Exit status 1 when something is stalled.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from browser_automation.etere_direct_client import connect
from business_logic.services.ingest_watchdog import format_report, open_jobs, scan


def main():
    with connect() as conn:
        result = scan(conn)
        print(format_report(result))
        if "--all" in sys.argv:
            print("\nEvery open non-Wait step:")
            for j in open_jobs(conn):
                print(
                    f"  {j['minutes']:7.1f} min  cls {j['id_instanceclass']:>2} x{j['start_cnt']:<4}"
                    f" {j['code'] or '?':30s} {j['workflow']}: {j['step']}"
                    + (f"  [{j['message']}]" if j["message"] else "")
                )
    sys.exit(1 if result["count"] else 0)


if __name__ == "__main__":
    main()
