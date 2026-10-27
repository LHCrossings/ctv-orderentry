"""Flip the paid lines on Daviselen Toyota 3213-3216 from Priority to Rotation.

Lee, 2026-10-27: the four So Cal Toyota December estimates were entered under
Priority (Etere's default for a narrow program window) before the automation was
changed to Rotation. Nothing is placed yet, so PRENOTAZIONE alone is the whole
transition (capofila/finefila 0, priorita user-set) — same shape as the 6/25
WorldLink bonus repair.

Dry run by default; --apply commits. Refuses if any line in range has a placed spot.
"""

import argparse
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
for _p in (_root, _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation.etere_direct_client import connect  # noqa: E402

CONTRACTS = (3213, 3214, 3215, 3216)
_IN = ",".join(str(c) for c in CONTRACTS)

_SELECT = f"""
SELECT cr.ID_CONTRATTITESTATA, cr.ID_CONTRATTIRIGHE, cr.DESCRIZIONE, cr.PRENOTAZIONE,
       cr.CONTROLLACAPOFILA, cr.CONTROLLAFINEFILA, cr.PRIORITA, cr.ROWSTATUS,
       (SELECT COUNT(*) FROM trafficPalinse tp WHERE tp.ID_ContrattiRighe = cr.ID_CONTRATTIRIGHE) AS placed
FROM CONTRATTIRIGHE cr
WHERE cr.ID_CONTRATTITESTATA IN ({_IN})
ORDER BY cr.ID_CONTRATTITESTATA, cr.ID_CONTRATTIRIGHE
"""


def _rows(cur):
    cur.execute(_SELECT)
    return cur.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = connect()
    cur = conn.cursor()
    rows = _rows(cur)
    placed = [r for r in rows if r[8] > 0]
    if placed:
        print(
            f"REFUSE: {len(placed)} line(s) already have placed spots; this script is for unplaced lines"
        )
        for r in placed:
            print("  ", r)
        return 2
    targets = [r for r in rows if r[3] != 1 and not r[4] and not r[5]]
    print(f"{len(rows)} lines on {CONTRACTS}; {len(targets)} to flip Priority→Rotation:")
    for r in targets:
        print(f"  {r[0]} line {r[1]} PRENOTAZIONE={r[3]} priorita={r[6]} rowstatus={r[7]}  {r[2]}")
    if not targets:
        print("nothing to do")
        return 0
    if not args.apply:
        print("dry run — rerun with --apply to commit")
        return 0

    ids = ",".join(str(r[1]) for r in targets)
    cur.execute(
        f"UPDATE CONTRATTIRIGHE SET PRENOTAZIONE = 1 "
        f"WHERE ID_CONTRATTIRIGHE IN ({ids}) AND PRENOTAZIONE <> 1 "
        f"AND CONTROLLACAPOFILA = 0 AND CONTROLLAFINEFILA = 0"
    )
    if cur.rowcount != len(targets):
        conn.rollback()
        print(f"ROLLBACK: updated {cur.rowcount}, expected {len(targets)}")
        return 1
    conn.commit()
    conn.close()

    # Readback from a FRESH connection (pymssql autocommit-off lesson, 9/23).
    conn2 = connect()
    after = _rows(conn2.cursor())
    conn2.close()
    not_rot = [r for r in after if r[3] != 1]
    print(
        f"readback: {len(after)} lines, {sum(1 for r in after if r[3] == 1)} Rotation, "
        f"{len(not_rot)} not Rotation, placed={sum(r[8] for r in after)}"
    )
    for r in not_rot:
        print("  STILL NOT ROTATION:", r)
    return 0 if not not_rot else 1


if __name__ == "__main__":
    sys.exit(main())
