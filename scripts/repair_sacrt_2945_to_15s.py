"""SacRT 2608 (contract 2945): convert the :30 airtime to :15s — Lee, 2026-10-09.

The media plan sold :30 units and says "If 15" creatives are provided, each 30" unit of
airtime will be converted into two 15" spots." The client delivered :15s. Lee unschedules the
order in the Etere app first (the app's own unschedule, no ghost risk), then this runs.

Per line, one transaction:
  * DURATA 900 -> 450 frames (:15)
  * N_PASSAGGI x2 (158 -> 316 spots); IMPORTO / 2 (paid $43 -> $21.50, bonus stays $0) so
    every line total and the $4,988 airtime total are unchanged
  * PASSAGGI_GIORNALIERI x2, except the Hmong bonus line 1 -> 3 (its first weekend's 6pm
    breaks are already full for other advertisers; 3/day lets it catch up later)
  * Interv_Committente 15 min on every line, 5 min on both Hmong lines (6p-8p has 14 breaks
    8-9 min apart; 5 min = one SacRT spot per break, every break usable). Order/event stay 0.
Day revenue (ContrattiImportiGiornalieri) is untouched: same dollars, same days. The header
is untouched: same dates, same $6,208 (airtime + production).

Capacity check behind the numbers: a day-by-day greedy placement into the real CVC breaks of
each line's attached blocks, honouring separation and the room other advertisers already
booked, seats 315/316 at these settings (the 316th is the Hmong bonus on the full first
weekend, hence the 3/day cap; Lee can also move a competing spot out of a full break).

Dry run by default (ROLLBACK); --apply commits and re-reads from a fresh connection.
A restore .sql is written to logs/ before any write.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_automation.etere_direct_client import connect  # noqa: E402

CT = 2945
FPS = 29.97
LINES = list(range(80697, 80709))
IDS = ",".join(map(str, LINES))
HMONG = {80701, 80702}
HMONG_BONUS = 80702
BONUS_CODE = 10
OLD_DUR, NEW_DUR = 900, 450
OLD_SPOTS = {
    80697: 20, 80698: 9, 80699: 20, 80700: 7, 80701: 18, 80702: 6,
    80703: 18, 80704: 6, 80705: 20, 80706: 7, 80707: 20, 80708: 7,
}  # fmt: skip
AIRTIME_TOTAL = 4988.0
HEADER_TOTAL = 6208.0


def frames(minutes: int) -> int:
    return round(minutes * 60 * FPS)


def target(r) -> dict:
    lid, bk, n, rate, cap = r["id"], int(r["bk"]), int(r["n"]), float(r["rate"]), int(r["cap"])
    return dict(
        spots=n * 2,
        rate=rate / 2,
        cap=3 if lid == HMONG_BONUS else cap * 2,
        sep=frames(5) if lid in HMONG else frames(15),
        dur=NEW_DUR,
        bonus=bk == BONUS_CODE,
    )


def load(cur) -> list[dict]:
    cur.execute(
        f"SELECT ID_CONTRATTIRIGHE id, DESCRIZIONE d, ID_BOOKINGCODE bk, N_PASSAGGI n, IMPORTO rate, "
        f"PASSAGGI_GIORNALIERI cap, DURATA dur, Interv_Committente sep, INTERVALLO io, INTERV_CONTRATTO ie "
        f"FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA={CT} ORDER BY ID_CONTRATTIRIGHE"
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def main(apply: bool):
    conn = connect()
    cur = conn.cursor()

    # ── preconditions ───────────────────────────────────────────────────────────
    rows = load(cur)
    assert [r["id"] for r in rows] == LINES, f"line set changed: {[r['id'] for r in rows]}"
    for r in rows:
        assert int(r["dur"]) == OLD_DUR, (
            f"line {r['id']} is not :30 ({r['dur']} frames) — already converted?"
        )
        assert int(r["n"]) == OLD_SPOTS[r["id"]], (
            f"line {r['id']} spots {r['n']} != {OLD_SPOTS[r['id']]}"
        )
        assert int(r["bk"]) in (2, BONUS_CODE), f"line {r['id']} booking code {r['bk']}"
        want_rate = 0.0 if int(r["bk"]) == BONUS_CODE else 43.0
        assert abs(float(r["rate"]) - want_rate) < 0.005, (
            f"line {r['id']} rate {r['rate']} != {want_rate}"
        )
        assert int(r["io"]) == 0 and int(r["ie"]) == 0, f"line {r['id']} order/event interval not 0"
    cur.execute(f"SELECT COUNT(*) FROM trafficPalinse WHERE ID_ContrattiRighe IN ({IDS})")
    placed = cur.fetchone()[0]
    assert placed == 0, f"{placed} placement(s) still on the contract — unschedule in the app first"
    cur.execute(f"SELECT COUNT(*) FROM Traffic_ScheduleList WHERE ID_ContrattiRighe IN ({IDS})")
    assert cur.fetchone()[0] == 0, "blacklist rows exist — accounting would change"
    total = sum(int(r["n"]) * float(r["rate"]) for r in rows)
    assert abs(total - AIRTIME_TOTAL) < 0.005, f"airtime total {total} != {AIRTIME_TOTAL}"
    cur.execute(f"SELECT LISTINO FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA={CT}")
    assert abs(float(cur.fetchone()[0]) - HEADER_TOTAL) < 0.005, "header total changed"
    print(f"[PRE] 12 lines at :30, 158 spots, ${total:,.2f}, 0 placed, no blacklist")

    plan = {r["id"]: target(r) for r in rows}
    for r in rows:
        t = plan[r["id"]]
        print(
            f"[PLAN] {r['id']} {'BNS ' if t['bonus'] else 'paid'} {r['d']:<26} "
            f"{int(r['n']):>2} x ${float(r['rate']):>5.2f} -> {t['spots']:>2} x ${t['rate']:>5.2f}  "
            f"cap {int(r['cap'])}->{t['cap']}/day  sep {round(int(r['sep']) / 60 / FPS)}->{round(t['sep'] / 60 / FPS)} min"
        )
    new_total = sum(t["spots"] * t["rate"] for t in plan.values())
    assert abs(new_total - AIRTIME_TOTAL) < 0.005, f"converted total {new_total} != {AIRTIME_TOTAL}"

    # ── restore file ────────────────────────────────────────────────────────────
    restore = [
        f"-- restore for contract {CT} :15 conversion, generated {date.today()} — run as one batch",
        "BEGIN TRAN;",
    ]
    for r in rows:
        restore.append(
            f"UPDATE CONTRATTIRIGHE SET DURATA={int(r['dur'])}, N_PASSAGGI={int(r['n'])}, IMPORTO={float(r['rate'])}, "
            f"PASSAGGI_GIORNALIERI={int(r['cap'])}, Interv_Committente={int(r['sep'])} WHERE ID_CONTRATTIRIGHE={r['id']};"
        )
    restore.append("COMMIT;")
    rpath = Path("logs") / f"sacrt-{CT}-to15s-restore-{date.today():%Y%m%d}.sql"
    rpath.parent.mkdir(exist_ok=True)
    rpath.write_text("\n".join(restore) + "\n")
    print(f"[BACKUP] {rpath}")

    # ── writes (one transaction) ────────────────────────────────────────────────
    for lid, t in plan.items():
        cur.execute(
            "UPDATE CONTRATTIRIGHE SET DURATA=%s, N_PASSAGGI=%s, IMPORTO=%s, PASSAGGI_GIORNALIERI=%s, "
            "Interv_Committente=%s WHERE ID_CONTRATTIRIGHE=%s",
            (t["dur"], t["spots"], t["rate"], t["cap"], t["sep"], lid),
        )
        assert cur.rowcount == 1
    print(
        f"[LINES] 12 lines -> :15, {sum(t['spots'] for t in plan.values())} spots, ${new_total:,.2f}"
    )

    verify(cur, "in-txn", plan)
    if apply:
        conn.commit()
        print("[COMMIT] committed")
    else:
        conn.rollback()
        print("[DRY RUN] rolled back — rerun with --apply")
    conn.close()
    if apply:
        c2 = connect()
        verify(c2.cursor(), "fresh connection", plan)
        c2.close()


def verify(cur, label, plan):
    rows = load(cur)
    got = {
        r["id"]: (
            int(r["dur"]),
            int(r["n"]),
            round(float(r["rate"]), 4),
            int(r["cap"]),
            int(r["sep"]),
            int(r["io"]),
            int(r["ie"]),
        )
        for r in rows
    }
    want = {
        lid: (t["dur"], t["spots"], round(t["rate"], 4), t["cap"], t["sep"], 0, 0)
        for lid, t in plan.items()
    }
    bad = {k: (got.get(k), want[k]) for k in want if got.get(k) != want[k]}
    total = sum(int(r["n"]) * float(r["rate"]) for r in rows)
    cur.execute(
        f"SELECT ISNULL(SUM(IMPORTO),0) FROM ContrattiImportiGiornalieri WHERE ID_ContrattiRighe IN ({IDS})"
    )
    cig = float(cur.fetchone()[0])
    cur.execute(
        f"SELECT LISTINO, DATA_INIZIO, DATA_TERMINE FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA={CT}"
    )
    hl, h0, h1 = cur.fetchone()
    cur.execute(f"SELECT COUNT(*) FROM trafficPalinse WHERE ID_ContrattiRighe IN ({IDS})")
    placed = cur.fetchone()[0]
    print(
        f"[VERIFY {label}] lines={len(rows)} spots={sum(int(r['n']) for r in rows)} durations={sorted({int(r['dur']) for r in rows})} "
        f"airtime=${total:,.2f} importi=${cig:,.2f} header {h0:%m/%d}-{h1:%m/%d} ${float(hl):,.2f} placed={placed} mismatches={len(bad)}"
    )
    ok = (
        not bad
        and len(rows) == len(LINES)
        and abs(total - AIRTIME_TOTAL) < 0.005
        and abs(cig - AIRTIME_TOTAL) < 0.005
        and abs(float(hl) - HEADER_TOTAL) < 0.005
        and h0.date() == date(2026, 10, 10)
        and h1.date() == date(2026, 10, 30)
        and placed == 0
    )
    if not ok:
        raise SystemExit(f"[VERIFY {label}] FAILED {bad}")
    print(f"[VERIFY {label}] OK")


if __name__ == "__main__":
    main("--apply" in sys.argv)
