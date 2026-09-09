"""Ghost-spot watchdog: future commercial playlist rows with no contract backing.

A ghost spot is a TPALINSE row (the playlist EE airs from) whose trafficPalinse
row (the contract-side placement SE shows) is gone: it airs, unbilled, and
violates separation against the legitimate spots around it. 45 were found live
on 2026-07-14 (WL Coterie 2919); historical ones trace back to 2022, so causes
include manual EE/SE operations, not just automation. `_unschedule_spots` in
worldlink_automation.py was one confirmed producer (fixed 2026-07-14).

    uv run python scripts/check_ghost_spots.py            # future ghosts (actionable)
    uv run python scripts/check_ghost_spots.py --history  # also count aired ones by month

Deleting: back up the rows first, then DELETE FROM TPALINSE by ID_TPALINSE.
The same scan runs nightly in Broadcast Health (business_logic/services/ghost_spots.py).
Never delete PAST rows — they're as-run history. PER/PSA rows without
trafficPalinse are the daily filler mechanism and are EXCLUDED by design.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from browser_automation.etere_direct_client import connect
from business_logic.services.ghost_spots import GHOST_WHERE, format_report, scan


def main():
    with connect() as conn:
        result = scan(conn)
        print(format_report(result))
        if "--history" in sys.argv:
            cur = conn.cursor()
            cur.execute(
                "SELECT CONVERT(varchar(7), t.DATA, 23), COUNT(*) "
                "FROM TPALINSE t LEFT JOIN trafficPalinse tp ON tp.id_tpalinse = t.ID_TPALINSE "
                f"WHERE {GHOST_WHERE} AND t.DATA <= GETDATE() "
                "GROUP BY CONVERT(varchar(7), t.DATA, 23) ORDER BY 1"
            )
            hist = cur.fetchall()
            print("\nAired ghost spots by month (history — leave alone, as-run record):")
            for mth, n in hist:
                print(f"  {mth}: {n}")
            print(f"  total: {sum(n for _, n in hist)}")
    sys.exit(1 if result["count"] else 0)


if __name__ == "__main__":
    main()
