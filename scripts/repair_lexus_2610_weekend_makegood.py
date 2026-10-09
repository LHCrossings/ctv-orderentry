"""Lexus 26Q4 (IW Lexus 202 NYC 2740, 208 SFO 2742, 210 SFO 2747): pull Fri 10/9 - Sun 10/11,
make good the week of 10/12 - 10/18. Lee / Melissa (IW Group), 2026-10-09.

No traffic arrived for the 10/9 start. Melissa: "remove everything from Friday through Sunday,
and make it good next week." Lee unschedules 10/9-10/11 on the three contracts in the Etere app
first (Etere's own full unschedule, no ghost risk); this script then revises the lines.

28 spots were placed on the weekend, all Idle, none with a creative. Two line shapes:
  * WHOLE-WEEKEND lines (9 lines, 13 spots; DATA_FINE = 10/11): the line simply MOVES to
    10/12-10/18, counts/caps unchanged, description prefixed "MG ".
  * SPANNING lines (12 lines, 15 spots; 10/9-10/25 or 10/31, weekly counts): the line is TRIMMED
    to start 10/12 with N_PASSAGGI reduced by the weekend spots (description unchanged), and a
    new "MG <desc>" line is ADDED for 10/12-10/18 carrying those spots — same market, days,
    window, rate, duration, cap, separation, scheduling type, priority, booking code.
Lee's convention: a line that is rescheduled or added as a make-good carries "MG" in its
description; a line that is only trimmed keeps its description.

Day revenue (ContrattiImportiGiornalieri) is rebuilt for every moved/trimmed line and written
for every MG line (even spread over the line's active days, the shape Etere wrote). Contract
money is unchanged, so headers (dates, LISTINO) are untouched and verified so.

Dry run by default (ROLLBACK); --apply commits and re-verifies from a fresh connection.
A restore .sql is written to logs/ (MG line ids are known only on --apply).
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_automation.etere_direct_client import (  # noqa: E402
    EtereDirectClient,
    _frames_to_hhmm,
    connect,
)

FPS = 29.97
CONTRACTS = {
    2740: "IW Lexus 202 NYC 2610",
    2742: "IW Lexus 208 SFO 2610",
    2747: "IW Lexus 210 SFO 2610",
}
MARKET_BY_USER = {1: "NYC", 4: "SFO"}
PULL_FROM, PULL_TO = date(2026, 10, 9), date(2026, 10, 11)
MG_FROM, MG_TO = date(2026, 10, 12), date(2026, 10, 18)
# weekend spots per line, captured from trafficPalinse before Lee's app unschedule (28 total)
REMOVED = {
    76393: 1, 76382: 1, 76413: 1, 76403: 1, 76424: 2, 76431: 1,                     # 2740 (7)
    85061: 1, 85069: 1, 85076: 1, 85096: 2, 85083: 2, 85087: 1, 85090: 2, 85093: 1,  # 2742 (11)
    85104: 1, 85111: 1, 85118: 1, 85135: 3, 85125: 2, 85128: 1, 85132: 1,           # 2747 (10)
}  # fmt: skip
assert sum(REMOVED.values()) == 28
IDS = ",".join(map(str, REMOVED))
CT_IDS = ",".join(map(str, CONTRACTS))
DAY_COLS = ["LUNEDI", "MARTEDI", "MERCOLEDI", "GIOVEDI", "VENERDI", "SABATO", "DOMENICA"]


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


def days_token(bits) -> str:
    """Day-pattern string parse_day_bits understands, from the 7 day flags."""
    names = ["M", "Tu", "W", "Th", "F", "Sa", "Su"]
    on = [i for i, b in enumerate(bits) if b]
    if on == list(range(7)):
        return "M-Su"
    if on == list(range(5)):
        return "M-F"
    if on == [5, 6]:
        return "Sa-Su"
    if len(on) == 1:
        return names[on[0]]
    raise ValueError(f"unexpected day pattern {bits}")


def load_lines(cur) -> dict[int, dict]:
    cur.execute(
        f"SELECT ID_CONTRATTIRIGHE, ID_CONTRATTITESTATA, DESCRIZIONE, COD_USER, DATA_INIZIO, DATA_FINE, DATESTART, DATEEND, "
        f"N_PASSAGGI, PASSAGGI_SETTIMANALI, PASSAGGI_GIORNALIERI, IMPORTO, DURATA, ID_BOOKINGCODE, PRENOTAZIONE, PRIORITA, "
        f"PrioritaWhiteList, ROWSTATUS, ORA_INIZIO, ORA_FINE, Interv_Committente, INTERVALLO, INTERV_CONTRATTO, {','.join(DAY_COLS)} "
        f"FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA IN ({CT_IDS}) ORDER BY ID_CONTRATTIRIGHE"
    )
    keys = [
        "id", "ct", "desc", "mkt", "s", "e", "fs", "fe", "n", "wk", "cap", "rate", "dur", "bk", "pren", "pri", "pwl",
        "rs", "lo", "hi", "ic", "io", "ie",
    ]  # fmt: skip
    out = {}
    for row in cur.fetchall():
        r = dict(zip(keys, row[: len(keys)]))
        r["bits"] = [bool(b) for b in row[len(keys) :]]
        for k in ("s", "e", "fs", "fe"):
            r[k] = r[k].date()
        out[r["id"]] = r
    return out


def write_cig(cur, lid: int, total: float, days: list[date]):
    cur.execute("DELETE FROM ContrattiImportiGiornalieri WHERE ID_ContrattiRighe=%s", (lid,))
    if total == 0 or not days:
        return  # Etere writes no day-revenue rows for $0 bonus lines
    per = round(total / len(days), 4)
    amts = [per] * (len(days) - 1) + [round(total - per * (len(days) - 1), 4)]
    for d, a in zip(days, amts):
        cur.execute(
            "INSERT INTO ContrattiImportiGiornalieri (ID_ContrattiRighe, DATA, IMPORTO) VALUES (%s,%s,%s)",
            (lid, d, a),
        )


def main(apply: bool, rehearse: bool = False):
    """rehearse=True: dry run BEFORE the app unschedule (skips the 0-placement check); never commits."""
    conn = connect()
    cur = conn.cursor()
    client = EtereDirectClient(conn, autocommit=False)

    # ── preconditions ───────────────────────────────────────────────────────────
    lines = load_lines(cur)
    missing = [i for i in REMOVED if i not in lines]
    assert not missing, f"lines not on these contracts: {missing}"
    cur.execute(
        f"SELECT COUNT(*) FROM trafficPalinse tp JOIN CONTRATTIRIGHE r ON r.ID_CONTRATTIRIGHE=tp.ID_ContrattiRighe "
        f"WHERE r.ID_CONTRATTITESTATA IN ({CT_IDS}) AND tp.Date BETWEEN %s AND %s",
        (PULL_FROM, PULL_TO),
    )
    still = cur.fetchone()[0]
    if rehearse:
        print(f"[REHEARSE] {still} weekend placement(s) still on — rolling back no matter what")
        apply = False
    else:
        assert still == 0, (
            f"{still} weekend placement(s) still on the Lexus contracts — unschedule 10/9-10/11 in the app first"
        )
    cur.execute(f"SELECT COUNT(*) FROM Traffic_ScheduleList WHERE ID_ContrattiRighe IN ({IDS})")
    assert cur.fetchone()[0] == 0, "blacklist rows exist on the touched lines"
    cur.execute(
        f"SELECT ID_CONTRATTITESTATA, LISTINO, DATA_INIZIO, DATA_TERMINE FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA IN ({CT_IDS})"
    )
    headers_before = {r[0]: (float(r[1]), r[2], r[3]) for r in cur.fetchall()}
    cur.execute(
        f"SELECT r.ID_CONTRATTITESTATA, ISNULL(SUM(c.IMPORTO),0) FROM CONTRATTIRIGHE r "
        f"LEFT JOIN ContrattiImportiGiornalieri c ON c.ID_ContrattiRighe=r.ID_CONTRATTIRIGHE "
        f"WHERE r.ID_CONTRATTITESTATA IN ({CT_IDS}) GROUP BY r.ID_CONTRATTITESTATA"
    )
    cig_before = {r[0]: float(r[1]) for r in cur.fetchall()}
    money_before = {
        ct: sum(int(r["n"]) * float(r["rate"]) for r in lines.values() if r["ct"] == ct)
        for ct in CONTRACTS
    }

    moves, splits = [], []
    for lid, removed in REMOVED.items():
        r = lines[lid]
        assert r["s"] == PULL_FROM and r["fs"] == PULL_FROM, (
            f"line {lid} start {r['s']}/{r['fs']} != 10/9"
        )
        assert int(r["io"]) == 0 and int(r["ie"]) == 0, f"line {lid} order/event interval not 0"
        if r["e"] == PULL_TO:
            assert int(r["n"]) == removed, (
                f"line {lid}: whole-weekend line holds {r['n']} spots, {removed} were placed"
            )
            moves.append(r)
        else:
            assert int(r["n"]) > removed, f"line {lid}: {r['n']} spots, {removed} removed"
            splits.append(r)
    assert len(moves) == 9 and len(splits) == 12
    print(
        f"[PRE] 3 contracts, 0 weekend placements, {len(moves)} lines to move, {len(splits)} lines to trim + MG"
    )

    # ── restore file (part 1: everything that exists before the write) ──────────
    restore = [
        f"-- restore for Lexus 2740/2742/2747 weekend make-good, generated {date.today()} — run as one batch",
        "BEGIN TRAN;",
    ]
    for lid in REMOVED:
        r = lines[lid]
        restore.append(
            f"UPDATE CONTRATTIRIGHE SET DESCRIZIONE={lit(r['desc'])}, DATA_INIZIO={lit(r['s'])}, DATA_FINE={lit(r['e'])}, "
            f"DATESTART={lit(r['fs'])}, DATEEND={lit(r['fe'])}, N_PASSAGGI={int(r['n'])} WHERE ID_CONTRATTIRIGHE={lid};"
        )
    cur.execute(f"SELECT * FROM ContrattiImportiGiornalieri WHERE ID_ContrattiRighe IN ({IDS})")
    cig_rows = cur.fetchall()
    cig_cols = [d[0] for d in cur.description]
    restore.append(f"DELETE FROM ContrattiImportiGiornalieri WHERE ID_ContrattiRighe IN ({IDS});")
    for row in cig_rows:
        restore.append(
            f"INSERT INTO ContrattiImportiGiornalieri ({','.join(cig_cols)}) VALUES ({','.join(lit(v) for v in row)});"
        )

    # ── writes (one transaction) ────────────────────────────────────────────────
    touched = {}  # lid -> (expected n, expected desc, expected start, expected end, total)
    for r in moves:
        desc = "MG " + r["desc"]
        cur.execute(
            "UPDATE CONTRATTIRIGHE SET DESCRIZIONE=%s, DATA_INIZIO=%s, DATESTART=%s, DATA_FINE=%s, DATEEND=%s "
            "WHERE ID_CONTRATTIRIGHE=%s",
            (desc, MG_FROM, MG_FROM, MG_TO, MG_TO, r["id"]),
        )
        assert cur.rowcount == 1
        total = int(r["n"]) * float(r["rate"])
        write_cig(cur, r["id"], total, active_dates(MG_FROM, MG_TO, r["bits"]))
        touched[r["id"]] = (int(r["n"]), desc, MG_FROM, MG_TO, total)
        print(
            f"[MOVE] {r['ct']} {r['id']} {desc:<38} {r['n']} spots -> {MG_FROM:%m/%d}-{MG_TO:%m/%d}"
        )

    new_ids = []
    for r in splits:
        removed = REMOVED[r["id"]]
        new_n = int(r["n"]) - removed
        cur.execute(
            "UPDATE CONTRATTIRIGHE SET DATA_INIZIO=%s, DATESTART=%s, N_PASSAGGI=%s WHERE ID_CONTRATTIRIGHE=%s",
            (MG_FROM, MG_FROM, new_n, r["id"]),
        )
        assert cur.rowcount == 1
        total = new_n * float(r["rate"])
        write_cig(cur, r["id"], total, active_dates(MG_FROM, r["e"], r["bits"]))
        touched[r["id"]] = (new_n, r["desc"], MG_FROM, r["e"], total)
        print(
            f"[TRIM] {r['ct']} {r['id']} {r['desc']:<38} {r['n']} -> {new_n} spots, start {MG_FROM:%m/%d}, end {r['e']:%m/%d}"
        )

        mg_days = active_dates(MG_FROM, MG_TO, r["bits"])
        cap = int(r["cap"])
        assert cap * len(mg_days) >= removed, (
            f"line {r['id']}: cap {cap} x {len(mg_days)} days < {removed} MG spots"
        )
        desc = "MG " + r["desc"]
        is_bonus = int(r["bk"]) == 10
        new_id = client.add_contract_line(
            contract_id=r["ct"],
            market=MARKET_BY_USER[int(r["mkt"])],
            days=days_token(r["bits"]),
            time_range=f"{_frames_to_hhmm(int(r['lo']))}-{_frames_to_hhmm(int(r['hi']))}",
            description=desc,
            rate=float(r["rate"]),
            total_spots=removed,
            spots_per_week=removed,
            max_daily_run=cap,
            date_from=MG_FROM,
            date_to=MG_TO,
            duration=f"00:00:{int(r['dur']) // 30:02d}:00",
            is_bonus=is_bonus,
            booking_code=int(r["bk"]),
            separation_intervals=(round(int(r["ic"]) / 60 / FPS), 0, 0),
            scheduling_type=int(r["pren"]),
            priority=int(r["pri"]),
            whitelist_priority=int(r["pwl"]),
            row_status=int(r["rs"]),
        )
        assert new_id and new_id > 0, f"add_contract_line failed for MG of {r['id']}"
        new_ids.append((new_id, r))
        mg_total = removed * float(r["rate"])
        write_cig(cur, new_id, mg_total, mg_days)
        touched[new_id] = (removed, desc, MG_FROM, MG_TO, mg_total)
        print(
            f"[ADD ] {r['ct']} {new_id} {desc:<38} {removed} spots {MG_FROM:%m/%d}-{MG_TO:%m/%d} cap {cap}/day (from {r['id']})"
        )

    # MG lines must be exact copies of their source apart from dates/counts/description
    after = load_lines(cur)
    for new_id, src in new_ids:
        a = after[new_id]

        def norm(k, v):
            if k == "rate":
                return round(float(v), 4)
            if (
                k == "ic"
            ):  # separation: compare in minutes — the old web form wrote 44956, we write 44955
                return round(int(v) / 60 / FPS)
            return v

        same = [
            "mkt",
            "rate",
            "dur",
            "bk",
            "pren",
            "pri",
            "pwl",
            "rs",
            "lo",
            "hi",
            "ic",
            "io",
            "ie",
            "bits",
            "cap",
        ]
        diffs = {k: (src[k], a[k]) for k in same if norm(k, src[k]) != norm(k, a[k])}
        assert not diffs, f"MG line {new_id} differs from source {src['id']}: {diffs}"
        cur.execute("SELECT COUNT(*) FROM contrattifasce WHERE id_contrattirighe=%s", (new_id,))
        nb = cur.fetchone()[0]
        assert nb > 0, f"MG line {new_id} got no blocks"

    # restore part 2: the MG lines (ids exist only on --apply)
    for new_id, _ in new_ids:
        restore.append(f"DELETE FROM ContrattiImportiGiornalieri WHERE ID_ContrattiRighe={new_id};")
        restore.append(f"DELETE FROM contrattifasce WHERE id_contrattirighe={new_id};")
        restore.append(f"DELETE FROM CONTRATTIRIGHE WHERE ID_CONTRATTIRIGHE={new_id};")
    restore.append("COMMIT;")
    rpath = (
        Path("logs")
        / f"lexus-2610-weekend-makegood-restore-{date.today():%Y%m%d}{'' if apply else '-dryrun'}.sql"
    )
    rpath.parent.mkdir(exist_ok=True)
    rpath.write_text("\n".join(restore) + "\n")
    print(f"[BACKUP] {rpath} ({len(cig_rows)} day-revenue rows)")

    verify(cur, "in-txn", touched, headers_before, cig_before, money_before, rehearse)
    if apply:
        conn.commit()
        print("[COMMIT] committed")
    else:
        conn.rollback()
        print("[DRY RUN] rolled back — rerun with --apply")
    conn.close()
    if apply:
        c2 = connect()
        verify(c2.cursor(), "fresh connection", touched, headers_before, cig_before, money_before)
        c2.close()


def verify(cur, label, touched, headers_before, cig_before, money_before, rehearse=False):
    lines = load_lines(cur)
    bad = []
    for lid, (n, desc, s, e, total) in touched.items():
        r = lines.get(lid)
        if r is None:
            bad.append((lid, "missing"))
            continue
        if (
            int(r["n"]) != n
            or r["desc"] != desc
            or r["s"] != s
            or r["fs"] != s
            or r["e"] != e
            or r["fe"] != e
        ):
            bad.append(
                (lid, (r["n"], r["desc"], r["s"], r["fs"], r["e"], r["fe"]), (n, desc, s, e))
            )
        cur.execute(
            "SELECT ISNULL(SUM(IMPORTO),0), MIN(DATA), MAX(DATA) FROM ContrattiImportiGiornalieri WHERE ID_ContrattiRighe=%s",
            (lid,),
        )
        cig, c0, c1 = cur.fetchone()
        if abs(float(cig) - total) > 0.005 or (c0 and (c0.date() < s or c1.date() > e)):
            bad.append((lid, "cig", float(cig), total, c0, c1))
    # every moved/added line carries MG; trimmed lines keep their description (desc equality above covers both)
    mg_ok = all(
        lines[lid]["desc"].startswith("MG ")
        for lid, t in touched.items()
        if t[1].startswith("MG ") and lid in lines
    )
    cur.execute(
        f"SELECT ID_CONTRATTITESTATA, LISTINO, DATA_INIZIO, DATA_TERMINE FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA IN ({CT_IDS})"
    )
    headers = {r[0]: (float(r[1]), r[2], r[3]) for r in cur.fetchall()}
    cur.execute(
        f"SELECT r.ID_CONTRATTITESTATA, ISNULL(SUM(c.IMPORTO),0) FROM CONTRATTIRIGHE r "
        f"LEFT JOIN ContrattiImportiGiornalieri c ON c.ID_ContrattiRighe=r.ID_CONTRATTIRIGHE "
        f"WHERE r.ID_CONTRATTITESTATA IN ({CT_IDS}) GROUP BY r.ID_CONTRATTITESTATA"
    )
    cig_now = {r[0]: float(r[1]) for r in cur.fetchall()}
    money_now = {
        ct: sum(int(r["n"]) * float(r["rate"]) for r in lines.values() if r["ct"] == ct)
        for ct in CONTRACTS
    }
    cur.execute(
        f"SELECT COUNT(*) FROM trafficPalinse tp JOIN CONTRATTIRIGHE r ON r.ID_CONTRATTIRIGHE=tp.ID_ContrattiRighe "
        f"WHERE r.ID_CONTRATTITESTATA IN ({CT_IDS}) AND tp.Date BETWEEN %s AND %s",
        (PULL_FROM, PULL_TO),
    )
    weekend = cur.fetchone()[0]
    for ct, code in CONTRACTS.items():
        print(
            f"[VERIFY {label}] {code}: lines ${money_now[ct]:,.2f} (was ${money_before[ct]:,.2f}) | importi ${cig_now[ct]:,.2f} "
            f"(was ${cig_before[ct]:,.2f}) | header ${headers[ct][0]:,.2f} {headers[ct][1]:%m/%d}-{headers[ct][2]:%m/%d}"
        )
    ok = (
        not bad
        and mg_ok
        and headers == headers_before
        and all(abs(money_now[ct] - money_before[ct]) < 0.005 for ct in CONTRACTS)
        and all(abs(cig_now[ct] - cig_before[ct]) < 0.01 for ct in CONTRACTS)
        and (weekend == 0 or rehearse)
    )
    if not ok:
        raise SystemExit(f"[VERIFY {label}] FAILED bad={bad} mg_ok={mg_ok} weekend={weekend}")
    print(
        f"[VERIFY {label}] OK — {len(touched)} lines, MG on every moved/added line, money and headers unchanged"
    )


if __name__ == "__main__":
    main("--apply" in sys.argv, rehearse="--rehearse" in sys.argv)
