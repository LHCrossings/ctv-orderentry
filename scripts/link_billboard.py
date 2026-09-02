"""Link a billboard contract line to the spot line it must air directly before.

Etere's web UI does this on the line form ("linked spot"). Behind the scenes it is two
columns on the SPOT line (the :15/:30 that follows the billboard), oracle = PACO BMO 27
lines 72799→72808 and Daviselen Toyota 1325 lines 71588→71590 (both hand-linked, both
placed billboard-then-spot at consecutive XORDERs every time):

    CONTRATTIRIGHE.IDLINKEDSPOTSCHEDPOS = <billboard line id>
    CONTRATTIRIGHE.LINKEDSPOTSCHEDPOS   = 1      (1 = the linked spot airs BEFORE this line)

The billboard line itself is an ordinary billboard (Top of break, priority 3, separation
0) and keeps its own link columns at 0. Nothing else on either line changes.

Usage (dry run prints what would change, --apply writes inside a verified transaction):
    uv run python3 scripts/link_billboard.py --spot 72799 --billboard 72808
    uv run python3 scripts/link_billboard.py --spot 72799 --billboard 72808 --apply
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "browser_automation"))

from browser_automation.etere_direct_client import connect  # noqa: E402

LINK_BEFORE = 1


def _line(cur, line_id: int) -> dict:
    cur.execute(
        """SELECT r.ID_CONTRATTIRIGHE, r.ID_CONTRATTITESTATA, t.COD_CONTRATTO, r.DESCRIZIONE,
                  r.DURATA, r.N_PASSAGGI, r.CONTROLLACAPOFILA, r.CONTROLLAFINEFILA, r.PRIORITA,
                  r.PRENOTAZIONE, r.ID_BOOKINGCODE, r.IDLINKEDSPOTSCHEDPOS, r.LINKEDSPOTSCHEDPOS,
                  r.DATA_INIZIO, r.DATA_FINE, r.COD_USER
           FROM CONTRATTIRIGHE r JOIN CONTRATTITESTATA t ON t.ID_CONTRATTITESTATA = r.ID_CONTRATTITESTATA
           WHERE r.ID_CONTRATTIRIGHE = %s""",
        (line_id,),
    )
    row = cur.fetchone()
    if row is None:
        sys.exit(f"line {line_id} not found")
    keys = (
        "id",
        "contract_id",
        "code",
        "desc",
        "durata",
        "spots",
        "capofila",
        "finefila",
        "priorita",
        "prenotazione",
        "booking",
        "linked_id",
        "linked_pos",
        "date_from",
        "date_to",
        "cod_user",
    )
    return dict(zip(keys, row))


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--spot", type=int, required=True, help="CONTRATTIRIGHE id of the :15/:30 spot line"
    )
    ap.add_argument(
        "--billboard", type=int, required=True, help="CONTRATTIRIGHE id of the billboard line"
    )
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    a = ap.parse_args()

    conn = connect()
    cur = conn.cursor()
    spot, bb = _line(cur, a.spot), _line(cur, a.billboard)
    for tag, ln in (("SPOT     ", spot), ("BILLBOARD", bb)):
        print(
            f"{tag} {ln['id']}  {ln['code']}  {ln['desc']!r}  {ln['durata'] / 29.97:.0f}s  "
            f"spots={ln['spots']}  top={int(bool(ln['capofila']))}  prio={ln['priorita']}  "
            f"sched={ln['prenotazione']}  book={ln['booking']}  linked={ln['linked_id']}/{ln['linked_pos']}  "
            f"{ln['date_from']:%m/%d}-{ln['date_to']:%m/%d}  market={ln['cod_user']}"
        )
    problems = []
    if spot["contract_id"] != bb["contract_id"]:
        problems.append("the two lines are on different contracts")
    if not bb["capofila"]:
        problems.append(
            "billboard line is not Top of break (CONTROLLACAPOFILA=0) — link it, but check the line"
        )
    if spot["capofila"]:
        problems.append(
            "spot line is itself Top of break — the PACO convention puts Top on the billboard only"
        )
    if spot["spots"] != bb["spots"]:
        problems.append(
            f"spot counts differ ({spot['spots']} vs {bb['spots']}) — every billboard needs a partner"
        )
    if spot["linked_id"] not in (0, None, a.billboard):
        problems.append(f"spot line already links to {spot['linked_id']}")
    if bb["linked_id"]:
        problems.append(f"billboard line carries a link itself ({bb['linked_id']}) — should be 0")
    for p in problems:
        print("  ⚠", p)
    if spot["linked_id"] == a.billboard and spot["linked_pos"] == LINK_BEFORE:
        print("already linked — nothing to do")
        return
    print(
        f"\nwould set CONTRATTIRIGHE {a.spot}: IDLINKEDSPOTSCHEDPOS={a.billboard}, LINKEDSPOTSCHEDPOS={LINK_BEFORE}"
    )
    if not a.apply:
        print("DRY RUN — add --apply to write")
        return
    if any("different contracts" in p for p in problems):
        sys.exit("refusing: different contracts")
    cur.execute(
        "UPDATE CONTRATTIRIGHE SET IDLINKEDSPOTSCHEDPOS=%s, LINKEDSPOTSCHEDPOS=%s WHERE ID_CONTRATTIRIGHE=%s",
        (a.billboard, LINK_BEFORE, a.spot),
    )
    if cur.rowcount != 1:
        conn.rollback()
        sys.exit(f"update touched {cur.rowcount} rows — rolled back")
    after = _line(cur, a.spot)
    if (after["linked_id"], after["linked_pos"]) != (a.billboard, LINK_BEFORE):
        conn.rollback()
        sys.exit(f"readback mismatch {after['linked_id']}/{after['linked_pos']} — rolled back")
    conn.commit()
    print(f"LINKED: spot line {a.spot} → billboard line {a.billboard} airs immediately before it")
    print(
        f"undo: UPDATE CONTRATTIRIGHE SET IDLINKEDSPOTSCHEDPOS=0, LINKEDSPOTSCHEDPOS=0 WHERE ID_CONTRATTIRIGHE={a.spot};"
    )


if __name__ == "__main__":
    main()
