"""Trim Polaris ASC 261006 (contract 3153) to what actually ran — Lee, 2026-10-08.

Maija pulled the evening spots in the Etere app and end-dated the order 10/7; spot counts
already match placements ($1,116). What the app left stale: every line and the header still
end 10/12, the two zero-spot Sa-Su lines are fluff, and ContrattiImportiGiornalieri (the
day-accurate revenue the Booked Business report reads) still carries the original $4,276.

Dry run by default (ROLLBACK); --apply commits. A restore .sql is written to logs/ first.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_automation.etere_direct_client import connect  # noqa: E402

CT = 3153
KEEP = [85395, 85396, 85397, 85398, 85399]
DROP = [85400, 85401]
NEW_END = date(2026, 10, 7)
EXPECTED_TOTAL = 1116.0
DAY_COLS = ["LUNEDI", "MARTEDI", "MERCOLEDI", "GIOVEDI", "VENERDI", "SABATO", "DOMENICA"]
CHILD = [
    ("CONTRATTIFASCE", "id_contrattirighe"),
    ("CONTRATTIFILMATI", "ID_CONTRATTIRIGHE"),
    ("ContrattiImportiGiornalieri", "ID_ContrattiRighe"),
    ("CTV_LineLanguage", "ID_CONTRATTIRIGHE"),
]


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


def insert_sql(cur, table, col, ids):
    cur.execute(f"SELECT * FROM {table} WHERE {col} IN ({','.join(map(str, ids))})")
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    out = []
    for r in rows:
        out.append(
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join(lit(v) for v in r)});"
        )
    return out, len(rows)


def main(apply: bool):
    conn = connect()
    cur = conn.cursor()
    all_ids = KEEP + DROP

    # ── preconditions ───────────────────────────────────────────────────────────
    cur.execute(
        f"SELECT ID_CONTRATTIRIGHE, N_PASSAGGI, IMPORTO FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA={CT}"
    )
    lines = {r[0]: (int(r[1]), float(r[2])) for r in cur.fetchall()}
    assert set(lines) == set(all_ids), f"line set changed: {sorted(lines)}"
    assert all(lines[i][0] == 0 for i in DROP), "a line to drop has spots"
    cur.execute(
        f"SELECT ID_ContrattiRighe, COUNT(*) FROM trafficPalinse WHERE ID_ContrattiRighe IN ({','.join(map(str, all_ids))}) GROUP BY ID_ContrattiRighe"
    )
    placed = dict(cur.fetchall())
    for i in KEEP:
        assert placed.get(i, 0) == lines[i][0], (
            f"line {i}: placed {placed.get(i)} != N_PASSAGGI {lines[i][0]}"
        )
    assert not any(i in placed for i in DROP), "a line to drop has placements"
    cur.execute(
        f"SELECT COUNT(*) FROM Traffic_ScheduleList WHERE ID_ContrattiRighe IN ({','.join(map(str, all_ids))})"
    )
    assert cur.fetchone()[0] == 0, "blacklist rows exist — accounting would change"
    total = sum(n * r for n, r in lines.values())
    assert abs(total - EXPECTED_TOTAL) < 0.005, f"line total {total} != {EXPECTED_TOTAL}"
    print(f"[PRE] 7 lines, placed == ordered on every kept line, no blacklist, total ${total:,.2f}")

    # ── restore file ────────────────────────────────────────────────────────────
    restore = [
        f"-- restore for contract {CT} trim, generated {date.today()} — run as one batch",
        "BEGIN TRAN;",
    ]
    cur.execute(
        f"SELECT ID_CONTRATTIRIGHE, DATA_INIZIO, DATA_FINE, DATESTART, DATEEND FROM CONTRATTIRIGHE WHERE ID_CONTRATTIRIGHE IN ({','.join(map(str, KEEP))})"
    )
    for r in cur.fetchall():
        restore.append(
            f"UPDATE CONTRATTIRIGHE SET DATA_INIZIO={lit(r[1])}, DATA_FINE={lit(r[2])}, DATESTART={lit(r[3])}, DATEEND={lit(r[4])} WHERE ID_CONTRATTIRIGHE={r[0]};"
        )
    cur.execute(
        f"SELECT DATA_INIZIO, DATA_TERMINE, DATA_ACQUISIZIONE FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA={CT}"
    )
    h = cur.fetchone()
    restore.append(
        f"UPDATE CONTRATTITESTATA SET DATA_INIZIO={lit(h[0])}, DATA_TERMINE={lit(h[1])}, DATA_ACQUISIZIONE={lit(h[2])} WHERE ID_CONTRATTITESTATA={CT};"
    )
    restore.append("SET IDENTITY_INSERT CONTRATTIRIGHE ON;")
    s, n = insert_sql(cur, "CONTRATTIRIGHE", "ID_CONTRATTIRIGHE", DROP)
    restore += s
    restore.append("SET IDENTITY_INSERT CONTRATTIRIGHE OFF;")
    for t, c in CHILD:
        ids = all_ids if t == "ContrattiImportiGiornalieri" else DROP
        s, n = insert_sql(cur, t, c, ids)
        cur.execute(
            "SELECT COUNT(*) FROM sys.identity_columns WHERE object_id = OBJECT_ID(%s)", (t,)
        )
        has_identity = cur.fetchone()[0] > 0
        if t == "ContrattiImportiGiornalieri":
            restore.append(f"DELETE FROM {t} WHERE {c} IN ({','.join(map(str, all_ids))});")
        if has_identity:
            restore.append(f"SET IDENTITY_INSERT {t} ON;")
        restore += s
        if has_identity:
            restore.append(f"SET IDENTITY_INSERT {t} OFF;")
        print(f"[BACKUP] {t}: {n} row(s){' (identity)' if has_identity else ''}")
    restore.append("COMMIT;")
    rpath = Path("logs") / f"asc-{CT}-trim-restore-{date.today():%Y%m%d}.sql"
    rpath.parent.mkdir(exist_ok=True)
    rpath.write_text("\n".join(restore) + "\n")
    print(f"[BACKUP] {rpath}")

    # ── writes (one transaction) ────────────────────────────────────────────────
    cur.execute(
        f"UPDATE CONTRATTIRIGHE SET DATA_FINE=%s, DATEEND=%s WHERE ID_CONTRATTIRIGHE IN ({','.join(map(str, KEEP))})",
        (NEW_END, NEW_END),
    )
    print(f"[LINES] {cur.rowcount} kept lines end-dated {NEW_END}")
    for t, c in CHILD:
        cur.execute(f"DELETE FROM {t} WHERE {c} IN ({','.join(map(str, DROP))})")
        print(f"[DROP] {t}: {cur.rowcount} row(s)")
    cur.execute(
        f"DELETE FROM CONTRATTIRIGHE WHERE ID_CONTRATTIRIGHE IN ({','.join(map(str, DROP))}) AND N_PASSAGGI=0"
    )
    assert cur.rowcount == len(DROP), f"deleted {cur.rowcount} lines, expected {len(DROP)}"
    print(f"[DROP] CONTRATTIRIGHE: {cur.rowcount} zero-spot Sa-Su line(s)")

    # day-revenue rebuild: line total spread evenly over the line's active weekdays (Etere's own shape)
    cur.execute(
        f"DELETE FROM ContrattiImportiGiornalieri WHERE ID_ContrattiRighe IN ({','.join(map(str, KEEP))})"
    )
    cur.execute(
        f"SELECT ID_CONTRATTIRIGHE, DATA_INIZIO, DATA_FINE, N_PASSAGGI, IMPORTO, {','.join(DAY_COLS)} FROM CONTRATTIRIGHE WHERE ID_CONTRATTIRIGHE IN ({','.join(map(str, KEEP))})"
    )
    for r in cur.fetchall():
        lid, d0, d1, n, rate = r[0], r[1].date(), r[2].date(), int(r[3]), float(r[4])
        bits = r[5:12]
        days, d = [], d0
        while d <= d1:
            if bits[d.weekday()]:
                days.append(d)
            d += timedelta(days=1)
        tot = n * rate
        per = round(tot / len(days), 4)
        amts = [per] * (len(days) - 1) + [round(tot - per * (len(days) - 1), 4)]
        for d, a in zip(days, amts):
            cur.execute(
                "INSERT INTO ContrattiImportiGiornalieri (ID_ContrattiRighe, DATA, IMPORTO) VALUES (%s,%s,%s)",
                (lid, d, a),
            )
        print(f"[IMPORTI] line {lid}: ${tot:,.2f} over {[str(x) for x in days]}")

    cur.execute(
        f"UPDATE CONTRATTITESTATA SET DATA_TERMINE=%s WHERE ID_CONTRATTITESTATA={CT}", (NEW_END,)
    )
    cur.execute(
        f"UPDATE CONTRATTITESTATA SET LISTINO=%s, SCONTATO=%s, LISTINOORIGINALE=%s WHERE ID_CONTRATTITESTATA={CT}",
        (EXPECTED_TOTAL,) * 3,
    )
    print(f"[HEADER] end {NEW_END}, totals ${EXPECTED_TOTAL:,.2f}")

    # ── verify inside the transaction ───────────────────────────────────────────
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
        f"SELECT COUNT(*), SUM(N_PASSAGGI*IMPORTO), MIN(DATA_INIZIO), MAX(DATA_FINE), MAX(DATEEND), MIN(N_PASSAGGI) FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA={CT}"
    )
    n, tot, d0, d1, de, minn = cur.fetchone()
    cur.execute(
        f"SELECT SUM(IMPORTO), COUNT(*), MIN(DATA), MAX(DATA) FROM ContrattiImportiGiornalieri WHERE ID_ContrattiRighe IN (SELECT ID_CONTRATTIRIGHE FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA={CT})"
    )
    cig, cign, c0, c1 = cur.fetchone()
    cur.execute(
        f"SELECT DATA_INIZIO, DATA_TERMINE, LISTINO FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA={CT}"
    )
    h0, h1, hl = cur.fetchone()
    cur.execute(
        f"SELECT COUNT(*) FROM trafficPalinse tp JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE=tp.ID_ContrattiRighe WHERE cr.ID_CONTRATTITESTATA={CT}"
    )
    placed = cur.fetchone()[0]
    print(
        f"[VERIFY {label}] lines={n} min_spots={minn} total=${float(tot):,.2f} line dates {d0:%m/%d}-{d1:%m/%d} (form end {de:%m/%d}) | "
        f"importi ${float(cig):,.2f} in {cign} rows {c0:%m/%d}-{c1:%m/%d} | header {h0:%m/%d}-{h1:%m/%d} ${float(hl):,.2f} | placed {placed}"
    )
    ok = (
        n == 5
        and minn > 0
        and abs(float(tot) - EXPECTED_TOTAL) < 0.005
        and abs(float(cig) - EXPECTED_TOTAL) < 0.005
        and d1.date() == NEW_END
        and de.date() == NEW_END
        and c1.date() <= NEW_END
        and h1.date() == NEW_END
        and placed == 7
    )
    if not ok:
        raise SystemExit(f"[VERIFY {label}] FAILED")
    print(f"[VERIFY {label}] OK")


if __name__ == "__main__":
    main("--apply" in sys.argv)
