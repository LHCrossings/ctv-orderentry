"""
Re-line the Cantonese 7pm-8pm row on Graton 3232 (SFO) / 3233 (CVC), Nov 2026.

The IO's week 1 is Thu 1x + Fri 2x (weeks 2-4 add Wed 1x). The parser wrote it
as 'Th,F 3 spots max 1/day' + 'W,Th,F 12 spots max 1/day' — neither can place
(Lee 2026-10-31). Lee deletes those two lines per contract in the Etere app
(the safe by-hand trim); this script ADDS the three lines the fixed parser
now produces, onto the existing headers, copying separation and row status
from the Mandarin sibling line.

    uv run python3 scripts/reline_graton_2611_cantonese.py            # dry run (rollback)
    uv run python3 scripts/reline_graton_2611_cantonese.py --apply    # commit
"""

import sys
from datetime import date
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "browser_automation"))

from browser_automation.etere_direct_client import EtereDirectClient, connect  # noqa: E402

FPS = 29.97
DESC = "Cant. News/Talk 7pm-8pm"
SIBLING_DESC = "Mand. News/Drama 8pm-10pm"
# (contract id, market, rate)
CONTRACTS = [(3232, "SFO", 180.0), (3233, "CVC", 60.0)]
# (days, start, end, total, max/day) — exactly get_etere_lines() on the Nov IO
PLAN = [
    ("TH", date(2026, 11, 2), date(2026, 11, 5), 1, 1),
    ("F", date(2026, 11, 2), date(2026, 11, 27), 8, 2),
    ("W,TH", date(2026, 11, 9), date(2026, 11, 26), 6, 1),
]
EXPECTED_WEEKDAY_BLOCKS = {"TH": 1, "F": 1, "W,TH": 2}  # distinct weekday blocks per line


def _minutes(frames: int) -> int:
    return round(frames / (60 * FPS))


def main(apply: bool) -> int:
    conn = connect()
    cur = conn.cursor()
    try:
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")
        for cid, market, rate in CONTRACTS:
            cur.execute(
                "SELECT ID_CONTRATTIRIGHE, DESCRIZIONE, N_PASSAGGI, PASSAGGI_GIORNALIERI, "
                "Interv_Committente, INTERVALLO, INTERV_CONTRATTO, ROWSTATUS, DURATA, "
                "(SELECT COUNT(*) FROM trafficPalinse tp WHERE tp.ID_ContrattiRighe=cr.ID_CONTRATTIRIGHE) "
                "FROM CONTRATTIRIGHE cr WHERE ID_CONTRATTITESTATA=%s ORDER BY ID_CONTRATTIRIGHE",
                (cid,),
            )
            rows = cur.fetchall()
            wrong = [r for r in rows if r[1] == DESC]
            sib = [r for r in rows if r[1] == SIBLING_DESC]
            assert sib, f"{cid}: no {SIBLING_DESC!r} sibling line"
            sib = sib[0]
            sep = (_minutes(sib[4]), _minutes(sib[5]), _minutes(sib[6]))
            row_status = sib[7]
            print(
                f"\n== {cid} {market}  ${rate:.2f}/spot  sibling sep {sep} rowstatus {row_status}"
            )
            for r in wrong:
                print(f"   old line {r[0]}: {r[2]} spots, max {r[3]}/day, placed {r[9]}")
                assert r[9] == 0, f"{cid}: old line {r[0]} has placed spots — stop"
            if wrong:
                print(
                    "   ⚠ the old Cantonese line(s) above are still on the contract — "
                    "delete them in the Etere app before --apply"
                )
                if apply:
                    return 2

            new_ids = []
            for days, d0, d1, total, cap in PLAN:
                lid = client.add_contract_line(
                    contract_id=cid,
                    market=market,
                    days=days,
                    time_range="19:00-20:00",
                    description=DESC,
                    rate=rate,
                    total_spots=total,
                    spots_per_week=0,
                    max_daily_run=cap,
                    date_from=d0,
                    date_to=d1,
                    duration="00:00:30:00",
                    is_bonus=False,
                    booking_code=2,
                    separation_intervals=sep,
                    row_status=row_status,
                )
                assert lid > 0, f"{cid}: add_contract_line failed for {days}"
                new_ids.append((lid, days, d0, d1, total, cap))

            # Readback inside the transaction.
            for lid, days, d0, d1, total, cap in new_ids:
                cur.execute(
                    "SELECT N_PASSAGGI, PASSAGGI_GIORNALIERI, CONVERT(varchar,DATESTART,101), "
                    "CONVERT(varchar,DATEEND,101), Interv_Committente, INTERVALLO, INTERV_CONTRATTO, "
                    "ROWSTATUS, IMPORTO, ID_BOOKINGCODE, COD_USER, ORA_INIZIOF, ORA_FINEF, "
                    "(SELECT COUNT(*) FROM contrattifasce f WHERE f.id_contrattirighe=cr.ID_CONTRATTIRIGHE) "
                    "FROM CONTRATTIRIGHE cr WHERE ID_CONTRATTIRIGHE=%s",
                    (lid,),
                )
                r = cur.fetchone()
                print(
                    f"   new line {lid}: days={days:5s} {r[2]}–{r[3]}  {r[0]} spots max {r[1]}/day  "
                    f"${r[8]}  sep {_minutes(r[4])}/{_minutes(r[5])}/{_minutes(r[6])}  "
                    f"rowstatus {r[7]} bc {r[9]} station {r[10]}  blocks {r[13]}"
                )
                assert (r[0], r[1]) == (total, cap)
                assert r[2] == d0.strftime("%m/%d/%Y") and r[3] == d1.strftime("%m/%d/%Y")
                assert (_minutes(r[4]), _minutes(r[5]), _minutes(r[6])) == sep
                assert r[7] == row_status and r[9] == 2 and float(r[8]) == rate
                assert r[13] >= EXPECTED_WEEKDAY_BLOCKS[days], f"line {lid}: only {r[13]} blocks"
            cur.execute(
                "SELECT SUM(N_PASSAGGI), SUM(N_PASSAGGI*IMPORTO) FROM CONTRATTIRIGHE "
                "WHERE ID_CONTRATTITESTATA=%s AND DESCRIZIONE=%s AND ID_CONTRATTIRIGHE IN %s",
                (cid, DESC, tuple(n[0] for n in new_ids)),
            )
            tot = cur.fetchone()
            print(
                f"   Cantonese row total: {tot[0]} spots  ${float(tot[1]):,.2f}  (IO: 15 / ${15 * rate:,.2f})"
            )
            assert tot[0] == 15 and float(tot[1]) == 15 * rate

        if apply:
            conn.commit()
            print("\nCOMMITTED")
        else:
            conn.rollback()
            print("\nDRY RUN — rolled back")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
