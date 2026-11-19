"""Bring the ES November cut into the NYC Hinglish rotation (IW Lexus 202 NYC, contract 2740).

IW Group's traffic sheet "Lexus CY26 DSE - NYC Asian Indian TV … R2" gave the ES "Child's
Eyes" November cut (TYIW26TL877H :30 = LEXUS30E312, TYIW26TL878H :15 = LEXUS15E312) the
flight 12/1-12/31. The title says CY26-NOV, the row carries the "Asset Swap at 12:01 AM
12/1" note, and the lease offer on screen expires 11/30/2026; the buyer confirmed the typo
on 11/19 (Lee). So November aired RX and NX only since 11/16, where each of RX / NX / ES
should carry one third.

Lee 11/19: heavy up ES on what is left so that ALL of November, aired spots included,
comes out one third each per length.

For each length this takes the November total T (aired + still to air), sets the targets
T // 3 with the remainder to ES first and RX second, and flips just enough unaired RX and
NX rows to ES, evenly spaced through each cut's remaining rows in air order. Each flip is
the one full-row writer (`tpalinse_assign.assign_creative`: code, title, creative,
NEWTYPE, binding, aspect, duration). Line rotation pools (CONTRATTIFILMATI) are not
touched: every November spot is already placed.

Dry run by default (rollback). --apply commits and re-reads from a fresh connection. A
restore .sql of the previous values is written to logs/ first. Safe to re-run: rows that
already carry ES count toward the target.
"""

import argparse
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from browser_automation.etere_direct_client import connect  # noqa: E402
from business_logic.services import playout_binding as pb  # noqa: E402
from business_logic.services.tpalinse_assign import (  # noqa: E402
    assign_creative,
    creative_values,
)

CT = 2740
D0, D1 = "2026-11-01", "2026-11-30"
# length in frames → {cut: FILMATI id}
CUTS = {
    900: {"RX": 153440, "NX": 153439, "ES": 153438},  # LEXUS30E314 / E313 / E312
    450: {"RX": 153431, "NX": 153430, "ES": 153429},  # LEXUS15E314 / E313 / E312
}
COLS = "ID_FILMATI, COD_PROGRA, TITLE, NEWTYPE, SUPPORTO, ASPECT, DURATION_P"

ROWS_SQL = (
    "SELECT tp.ID_TPALINSE, CONVERT(VARCHAR(10), tp.DATA, 120), tp.ORA, RTRIM(tp.STATUS),"
    " tp.ID_FILMATI, cr.ID_CONTRATTIRIGHE, RTRIM(cr.DESCRIZIONE)"
    " FROM trafficPalinse tr"
    " JOIN TPALINSE tp ON tp.ID_TPALINSE = tr.id_tpalinse"
    " JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = tr.ID_ContrattiRighe"
    " WHERE cr.ID_CONTRATTITESTATA = %s AND tp.LIVELLO = 0 AND tp.DATA BETWEEN %s AND %s"
    " ORDER BY tp.DATA, tp.ORA, tp.ID_TPALINSE"
)


def targets(total: int) -> dict:
    """One third each; the remainder goes to ES first, then RX."""
    base, rem = divmod(total, 3)
    return {"ES": base + (rem >= 1), "RX": base + (rem >= 2), "NX": base}


def spread(n: int, k: int) -> list[int]:
    """k positions evenly spaced through n rows."""
    return [int((i + 0.5) * n / k) for i in range(k)]


def november(cur):
    cur.execute(ROWS_SQL, (CT, D0, D1))
    return cur.fetchall()


def counts(rows) -> dict:
    """{length: Counter(cut)} over every November row."""
    cut_of = {fid: (length, cut) for length, m in CUTS.items() for cut, fid in m.items()}
    out = {length: Counter() for length in CUTS}
    for r in rows:
        assert r[4] in cut_of, f"row {r[0]} {r[1]} carries creative {r[4]}, not a November cut"
        length, cut = cut_of[r[4]]
        out[length][cut] += 1
    return out


def plan(rows, today: str) -> list[tuple]:
    """[(row, new FILMATI id, cut it leaves)] — the rows that become ES."""
    flips = []
    for length, m in CUTS.items():
        mine = [r for r in rows if r[4] in m.values()]
        have = Counter({cut: sum(1 for r in mine if r[4] == fid) for cut, fid in m.items()})
        want = targets(len(mine))
        need_es = want["ES"] - have["ES"]
        assert need_es >= 0, f":{length // 30} already carries {have['ES']} ES, target {want['ES']}"
        give = {cut: have[cut] - want[cut] for cut in ("RX", "NX")}
        assert all(v >= 0 for v in give.values()) and sum(give.values()) == need_es, (
            f":{length // 30} have {dict(have)} want {want}: cannot reach the target by "
            "moving RX / NX rows to ES"
        )
        for cut, k in give.items():
            free = [r for r in mine if r[4] == m[cut] and r[3] == "I" and r[1] >= today]
            assert k <= len(free), (
                f":{length // 30} {cut}: {k} rows must move to ES but only {len(free)} are unaired"
            )
            flips += [(free[i], m["ES"], cut) for i in spread(len(free), k)] if k else []
    return sorted(flips, key=lambda f: (f[0][1], f[0][2]))


