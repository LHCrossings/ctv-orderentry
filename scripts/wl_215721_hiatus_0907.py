"""One-off: bring contract 2916 (WL Marketing 215721, 4imprint :30) in line with WorldLink's
revised IO "CROSSINGS 4IMP30 9.7.pdf" (prepared 9/2/2026, Labor Day hiatus).

IO vs Etere (NYC line carries the rate, 8 market lines at $0 — the WL Crossings convention):
  Line 9  CHANGE  M-F  7a-4p   8/31-9/6  5 spots  $4   (Etere: 8/31-9/25, 20 spots)
  Line 10 CHANGE  M-Su 8p-11p  8/31-9/6 14 spots  $5   (Etere: 8/31-9/27, 56 spots)
  Line 12 ADD     Tu-F 7a-4p   9/8-9/13  5 spots  $4
  Line 13 ADD     Tu-Su 8p-11p 9/8-9/13 14 spots  $5
  Line 14 ADD     M-F  7a-4p   9/14-9/27 10 spots (5/wk)  $4
  Line 15 ADD     M-Su 8p-11p  9/14-9/27 28 spots (14/wk) $5
  Line 11 Sa-Su 7a-8p unchanged.  IO contract total $1,950.00.

Lee clears the placed spots in Etere first and reschedules afterwards; this script only edits
the ORDER. Mirrors worldlink_automation's CHANGE path (_update_change_lines + _rewrite_change_cig
+ _refresh_header_totals) and its ADD path (_add_crossings_lines_direct).

    uv run python3 scripts/wl_215721_hiatus_0907.py            # dry run: plan + preconditions
    uv run python3 scripts/wl_215721_hiatus_0907.py --apply    # one transaction, readback, commit
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# etere_direct_client does a bare `import etere_client` inside add_contract_line.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "browser_automation"))
from browser_automation.etere_direct_client import (  # noqa: E402
    EtereDirectClient,
    connect,
    parse_day_bits,
)

# Same values/logic as worldlink_automation (not imported: that module pulls in Selenium).
CROSSINGS_ZERO_MARKETS = ["CMP", "HOU", "SFO", "SEA", "LAX", "CVC", "WDC", "MMT"]


def _refresh_header_totals(conn, ph: str, contract_id: int) -> None:
    """Recompute header list totals from the lines (raw CHANGE updates leave them stale)."""
    cur = conn.cursor()
    cur.execute(
        f"SELECT COALESCE(SUM(N_PASSAGGI * IMPORTO), 0) FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA = {ph}",
        (contract_id,),
    )
    total = float(cur.fetchone()[0] or 0)
    cur.execute(
        f"UPDATE CONTRATTITESTATA SET LISTINO = {ph}, SCONTATO = {ph}, LISTINOORIGINALE = {ph} "
        f"WHERE ID_CONTRATTITESTATA = {ph}",
        (total, total, total, contract_id),
    )


CONTRACT_ID = 2916
CONTRACT_CODE = "WL Marketing 215721"
IO_TOTAL = 1950.00
SEPARATION = (20, 0, 0)  # Interv_Committente 35964 frames = 20 min on the existing lines
NEW_END = date(2026, 9, 6)

CHANGES = {  # description prefix -> (new total spots)
    "(Line 9) ": 5,
    "(Line 10) ": 14,
}
ADDS = [  # (line no, days, from, to, desc-short, spots/wk, total, rate, start, end)
    (12, "Tu-F", "07:00", "16:00", "7a-4p", 5, 5, 4.0, date(2026, 9, 8), date(2026, 9, 13)),
    (13, "Tu-Su", "20:00", "23:00", "8p-11p", 14, 14, 5.0, date(2026, 9, 8), date(2026, 9, 13)),
    (14, "M-F", "07:00", "16:00", "7a-4p", 5, 10, 4.0, date(2026, 9, 14), date(2026, 9, 27)),
    (15, "M-Su", "20:00", "23:00", "8p-11p", 14, 28, 5.0, date(2026, 9, 14), date(2026, 9, 27)),
]
DAY_KEYS = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]


def rows_for(cur, prefix):
    cur.execute(
        "SELECT ID_CONTRATTIRIGHE, COD_USER, DATA_INIZIO, DATA_FINE, N_PASSAGGI, IMPORTO, ROWSTATUS, "
        "LUNEDI, MARTEDI, MERCOLEDI, GIOVEDI, VENERDI, SABATO, DOMENICA, RTRIM(DESCRIZIONE) "
        "FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA = %s AND DESCRIZIONE LIKE %s ORDER BY COD_USER",
        (CONTRACT_ID, prefix + "%"),
    )
    return cur.fetchall()


def placed(cur, line_ids, future_only):
    """Placed spots on these lines; future_only = dated AFTER the IO's new end (9/6)."""
    extra = f" AND t.DATA > '{NEW_END.isoformat()}'" if future_only else ""
    cur.execute(
        f"SELECT COUNT(*) FROM trafficPalinse tp JOIN TPALINSE t ON t.ID_TPALINSE = tp.id_tpalinse "
        f"WHERE tp.ID_ContrattiRighe IN ({','.join('%s' for _ in line_ids)}){extra}",
        tuple(line_ids),
    )
    return cur.fetchone()[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    conn = connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT RTRIM(COD_CONTRATTO), RTRIM(CUSTOMERREF) FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = %s",
        (CONTRACT_ID,),
    )
    code, ref = cur.fetchone()
    assert code == CONTRACT_CODE and ref == "215721", (code, ref)

    ok = True
    restore = [f"-- restore for {CONTRACT_CODE} hiatus edit ({date.today()})"]
    for prefix, new_total in CHANGES.items():
        rows = rows_for(cur, prefix)
        assert len(rows) == 9, (prefix, len(rows))
        ids = [r[0] for r in rows]
        fut = placed(cur, ids, future_only=True)
        tot = placed(cur, ids, future_only=False)
        print(
            f"{prefix.strip():<10} 9 lines | placed spots: {tot} total, {fut} on future dates | "
            f"end {rows[0][3].date()} -> {NEW_END}, spots {rows[0][4]} -> {new_total}"
        )
        if fut:
            ok = False
            print(
                "   PRECONDITION: spots dated after 9/6 still placed on this line — clear them first."
            )
        for r in rows:
            restore.append(
                f"UPDATE CONTRATTIRIGHE SET DATA_FINE='{r[3]:%Y-%m-%d}', DATEEND='{r[3]:%Y-%m-%d}', "
                f"N_PASSAGGI={r[4]}, ROWSTATUS={int(r[6])} WHERE ID_CONTRATTIRIGHE={r[0]};"
            )
    existing_adds = [rows_for(cur, f"(Line {n}) ") for n, *_ in ADDS]
    for (n, *_), found in zip(ADDS, existing_adds):
        if found:
            ok = False
            print(
                f"   PRECONDITION: (Line {n}) already exists on the contract ({len(found)} rows)."
            )
    cur.execute(
        "SELECT COUNT(*) FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA = %s", (CONTRACT_ID,)
    )
    n_before = cur.fetchone()[0]
    print(f"lines on contract before: {n_before}; will add {len(ADDS) * 9}")

    if not a.apply:
        print("\nDRY RUN — nothing written." + ("" if ok else " Preconditions NOT met."))
        return 0 if ok else 1
    if not ok:
        print("\nPreconditions not met — refusing to apply.")
        return 1

    Path("logs").mkdir(exist_ok=True)
    restore_path = Path("logs") / f"wl-215721-hiatus-restore-{date.today():%Y%m%d}.sql"

    try:
        # 1. CHANGE lines 9 and 10: end date + total; Ready so the scheduler can refill.
        for prefix, new_total in CHANGES.items():
            rows = rows_for(cur, prefix)
            for r in rows:
                # ROWSTATUS 1 = nothing left to place, 0 = Ready for the scheduler (automation rule).
                rowstatus = 1 if placed(cur, [r[0]], future_only=False) >= new_total else 0
                cur.execute(
                    "UPDATE CONTRATTIRIGHE SET DATA_FINE = %s, DATEEND = %s, N_PASSAGGI = %s, ROWSTATUS = %s "
                    "WHERE ID_CONTRATTIRIGHE = %s",
                    (NEW_END.isoformat(), NEW_END.isoformat(), new_total, rowstatus, r[0]),
                )
                assert cur.rowcount == 1
                # CIG: only the priced (NYC) row carries daily amounts.
                cur.execute(
                    "DELETE FROM ContrattiImportiGiornalieri WHERE ID_ContrattiRighe = %s", (r[0],)
                )
                total = new_total * float(r[5] or 0)
                if total > 0:
                    bits = parse_day_bits(rows[0][14].split(") ", 1)[1].split(" ")[0])
                    days, d = [], r[2].date()
                    while d <= NEW_END:
                        if bits[DAY_KEYS[d.weekday()]]:
                            days.append(d)
                        d += timedelta(days=1)
                    per = round(total / len(days), 4)
                    amts = [per] * (len(days) - 1) + [round(total - per * (len(days) - 1), 4)]
                    for d, amt in zip(days, amts):
                        cur.execute(
                            "INSERT INTO ContrattiImportiGiornalieri (ID_ContrattiRighe, DATA, IMPORTO) VALUES (%s, %s, %s)",
                            (r[0], d.isoformat(), amt),
                        )
                    print(
                        f"   CIG {prefix.strip()} NYC: {len(days)} days x ${per:.2f} = ${total:.2f}"
                    )

        # 2. ADD lines 12-15, NYC at rate + 8 markets at $0 (same shape as the automation).
        client = EtereDirectClient(conn, owner="Lee Hudson", autocommit=False)
        new_ids = []
        for n, days, t_from, t_to, short, spw, total, rate, d0, d1 in ADDS:
            desc = f"(Line {n}) {days} {short}"
            for mkt in ["NYC", *CROSSINGS_ZERO_MARKETS]:
                lid = client.add_contract_line(
                    market=mkt,
                    days=days,
                    time_range=f"{t_from}-{t_to}",
                    description=desc,
                    rate=rate if mkt == "NYC" else 0.0,
                    total_spots=total,
                    spots_per_week=spw,
                    date_from=d0,
                    date_to=d1,
                    duration="00:00:30:00",
                    is_bonus=False,
                    booking_code=2,
                    separation_intervals=SEPARATION,
                    scheduling_type=0,
                    row_status=0,
                    contract_id=CONTRACT_ID,
                    language="E",
                )
                new_ids.append(lid)
            print(f"   ADD {desc}: 9 lines, NYC ${rate:.2f}, {total} spots")
        _refresh_header_totals(conn, "%s", CONTRACT_ID)

        # Line 11 lost its 9/7+ spots in the unschedule too: mark every under-placed line Ready.
        cur.execute(
            "UPDATE r SET r.ROWSTATUS = 0 FROM CONTRATTIRIGHE r WHERE r.ID_CONTRATTITESTATA = %s "
            "AND r.ROWSTATUS = 1 AND r.N_PASSAGGI > "
            "(SELECT COUNT(*) FROM trafficPalinse tp WHERE tp.ID_ContrattiRighe = r.ID_CONTRATTIRIGHE)",
            (CONTRACT_ID,),
        )
        print(f"   lines flipped to Ready (under-placed): {cur.rowcount}")

        # 3. Readback.
        cur.execute(
            "SELECT COUNT(*), SUM(CASE WHEN COD_USER = 1 THEN N_PASSAGGI * IMPORTO ELSE 0 END), "
            "SUM(CASE WHEN COD_USER <> 1 AND IMPORTO <> 0 THEN 1 ELSE 0 END) "
            "FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA = %s",
            (CONTRACT_ID,),
        )
        n_after, nyc_total, bad_market_rates = cur.fetchone()
        nyc_total = float(nyc_total or 0)
        checks = {
            "line count": n_after == n_before + len(ADDS) * 9,
            "NYC contract total == IO $1,950.00": abs(nyc_total - IO_TOTAL) < 0.005,
            "no priced non-NYC line": bad_market_rates == 0,
            "new line ids": len(new_ids) == len(ADDS) * 9 and all(new_ids),
        }
        for prefix, new_total in CHANGES.items():
            rows = rows_for(cur, prefix)
            checks[f"{prefix.strip()} end/total"] = all(
                r[3].date() == NEW_END and r[4] == new_total for r in rows
            )
        for k, v in checks.items():
            print(f"   check {k}: {'OK' if v else 'FAIL'}")
        print(f"   NYC total now ${nyc_total:,.2f}; lines {n_before} -> {n_after}")
        if not all(checks.values()):
            raise RuntimeError("readback failed")
        restore.append(
            f"DELETE FROM ContrattiImportiGiornalieri WHERE ID_ContrattiRighe IN ({','.join(map(str, new_ids))});"
        )
        restore.append(
            f"DELETE FROM contrattifasce WHERE id_contrattirighe IN ({','.join(map(str, new_ids))});"
        )
        restore.append(
            f"DELETE FROM CONTRATTIRIGHE WHERE ID_CONTRATTIRIGHE IN ({','.join(map(str, new_ids))});"
        )
        restore.append(
            "-- then re-run _rewrite_change_cig for lines 9/10 and _refresh_header_totals(2916)"
        )
        restore_path.write_text("\n".join(restore) + "\n")
        conn.commit()
        print(f"\nCOMMITTED. Restore SQL: {restore_path}")
        return 0
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        print(f"\nROLLED BACK: {e}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
