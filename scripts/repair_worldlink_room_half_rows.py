"""Finish the half-written creative assignments the WorldLink room left (2026-10-30 20:34).

The room's "reapply filmati" step wrote `UPDATE TPALINSE SET ID_FILMATI = …` alone onto
the rows Etere re-placed after a restore, so 78 CVC rows (11/2-11/29) carry a creative id
while still reading COMS, no code, no title, no duration and an EMPTY SUPPORTO — the
Broadcast Health scan flagged the first six as "would air black" (Lee 10/31). This writes
the full assignment (code, title, NEWTYPE from the line's booking code, binding from the
one rule, aspect, duration) onto every unaired row with that signature, in one
transaction, verified inside it and re-read from a fresh connection.

Dry run by default (rollback). --apply commits. A restore .sql of the previous values is
written to logs/ first.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from browser_automation.etere_direct_client import connect  # noqa: E402
from business_logic.services import playout_binding as pb  # noqa: E402
from business_logic.services.tpalinse_assign import assign_creative  # noqa: E402

COLS = "ID_FILMATI, COD_PROGRA, TITLE, NEWTYPE, SUPPORTO, ASPECT, DURATION_P"

# The signature: a creative id with nothing else of the assignment written.
HALF_WHERE = (
    " FROM TPALINSE t WHERE t.LIVELLO = 0 AND t.COD_USER BETWEEN 1 AND 10"
    " AND t.STATUS IN ('I', 'E') AND t.DATA >= CAST(GETDATE() AS DATE)"
    " AND t.ID_FILMATI > 0 AND RTRIM(t.SUPPORTO) = '' AND RTRIM(t.NEWTYPE) = 'COMS'"
)


def half_rows(cur):
    cur.execute(
        "SELECT t.ID_TPALINSE, t.COD_USER, CONVERT(VARCHAR(10), t.DATA, 101), t.STATUS, "
        + COLS
        + HALF_WHERE
        + " ORDER BY t.DATA, t.COD_USER, t.ORA"
    )
    return cur.fetchall()


def row(cur, tp_id):
    cur.execute(f"SELECT {COLS} FROM TPALINSE WHERE ID_TPALINSE = %s", (tp_id,))
    return cur.fetchone()


def sibling(cur, filmati_id, tp_id):
    """A bound, unaired row of the same asset in another market — the oracle for the write."""
    cur.execute(
        f"SELECT TOP 1 ID_TPALINSE, {COLS} FROM TPALINSE WHERE ID_FILMATI = %s AND LIVELLO = 0"
        " AND RTRIM(SUPPORTO) <> '' AND ID_TPALINSE <> %s AND DATA >= CAST(GETDATE() AS DATE)"
        " ORDER BY DATA",
        (filmati_id, tp_id),
    )
    return cur.fetchone()


def _q(v):
    return "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"


def restore_sql(rows):
    out = ["-- restore the half-written rows to their pre-repair values"]
    for r in rows:
        tp_id, _m, _d, _s, fid, cod, title, nt, sup, asp, dur = r
        q = _q
        out.append(
            f"UPDATE TPALINSE SET ID_FILMATI={fid}, COD_PROGRA={q(cod)}, TITLE={q(title)}, NEWTYPE={q(nt)},"
            f" SUPPORTO={q(sup)}, ASPECT={q(asp)}, DURATION_P={'NULL' if dur is None else dur}"
            f" WHERE ID_TPALINSE={tp_id};"
        )
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = connect()
    cur = conn.cursor()
    rows = half_rows(cur)
    print(f"{len(rows)} half-written row(s):")
    by_asset = {}
    for r in rows:
        by_asset.setdefault(r[4], []).append(r)
        print(
            f"  {r[0]}  mkt {r[1]}  {r[2]}  {r[3]}  filmati {r[4]}  NEWTYPE {r[7]!r}  SUPPORTO {r[8]!r}"
        )
    if not rows:
        conn.close()
        return

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    restore = Path("logs") / f"worldlink-room-half-rows-restore-{stamp}.sql"
    restore.parent.mkdir(exist_ok=True)
    restore.write_text(restore_sql(rows))
    print(f"restore written: {restore}")

    written = skipped = 0
    for r in rows:
        if assign_creative(cur, r[0], r[4]):
            written += 1
        else:
            skipped += 1
            print(f"  SKIP {r[0]}: asset {r[4]} has no sized copy to bind")
    print(f"written {written}, skipped {skipped}")

    # In-transaction verify: the signature is gone, the scan's mismatch predicate is empty
    # for these rows, and each asset's row equals a bound sibling column for column.
    left = half_rows(cur)
    assert len(left) == skipped, f"{len(left)} half rows left, expected {skipped}"
    ids = ",".join(str(r[0]) for r in rows)
    dates = [datetime.strptime(r[2], "%m/%d/%Y").date() for r in rows]
    d0, d1 = min(dates), max(dates)
    cur.execute("SELECT COUNT(*)" + pb.mismatch_where(d0, d1) + f" AND t.ID_TPALINSE IN ({ids})")
    mis = cur.fetchone()[0]
    assert mis == skipped, f"{mis} rows still mismatched by the scan predicate"
    for fid, rs in by_asset.items():
        mine = row(cur, rs[0][0])
        sib = sibling(cur, fid, rs[0][0])
        if sib is None:
            print(f"  asset {fid}: no bound sibling to compare — now {mine}")
            continue
        # NEWTYPE may legitimately differ (BNS vs COM lines); every other column must match.
        diff = [
            (c, a, b)
            for c, a, b in zip(COLS.split(", "), mine, sib[1:])
            if c != "NEWTYPE" and a != b
        ]
        assert not diff, f"asset {fid}: row {rs[0][0]} differs from sibling {sib[0]}: {diff}"
        print(
            f"  asset {fid}: {len(rs)} row(s) now match sibling {sib[0]} ({mine[3]!r}, {mine[4]!r})"
        )

    if not args.apply:
        conn.rollback()
        print("DRY RUN — rolled back")
        conn.close()
        return
    conn.commit()
    conn.close()
    print("COMMITTED")

    fresh = connect()
    c2 = fresh.cursor()
    left = half_rows(c2)
    c2.execute("SELECT COUNT(*)" + pb.mismatch_where(d0, d1) + f" AND t.ID_TPALINSE IN ({ids})")
    mis = c2.fetchone()[0]
    sample = row(c2, rows[0][0])
    fresh.close()
    print(
        f"fresh connection: {len(left)} half rows left, {mis} scan mismatches among the repaired ids, sample {rows[0][0]} = {sample}"
    )


if __name__ == "__main__":
    main()