def _q(v):
    return "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT CONVERT(VARCHAR(10), GETDATE(), 120)")
    today = cur.fetchone()[0]
    rows = november(cur)
    before = counts(rows)
    for length in CUTS:
        n = sum(before[length].values())
        print(
            f":{length // 30}  November total {n}  now {dict(before[length])}  target {targets(n)}"
        )
    flips = plan(rows, today)
    if not flips:
        print("nothing to do: November already carries the target split")
        conn.close()
        return

    ids = [f[0][0] for f in flips]
    assert len(set(ids)) == len(ids)
    cur.execute(
        f"SELECT ID_TPALINSE, {COLS} FROM TPALINSE WHERE ID_TPALINSE IN ({','.join(map(str, ids))})"
    )
    old = {r[0]: r[1:] for r in cur.fetchall()}
    assert len(old) == len(ids)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    restore = Path("logs") / f"lexus-2740-nov-es-rotation-restore-{stamp}.sql"
    restore.parent.mkdir(exist_ok=True)
    restore.write_text(
        "-- restore the rows moved to the ES November cut to their previous creative\n"
        + "".join(
            f"UPDATE TPALINSE SET ID_FILMATI={o[0]}, COD_PROGRA={_q(o[1])}, TITLE={_q(o[2])},"
            f" NEWTYPE={_q(o[3])}, SUPPORTO={_q(o[4])}, ASPECT={_q(o[5])},"
            f" DURATION_P={'NULL' if o[6] is None else o[6]} WHERE ID_TPALINSE={tp};\n"
            for tp, o in old.items()
        )
    )
    print(f"restore written: {restore}")

    print(f"{len(flips)} row(s) move to ES:")
    for r, fid, cut in flips:
        hh = r[2] / 29.97 / 3600
        print(
            f"  {r[0]}  {r[1]}  {int(hh):02d}:{int(hh % 1 * 60):02d}  {cut} → ES ({fid})"
            f"  line {r[5]} {r[6]}"
        )
        if not assign_creative(cur, r[0], fid):
            conn.rollback()
            raise SystemExit(f"asset {fid} has no sized copy to bind (row {r[0]}); nothing written")

    # In-transaction verify: every length at its target, each moved row carries the ES
    # cut's full assignment, and the playout-binding scan finds nothing wrong on them.
    after_rows = november(cur)
    after = counts(after_rows)
    for length in CUTS:
        want = targets(sum(after[length].values()))
        assert dict(after[length]) == want, f":{length // 30} {dict(after[length])} != {want}"
        assert sum(after[length].values()) == sum(before[length].values())
    assert {(r[0], r[1], r[2], r[3], r[5]) for r in after_rows} == {
        (r[0], r[1], r[2], r[3], r[5]) for r in rows
    }, "a row's day, time, status or line moved"
    for r, fid, _cut in flips:
        cur.execute(f"SELECT {COLS} FROM TPALINSE WHERE ID_TPALINSE = %s", (r[0],))
        got = cur.fetchone()
        v = creative_values(cur, fid)
        assert (got[0], (got[1] or "").strip(), (got[2] or "").strip(), got[4], got[5], got[6]) == (
            fid,
            v["COD_PROGRA"],
            v["TITLE"],
            v["SUPPORTO"],
            v["ASPECT"],
            v["DURATION_P"],
        ), f"row {r[0]} reads {got}"
        assert (got[3] or "").strip() == (old[r[0]][3] or "").strip(), f"row {r[0]} NEWTYPE moved"
    dates = [datetime.strptime(f[0][1], "%Y-%m-%d").date() for f in flips]
    cur.execute(
        "SELECT COUNT(*)"
        + pb.mismatch_where(min(dates), max(dates))
        + f" AND t.ID_TPALINSE IN ({','.join(map(str, ids))})"
    )
    assert cur.fetchone()[0] == 0, "a moved row fails the playout-binding scan"
    for length in CUTS:
        print(f":{length // 30}  November after: {dict(after[length])}")

    if not args.apply:
        conn.rollback()
        conn.close()
        print("DRY RUN: verified and rolled back. --apply commits.")
        return
    conn.commit()
    conn.close()
    fresh = connect()
    final = counts(november(fresh.cursor()))
    fresh.close()
    for length in CUTS:
        assert dict(final[length]) == dict(after[length]), f"fresh read {dict(final[length])}"
        print(f":{length // 30}  fresh connection: {dict(final[length])}")
    print(f"APPLIED: {len(flips)} row(s) now carry the ES November cut.")


if __name__ == "__main__":
    main()
