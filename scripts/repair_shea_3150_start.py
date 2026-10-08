"""Move Gauger Shea 2610 (contract 3150) to a 10/15 start — Lee, 2026-10-08.

The client pushed the start from Thu 10/8 to Thu 10/15; the end (10/29) and every spot
count stay. Lee pulled the 10/8-10/14 placements in the Etere app himself (the scheduler
had only reserved, nothing aired). What is left to refigure: every line's start date, the
header start, the max-per-day caps (a monthly Rotation order has no weekly count, so the
same spots in fewer days need a higher cap — Sa-Su 11 spots over 4 weekend days is 3/day,
not 2), and ContrattiImportiGiornalieri (the day-accurate revenue the reports read), which
still spreads each line over 10/8-10/29.

Rule for the cap is the Gauger automation's own (`_max_daily`): ceil(spots / active days of
the pattern inside the flight), never below 1 — and cap × active days must cover the spots.

Dry run by default (ROLLBACK); --apply commits. A restore .sql is written to logs/ first.
"""

import math
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_automation.etere_direct_client import connect  # noqa: E402

CT = 3150
LINES = [85241, 85242, 85243, 85244]
NEW_START = date(2026, 10, 15)
END = date(2026, 10, 29)
EXPECTED_TOTAL = 5780.0
DAY_COLS = ["LUNEDI", "MARTEDI", "MERCOLEDI", "GIOVEDI", "VENERDI", "SABATO", "DOMENICA"]
IDS = ",".join(map(str, LINES))


def lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)) or type(v).__name__ == "Decimal":
        return str(v)
    if hasattr(v, "strftime"):
        return "'" + v.strftime("%Y-%m-%d %H:%M:%S") + "'"
    return "N'" + str(v).replace("'", "''") + "'"


def active_dates(d0: date, d1: date, bits) -> list[date]:
    out, d = [], d0
    while d <= d1:
        if bits[d.weekday()]:
            out.append(d)
        d += timedelta(days=1)
    return out


def plan(cur) -> list[dict]:
    cur.execute(
        f"SELECT ID_CONTRATTIRIGHE, DESCRIZIONE, N_PASSAGGI, IMPORTO, PASSAGGI_GIORNALIERI, DATA_INIZIO, DATA_FINE, "
        f"{','.join(DAY_COLS)} FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA={CT} ORDER BY ID_CONTRATTIRIGHE"
    )
    out = []
    for r in cur.fetchall():
        lid, desc, n, rate, cap, d0, d1 = (
            r[0],
            r[1],
            int(r[2]),
            float(r[3]),
            int(r[4]),
            r[5].date(),
            r[6].date(),
        )
        bits = [bool(b) for b in r[7:14]]
        old_days = active_dates(d0, d1, bits)
        new_days = active_dates(NEW_START, END, bits)
        new_cap = max(1, math.ceil(n / len(new_days)))
        assert new_cap * len(new_days) >= n, (
            f"line {lid}: {new_cap}/day x {len(new_days)} days < {n} spots"
        )
        out.append(
            dict(
                id=lid,
                desc=desc,
                spots=n,
                rate=rate,
                old_cap=cap,
                new_cap=new_cap,
                old_days=old_days,
                new_days=new_days,
                total=n * rate,
            )
        )
    return out


