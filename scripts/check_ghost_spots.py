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
Never delete PAST rows — they're as-run history. PER/PSA rows without
trafficPalinse are the daily filler mechanism and are EXCLUDED by design.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from browser_automation.etere_direct_client import connect

FPS = 29.97
MARKETS = {1: 'NYC', 2: 'CMP', 3: 'HOU', 4: 'SFO', 5: 'SEA',
           6: 'LAX', 7: 'CVC', 8: 'WDC', 9: 'MMT', 10: 'DAL'}

_GHOST_WHERE = (
    "t.LIVELLO = 0 AND t.NEWTYPE = 'COM' AND t.ID_FILMATI > 0 "
    "AND tp.id_trafficPalinse IS NULL"
)


def main():
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT t.ID_TPALINSE, t.COD_USER, CONVERT(varchar, t.DATA, 23), t.ORA, "
            "t.COD_PROGRA, t.DURATION "
            "FROM TPALINSE t LEFT JOIN trafficPalinse tp ON tp.id_tpalinse = t.ID_TPALINSE "
            f"WHERE {_GHOST_WHERE} AND t.DATA > GETDATE() "
            "ORDER BY t.DATA, t.COD_USER"
        )
        rows = cur.fetchall()
        if not rows:
            print("✓ Playlist clean — no future commercial spots without contract backing.")
        else:
            print(f"⚠ {len(rows)} future ghost spot(s) — these WILL AIR UNBILLED:\n")
            for tid, cu, d, ora, prog, dur in rows:
                s = int(ora) / FPS
                print(f"  {d}  {MARKETS.get(int(cu), cu):4s} "
                      f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{int(s % 60):02d}  "
                      f"dur={round(int(dur) / FPS)}s  id_tpalinse={tid}  {prog}")

        if '--history' in sys.argv:
            cur.execute(
                "SELECT CONVERT(varchar(7), t.DATA, 23), COUNT(*) "
                "FROM TPALINSE t LEFT JOIN trafficPalinse tp ON tp.id_tpalinse = t.ID_TPALINSE "
                f"WHERE {_GHOST_WHERE} AND t.DATA <= GETDATE() "
                "GROUP BY CONVERT(varchar(7), t.DATA, 23) ORDER BY 1"
            )
            hist = cur.fetchall()
            print("\nAired ghost spots by month (history — leave alone, as-run record):")
            for mth, n in hist:
                print(f"  {mth}: {n}")
            print(f"  total: {sum(n for _, n in hist)}")

    sys.exit(1 if rows else 0)


if __name__ == "__main__":
    main()
