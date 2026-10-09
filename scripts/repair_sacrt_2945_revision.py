"""Revise SacRT 2608 (contract 2945): start 10/10, same 10/30 end, add the $1,220 production charge.

Lee, 2026-10-09. The order was entered 8/3-10/30 and everything was pulled while the client
finished the spot (0 placements, no blacklist rows, nothing aired). The spot is done, so the
airtime restarts Sat 10/10 and keeps the original 10/30 end. The companion production
proposal ($1,220 NET, translation/VO) was handled by hand back in July; it now joins the
contract the way every mixed order enters today — a CONTRATTISPESE 'Production' row on the
first paid line, dated that line's flight start, PRINTDETAIL=1 (oracle: charges 188-203).

What changes, all in one transaction:
  * every line: DATA_INIZIO + DATESTART -> 10/10 (end columns untouched);
    PASSAGGI_GIORNALIERI -> ceil(spots / active pattern days inside 10/10-10/30), min 1
    (monthly Rotation order: no weekly count, the cap is what paces the spots);
  * ContrattiImportiGiornalieri: each paid line's total re-spread over its active days
    inside the new window (Etere wrote no rows for the $0 bonus lines; that shape is kept);
  * header: DATA_INIZIO + DATA_ACQUISIZIONE -> 10/10; LISTINO / LISTINOORIGINALE / SCONTATO
    4,988 -> 6,208 (every production oracle header carries lines + charges);
  * CONTRATTISPESE: one 'Production' row, $1,220.00, on line 80697, DATA 10/10.

Dry run by default (ROLLBACK); --apply commits and re-verifies from a fresh connection.
A restore .sql is written to logs/ before any write.
"""

import math
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_automation.etere_direct_client import connect  # noqa: E402