def main(apply: bool):
    conn = connect()
    cur = conn.cursor()

    # ── preconditions ───────────────────────────────────────────────────────────
    p = plan(cur)
    assert [x["id"] for x in p] == LINES, f"line set changed: {[x['id'] for x in p]}"
    assert all(
        x["old_days"][0] == date(2026, 10, 8) or x["old_days"][0] == date(2026, 10, 10) for x in p
    )
    cur.execute(f"SELECT COUNT(*) FROM trafficPalinse WHERE ID_ContrattiRighe IN ({IDS})")
    placed = cur.fetchone()[0]
    assert placed == 0, f"{placed} placement(s) still on the contract — unschedule in the app first"
    cur.execute(f"SELECT COUNT(*) FROM Traffic_ScheduleList WHERE ID_ContrattiRighe IN ({IDS})")
    assert cur.fetchone()[0] == 0, "blacklist rows exist — accounting would change"
    cur.execute(
        f"SELECT DATA_INIZIO, DATA_TERMINE, DATA_ACQUISIZIONE, LISTINO FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA={CT}"
    )
    h = cur.fetchone()
    assert h[1].date() == END and abs(float(h[3]) - EXPECTED_TOTAL) < 0.005, f"header changed: {h}"
    total = sum(x["total"] for x in p)
    assert abs(total - EXPECTED_TOTAL) < 0.005, f"line total {total} != {EXPECTED_TOTAL}"
    print(
        f"[PRE] 4 lines, 0 placed, no blacklist, header {h[0]:%m/%d}-{h[1]:%m/%d}, total ${total:,.2f}"
    )
    for x in p:
        chg = (
            f"{x['old_cap']} -> {x['new_cap']}/day"
            if x["old_cap"] != x["new_cap"]
            else f"{x['new_cap']}/day (unchanged)"
        )
        print(
            f"[PLAN] {x['id']} {x['desc']:<36} {x['spots']:>2} spots  "
            f"{len(x['old_days'])} days -> {len(x['new_days'])} days  cap {chg}"
        )

    # ── restore file ────────────────────────────────────────────────────────────
    restore = [
        f"-- restore for contract {CT} start move, generated {date.today()} — run as one batch",
        "BEGIN TRAN;",
    ]
    cur.execute(
        f"SELECT ID_CONTRATTIRIGHE, DATA_INIZIO, DATA_FINE, DATESTART, DATEEND, PASSAGGI_GIORNALIERI "
        f"FROM CONTRATTIRIGHE WHERE ID_CONTRATTIRIGHE IN ({IDS})"
    )
    for r in cur.fetchall():
        restore.append(
            f"UPDATE CONTRATTIRIGHE SET DATA_INIZIO={lit(r[1])}, DATA_FINE={lit(r[2])}, DATESTART={lit(r[3])}, "
            f"DATEEND={lit(r[4])}, PASSAGGI_GIORNALIERI={lit(r[5])} WHERE ID_CONTRATTIRIGHE={r[0]};"
        )
    restore.append(
        f"UPDATE CONTRATTITESTATA SET DATA_INIZIO={lit(h[0])}, DATA_TERMINE={lit(h[1])}, "
        f"DATA_ACQUISIZIONE={lit(h[2])} WHERE ID_CONTRATTITESTATA={CT};"
    )
    cur.execute(f"SELECT * FROM ContrattiImportiGiornalieri WHERE ID_ContrattiRighe IN ({IDS})")
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    restore.append(f"DELETE FROM ContrattiImportiGiornalieri WHERE ID_ContrattiRighe IN ({IDS});")
    for r in rows:
        restore.append(
            f"INSERT INTO ContrattiImportiGiornalieri ({','.join(cols)}) VALUES ({','.join(lit(v) for v in r)});"
        )
    restore.append("COMMIT;")
    rpath = Path("logs") / f"shea-{CT}-start-restore-{date.today():%Y%m%d}.sql"
    rpath.parent.mkdir(exist_ok=True)
    rpath.write_text("\n".join(restore) + "\n")
    print(f"[BACKUP] {rpath} ({len(rows)} day-revenue rows)")

    # ── writes (one transaction) ────────────────────────────────────────────────
    for x in p:
        cur.execute(
            "UPDATE CONTRATTIRIGHE SET DATA_INIZIO=%s, DATESTART=%s, PASSAGGI_GIORNALIERI=%s WHERE ID_CONTRATTIRIGHE=%s",
            (NEW_START, NEW_START, x["new_cap"], x["id"]),
        )
        assert cur.rowcount == 1
    print(f"[LINES] 4 lines start {NEW_START}, caps {[x['new_cap'] for x in p]}")

    cur.execute(f"DELETE FROM ContrattiImportiGiornalieri WHERE ID_ContrattiRighe IN ({IDS})")
    for x in p:
        if x["total"] == 0:
            continue  # Etere wrote no day-revenue rows for the $0 bonus line; keep that shape
        days = x["new_days"]
        per = round(x["total"] / len(days), 4)
        amts = [per] * (len(days) - 1) + [round(x["total"] - per * (len(days) - 1), 4)]
        for d, a in zip(days, amts):
            cur.execute(
                "INSERT INTO ContrattiImportiGiornalieri (ID_ContrattiRighe, DATA, IMPORTO) VALUES (%s,%s,%s)",
                (x["id"], d, a),
            )
        print(
            f"[IMPORTI] line {x['id']}: ${x['total']:,.2f} over {len(days)} days {days[0]:%m/%d}-{days[-1]:%m/%d}"
        )

    cur.execute(
        f"UPDATE CONTRATTITESTATA SET DATA_INIZIO=%s, DATA_ACQUISIZIONE=%s WHERE ID_CONTRATTITESTATA={CT}",
        (NEW_START, NEW_START),
    )
    print(
        f"[HEADER] start {NEW_START}, end {END} unchanged, totals ${EXPECTED_TOTAL:,.2f} unchanged"
    )

    verify(cur, "in-txn")
    if apply:
        conn.commit()
        print("[COMMIT] committed")
    else:
        conn.rollback()
        print("[DRY RUN] rolled back — rerun with --apply")
    conn.close()
    if apply:
        c2 = connect()
        verify(c2.cursor(), "fresh connection")
        c2.close()


