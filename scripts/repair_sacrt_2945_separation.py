"""SacRT 2608 (contract 2945): customer separation 60 -> 25 min, Hmong lines -> 15 min.

Lee, 2026-10-09, after the 10/10 restart shortened the flight from 13 weeks to 3. The order
entered at 60/0/0. A break-capacity test over every active day (COMS segments of each line's
attached blocks, greedy at the candidate interval) showed 25 min leaves every window at least
double its need except Hmong Sa-Su 6-8p, where 24 spots over the 6 remaining weekend days need
all 4 breaks that 25 min allows, every day, with no alternative. Lee's rule: paid lines
25/0/0, except Hmong paid 15/0/0; every bonus line (booking code 10) 15/0/0 regardless of
language. Order and event intervals stay 0.

Stored in frames at 29.97 fps: 60 min = 107892 (the value on the lines today),
25 min = 44955, 15 min = 26973.

Dry run by default (ROLLBACK); --apply commits and re-reads from a fresh connection.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_automation.etere_direct_client import connect  # noqa: E402

CT = 2945
FPS = 29.97
HMONG_PAID = 80701
LINES = list(range(80697, 80709))
OLD = 60
BONUS_CODE = 10


def target_minutes(lid: int, booking_code: int) -> int:
    if booking_code == BONUS_CODE or lid == HMONG_PAID:
        return 15
    return 25


def frames(minutes: int) -> int:
    return round(minutes * 60 * FPS)


def main(apply: bool):
    conn = connect()
    cur = conn.cursor()

    cur.execute(
        f"SELECT ID_CONTRATTIRIGHE, DESCRIZIONE, Interv_Committente, INTERVALLO, INTERV_CONTRATTO, ID_BOOKINGCODE "
        f"FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA={CT} ORDER BY 1"
    )
    rows = cur.fetchall()
    assert [r[0] for r in rows] == LINES, f"line set changed: {[r[0] for r in rows]}"
    assert all(int(r[5]) in (2, BONUS_CODE) for r in rows), (
        f"unexpected booking code: {[(r[0], r[5]) for r in rows]}"
    )
    NEW = {r[0]: target_minutes(r[0], int(r[5])) for r in rows}
    assert sorted(NEW.values()) == [15] * 7 + [25] * 5, NEW
    assert all(int(r[2]) == frames(OLD) and int(r[3]) == 0 and int(r[4]) == 0 for r in rows), (
        f"intervals are not 60/0/0 everywhere: {[(r[0], r[2], r[3], r[4]) for r in rows]}"
    )
    # frame convention oracle: other lines Etere's own form wrote at 25 and 15 minutes
    cur.execute(
        "SELECT SUM(CASE WHEN Interv_Committente=%s THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN Interv_Committente=%s THEN 1 ELSE 0 END) FROM CONTRATTIRIGHE",
        (frames(25), frames(15)),
    )
    n25, n15 = cur.fetchone()
    assert n25 and n15, f"frame values {frames(25)}/{frames(15)} not seen on any existing line"
    print(f"[PRE] 12 lines at 60/0/0; oracle lines at 25 min: {n25}, at 15 min: {n15}")

    restore = [
        f"-- restore for contract {CT} separation change, generated {date.today()}",
        "BEGIN TRAN;",
    ]
    for r in rows:
        restore.append(
            f"UPDATE CONTRATTIRIGHE SET Interv_Committente={int(r[2])} WHERE ID_CONTRATTIRIGHE={r[0]};"
        )
    restore.append("COMMIT;")
    rpath = Path("logs") / f"sacrt-{CT}-separation-restore-{date.today():%Y%m%d}.sql"
    rpath.write_text("\n".join(restore) + "\n")
    print(f"[BACKUP] {rpath}")

    for r in rows:
        lid, mins = r[0], NEW[r[0]]
        cur.execute(
            "UPDATE CONTRATTIRIGHE SET Interv_Committente=%s WHERE ID_CONTRATTIRIGHE=%s",
            (frames(mins), lid),
        )
        assert cur.rowcount == 1
        kind = "BNS " if int(r[5]) == BONUS_CODE else "paid"
        print(f"[LINE] {lid} {kind} {r[1]:<26} {OLD} -> {mins}/0/0")

    verify(cur, "in-txn", NEW)
    if apply:
        conn.commit()
        print("[COMMIT] committed")
    else:
        conn.rollback()
        print("[DRY RUN] rolled back — rerun with --apply")
    conn.close()
    if apply:
        c2 = connect()
        verify(c2.cursor(), "fresh connection", NEW)
        c2.close()


def verify(cur, label, NEW):
    cur.execute(
        f"SELECT ID_CONTRATTIRIGHE, Interv_Committente, INTERVALLO, INTERV_CONTRATTO "
        f"FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA={CT} ORDER BY 1"
    )
    got = {r[0]: (int(r[1]), int(r[2]), int(r[3])) for r in cur.fetchall()}
    want = {lid: (frames(m), 0, 0) for lid, m in NEW.items()}
    bad = {k: (got.get(k), want[k]) for k in want if got.get(k) != want[k]}
    summary = ", ".join(f"{lid}={round(v[0] / 60 / FPS)}" for lid, v in sorted(got.items()))
    print(
        f"[VERIFY {label}] customer minutes by line: {summary}; order/event all 0: "
        f"{all(v[1] == 0 and v[2] == 0 for v in got.values())}"
    )
    if bad:
        raise SystemExit(f"[VERIFY {label}] FAILED {bad}")
    print(f"[VERIFY {label}] OK")


if __name__ == "__main__":
    main("--apply" in sys.argv)
