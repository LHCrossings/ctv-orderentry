"""American Community Media — October 2026 subtitling + proofing services, $1,560.00 NET.

Lee (2026-10-26): "just so we can get the dollars registered in Etere; no spots will air;
agency AND client is American Community Media; agency order, zero commission; line listings."

Shape = contract 2203's production lines (reference only — that contract is CBM's, this one
is ACM end to end): one line per item, 1 "spot" at the item's NET amount, 6:00a-6:01a, hidden
from the scheduler (HIDEFROMSCHEDULER=1 → never places; ContrattiImportiGiornalieri still
carries the money on the line date, which is what October billing reads).

    python scripts/enter_acm_services_2610.py            # dry run: writes, reads back, rolls back
    python scripts/enter_acm_services_2610.py --apply    # commits
Options: --date m/d/yyyy (line + header date, default 10/26/2026 = the email), --media-center
317 (Calendar — Lee: so the contract registers as October money; Broadcast 316 would need a
date on or before 10/25), --market NYC, --code 'ACM 2610', --desc '...'.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
for _p in (str(_root), str(_root / "browser_automation")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from browser_automation.etere_direct_client import EtereDirectClient, connect  # noqa: E402
from browser_automation.line_planner import parse_date  # noqa: E402

CUSTOMER_ID = 438  # American Community Media
AGENCY_ID = 438  # the same record is the agency (Lee); ANAGRAF Commissione 0.00
ITEMS = [  # (description, NET)
    ("Video subtitling Hindi :30", 360.00),
    ("Video subtitling Khmer :30", 360.00),
    ("Proofing Spanish video script", 150.00),
    ("Proofing Hindi C2 video, flyer, radio scripts", 300.00),  # 50-char line limit
    ("Proofing Khmer video script", 200.00),
    ("Proofing Hindi radio script #3 :30 & :15", 190.00),
]
TOTAL = 1560.00


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--date", default="10/26/2026")  # the email date; Calendar → October
    ap.add_argument(
        "--media-center",
        type=int,
        default=317,
        help="317 Calendar (Lee: October money), 316 Broadcast",
    )
    ap.add_argument("--market", default="NYC")
    ap.add_argument("--code", default="ACM 2610")
    ap.add_argument("--desc", default="ACM Subtitling & Proofing 2610")
    a = ap.parse_args()
    assert abs(sum(v for _, v in ITEMS) - TOTAL) < 0.005, "items do not sum to the email total"
    d: date = parse_date(a.date)

    conn = connect()
    try:
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")
        cid = client.create_contract_header(
            code=a.code,
            description=a.desc,
            customer_id=CUSTOMER_ID,
            agency_id=AGENCY_ID,
            agency_pct=0.0,
            lookup_customer_defaults=True,  # payment / agent / media centre from ANAGRAF 438
            contract_date=d,
            contract_end_date=d,
            contract_type=1,
            billing_type="agency",
            media_center_id=a.media_center,
            note="Production and services only - no airtime (email 10/26/2026)",
            allow_rename=True,
        )
        print(f"header {cid} {a.code!r} {a.desc!r} {d}")
        for desc, amount in ITEMS:
            lid = client.add_contract_line(
                market=a.market,
                days="M-Su",
                time_range="06:00-06:01",
                description=desc,
                rate=amount,
                total_spots=1,
                spots_per_week=1,
                max_daily_run=1,
                date_from=d,
                date_to=d,
                duration="1",
                booking_code=2,
                scheduling_type=1,
                separation_intervals=(15, 0, 0),
                hide_from_scheduler=True,
            )
            print(f"  line {lid} {desc!r} ${amount:,.2f}")

        cur = conn.cursor()
        cur.execute(
            "SELECT COD_CONTRATTO, DESCRIZIONE, COMMITTENTE, AGENZIA, P_AGENZIA, INVOICEMODE, CENTROMEDIA, COD_USER, "
            "CONVERT(varchar(10), DATA_INIZIO, 101), CONVERT(varchar(10), DATA_TERMINE, 101) "
            "FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = %s",
            (cid,),
        )
        print("header readback:", cur.fetchone())
        cur.execute(
            "SELECT COD_USER, DESCRIZIONE, CONVERT(varchar(10), DATESTART, 101), N_PASSAGGI, IMPORTO, "
            "HIDEFROMSCHEDULER, ROWSTATUS, ID_BOOKINGCODE, PRENOTAZIONE "
            "FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA = %s ORDER BY ID_CONTRATTIRIGHE",
            (cid,),
        )
        rows = cur.fetchall()
        for r in rows:
            print("  line readback:", r)
        cur.execute(
            "SELECT CONVERT(varchar(10), g.DATA, 101), SUM(g.IMPORTO) FROM ContrattiImportiGiornalieri g "
            "JOIN CONTRATTIRIGHE r ON r.ID_CONTRATTIRIGHE = g.ID_ContrattiRighe "
            "WHERE r.ID_CONTRATTITESTATA = %s GROUP BY g.DATA",
            (cid,),
        )
        cig = cur.fetchall()
        print("daily revenue readback:", cig)
        ok = (
            len(rows) == len(ITEMS)
            and all(bool(r[5]) and r[6] == 0 and r[3] == 1 for r in rows)
            and abs(float(sum(float(r[4]) for r in rows)) - TOTAL) < 0.005
            and len(cig) == 1
            and abs(float(cig[0][1]) - TOTAL) < 0.005
        )
        if not ok:
            print("✗ readback does not match the plan — rolling back")
            conn.rollback()
            return 2
        if a.apply:
            conn.commit()
            print(
                f"✓ COMMITTED contract {cid} {a.code}: {len(rows)} hidden lines, ${TOTAL:,.2f} on {d}"
            )
        else:
            conn.rollback()
            print("DRY RUN — rolled back")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