def verify(cur, label):
    cur.execute(
        f"SELECT COUNT(*), SUM(N_PASSAGGI*IMPORTO), MIN(DATA_INIZIO), MAX(DATA_INIZIO), MIN(DATESTART), MAX(DATESTART), "
        f"MAX(DATA_FINE), MAX(DATEEND) FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA={CT}"
    )
    n, tot, s0, s1, fs0, fs1, e1, fe1 = cur.fetchone()
    cur.execute(
        f"SELECT ID_CONTRATTIRIGHE, N_PASSAGGI, PASSAGGI_GIORNALIERI, {','.join(DAY_COLS)} FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA={CT}"
    )
    caps_ok = all(
        int(r[2]) * len(active_dates(NEW_START, END, [bool(b) for b in r[3:10]])) >= int(r[1])
        for r in cur.fetchall()
    )
    cur.execute(
        f"SELECT SUM(IMPORTO), COUNT(*), MIN(DATA), MAX(DATA) FROM ContrattiImportiGiornalieri WHERE ID_ContrattiRighe IN ({IDS})"
    )
    cig, cign, c0, c1 = cur.fetchone()
    cur.execute(
        f"SELECT DATA_INIZIO, DATA_TERMINE, DATA_ACQUISIZIONE, LISTINO FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA={CT}"
    )
    h0, h1, ha, hl = cur.fetchone()
    cur.execute(f"SELECT COUNT(*) FROM trafficPalinse WHERE ID_ContrattiRighe IN ({IDS})")
    placed = cur.fetchone()[0]
    print(
        f"[VERIFY {label}] lines={n} total=${float(tot):,.2f} starts {s0:%m/%d}..{s1:%m/%d} (form {fs0:%m/%d}..{fs1:%m/%d}) "
        f"end {e1:%m/%d}/{fe1:%m/%d} caps_cover_spots={caps_ok} | importi ${float(cig):,.2f} in {cign} rows {c0:%m/%d}-{c1:%m/%d} | "
        f"header {h0:%m/%d}-{h1:%m/%d} acq {ha:%m/%d} ${float(hl):,.2f} | placed {placed}"
    )
    ok = (
        n == 4
        and abs(float(tot) - EXPECTED_TOTAL) < 0.005
        and s0.date() == s1.date() == fs0.date() == fs1.date() == NEW_START
        and e1.date() == fe1.date() == END
        and caps_ok
        and abs(float(cig) - EXPECTED_TOTAL) < 0.005
        and c0.date() >= NEW_START
        and c1.date() <= END
        and h0.date() == ha.date() == NEW_START
        and h1.date() == END
        and abs(float(hl) - EXPECTED_TOTAL) < 0.005
        and placed == 0
    )
    if not ok:
        raise SystemExit(f"[VERIFY {label}] FAILED")
    print(f"[VERIFY {label}] OK")


if __name__ == "__main__":
    main("--apply" in sys.argv)