CT = 2945
LINES = list(range(80697, 80709))  # 80697..80708, 12 lines
FIRST_PAID_LINE = 80697  # Chinese M-F (7p-12a), $43 x 20 — carries the production charge
OLD_START = date(2026, 8, 3)
NEW_START = date(2026, 10, 10)
END = date(2026, 10, 30)
AIRTIME_TOTAL = 4988.0
PRODUCTION = 1220.0
NEW_HEADER_TOTAL = AIRTIME_TOTAL + PRODUCTION
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
                old_start=d0,
                end=d1,
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
    assert all(x["old_start"] == OLD_START and x["end"] == END for x in p), "line dates changed"
    cur.execute(f"SELECT COUNT(*) FROM trafficPalinse WHERE ID_ContrattiRighe IN ({IDS})")
    placed = cur.fetchone()[0]
    assert placed == 0, f"{placed} placement(s) still on the contract — unschedule in the app first"
    cur.execute(f"SELECT COUNT(*) FROM Traffic_ScheduleList WHERE ID_ContrattiRighe IN ({IDS})")
    assert cur.fetchone()[0] == 0, "blacklist rows exist — accounting would change"
    cur.execute(f"SELECT COUNT(*) FROM CONTRATTISPESE WHERE ID_CONTRATTIRIGHE IN ({IDS})")
    assert cur.fetchone()[0] == 0, (
        "the contract already carries a charge — would double the production"
    )
    cur.execute(
        f"SELECT DATA_INIZIO, DATA_TERMINE, DATA_ACQUISIZIONE, LISTINO, LISTINOORIGINALE, SCONTATO "
        f"FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA={CT}"
    )
    h = cur.fetchone()
    assert h[0].date() == OLD_START and h[1].date() == END, f"header dates changed: {h}"
    assert all(abs(float(v) - AIRTIME_TOTAL) < 0.005 for v in h[3:6]), f"header money changed: {h}"
    total = sum(x["total"] for x in p)
    assert abs(total - AIRTIME_TOTAL) < 0.005, f"line total {total} != {AIRTIME_TOTAL}"
    print(
        f"[PRE] {len(p)} lines, 0 placed, no blacklist, no charges, header {h[0]:%m/%d}-{h[1]:%m/%d}, "
        f"airtime ${total:,.2f}"
    )
    for x in p:
        chg = (
            f"{x['old_cap']} -> {x['new_cap']}/day"
            if x["old_cap"] != x["new_cap"]
            else f"{x['new_cap']}/day (unchanged)"
        )
        print(
            f"[PLAN] {x['id']} {x['desc']:<26} {x['spots']:>2} spots  "
            f"{len(x['old_days']):>2} days -> {len(x['new_days']):>2} days "
            f"({x['new_days'][0]:%m/%d}-{x['new_days'][-1]:%m/%d})  cap {chg}"
        )
    print(
        f"[PLAN] production ${PRODUCTION:,.2f} -> CONTRATTISPESE 'Production' on line {FIRST_PAID_LINE}, "
        f"dated {NEW_START:%m/%d}; header total ${AIRTIME_TOTAL:,.2f} -> ${NEW_HEADER_TOTAL:,.2f}"
    )

    # ── restore file ────────────────────────────────────────────────────────────
    restore = [
        f"-- restore for contract {CT} revision (10/10 start + production), generated {date.today()} — run as one batch",
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
        f"DATA_ACQUISIZIONE={lit(h[2])}, LISTINO={lit(h[3])}, LISTINOORIGINALE={lit(h[4])}, "
        f"SCONTATO={lit(h[5])} WHERE ID_CONTRATTITESTATA={CT};"
    )
    restore.append(
        f"DELETE FROM CONTRATTISPESE WHERE ID_CONTRATTIRIGHE={FIRST_PAID_LINE} AND DESCRIZIONE='Production';"
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
    rpath = Path("logs") / f"sacrt-{CT}-revision-restore-{date.today():%Y%m%d}.sql"
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
    print(f"[LINES] {len(p)} lines start {NEW_START}, caps {[x['new_cap'] for x in p]}")

    cur.execute(f"DELETE FROM ContrattiImportiGiornalieri WHERE ID_ContrattiRighe IN ({IDS})")
    for x in p:
        if x["total"] == 0:
            continue  # Etere wrote no day-revenue rows for the $0 bonus lines; keep that shape
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
        "INSERT INTO CONTRATTISPESE (ID_CONTRATTIRIGHE, DESCRIZIONE, DATA, IMPORTO, VALUTA, "
        "ID_FATTURAEMITTENTE, ID_FATTURAAGENZIA, PRINTDETAIL, ID_CPEMITTENTE, ID_CPAGENZIA) "
        "VALUES (%s, 'Production', %s, %s, '', 0, 0, 1, 0, 0)",
        (FIRST_PAID_LINE, NEW_START, PRODUCTION),
    )
    assert cur.rowcount == 1
    print(f"[CHARGE] Production ${PRODUCTION:,.2f} on line {FIRST_PAID_LINE} dated {NEW_START}")

    cur.execute(
        f"UPDATE CONTRATTITESTATA SET DATA_INIZIO=%s, DATA_ACQUISIZIONE=%s, LISTINO=%s, LISTINOORIGINALE=%s, "
        f"SCONTATO=%s WHERE ID_CONTRATTITESTATA={CT}",
        (NEW_START, NEW_START, NEW_HEADER_TOTAL, NEW_HEADER_TOTAL, NEW_HEADER_TOTAL),
    )
    assert cur.rowcount == 1
    print(f"[HEADER] start {NEW_START}, end {END} unchanged, total ${NEW_HEADER_TOTAL:,.2f}")

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
        f"MIN(DATA_FINE), MAX(DATA_FINE), MIN(DATEEND), MAX(DATEEND) FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA={CT}"
    )
    n, tot, s0, s1, fs0, fs1, e0, e1, fe0, fe1 = cur.fetchone()
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
        f"SELECT COUNT(*), ISNULL(SUM(IMPORTO),0), MIN(DATA), MAX(ID_CONTRATTIRIGHE), MAX(CAST(PRINTDETAIL AS int)) "
        f"FROM CONTRATTISPESE WHERE ID_CONTRATTIRIGHE IN ({IDS}) AND DESCRIZIONE='Production'"
    )
    chn, chg, chd, chl, chp = cur.fetchone()
    cur.execute(
        f"SELECT DATA_INIZIO, DATA_TERMINE, DATA_ACQUISIZIONE, LISTINO, LISTINOORIGINALE, SCONTATO "
        f"FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA={CT}"
    )
    h0, h1, ha, hl, hlo, hs = cur.fetchone()
    cur.execute(f"SELECT COUNT(*) FROM trafficPalinse WHERE ID_ContrattiRighe IN ({IDS})")
    placed = cur.fetchone()[0]
    print(
        f"[VERIFY {label}] lines={n} airtime=${float(tot):,.2f} starts {s0:%m/%d}..{s1:%m/%d} (form {fs0:%m/%d}..{fs1:%m/%d}) "
        f"ends {e0:%m/%d}..{e1:%m/%d} (form {fe0:%m/%d}..{fe1:%m/%d}) caps_cover_spots={caps_ok} | "
        f"importi ${float(cig):,.2f} in {cign} rows {c0:%m/%d}-{c1:%m/%d} | "
        f"charge {chn} row ${float(chg):,.2f} line {chl} {chd:%m/%d} print={chp} | "
        f"header {h0:%m/%d}-{h1:%m/%d} acq {ha:%m/%d} ${float(hl):,.2f}/${float(hlo):,.2f}/${float(hs):,.2f} | placed {placed}"
    )
    ok = (
        n == len(LINES)
        and abs(float(tot) - AIRTIME_TOTAL) < 0.005
        and s0.date() == s1.date() == fs0.date() == fs1.date() == NEW_START
        and e0.date() == e1.date() == fe0.date() == fe1.date() == END
        and caps_ok
        and abs(float(cig) - AIRTIME_TOTAL) < 0.005
        and c0.date() >= NEW_START
        and c1.date() <= END
        and chn == 1
        and abs(float(chg) - PRODUCTION) < 0.005
        and chl == FIRST_PAID_LINE
        and chd.date() == NEW_START
        and chp == 1
        and h0.date() == ha.date() == NEW_START
        and h1.date() == END
        and all(abs(float(v) - NEW_HEADER_TOTAL) < 0.005 for v in (hl, hlo, hs))
        and placed == 0
    )
    if not ok:
        raise SystemExit(f"[VERIFY {label}] FAILED")
    print(f"[VERIFY {label}] OK")


if __name__ == "__main__":
    main("--apply" in sys.argv)
