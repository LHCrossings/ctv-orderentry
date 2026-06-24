"""
Toyota AAPI Added Value Order Automation

Handles "Toyota AAPI Month Flight Schedule" PDFs — a separate order type from
the regular TCAA cable buy.  All lines are:
  - BNS spot type (spot_code=10)
  - Top-of-break scheduling (is_billboard=True — :25s with embedded :10 billboard)
  - Added Value (is_added_value=True)
  - Rate $0
  - Duration :25s

Master market is NYC (set by session).  All lines use market=SEA.
"""
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

_src_path = Path(__file__).parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from parsers.tcaa_av_parser import ToyotaAVOrder, parse_toyota_av_pdf

from browser_automation.etere_client import EtereClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CUSTOMER_ID = 75            # TCAA Toyota
DEFAULT_MARKET = "SEA"
DEFAULT_DURATION_SEC = 25   # All Toyota AV spots are :25s
DEFAULT_SEPARATION = (10, 0, 0)  # TCAA standard: 10-min customer, 0 event, 0 order
CONTRACT_NOTES = "May AAPI Heritage Month Sponsorship"


# ---------------------------------------------------------------------------
# User input gathering (all prompts before browser opens)
# ---------------------------------------------------------------------------

def gather_inputs(order: ToyotaAVOrder) -> dict:
    """Gather all user input needed before automation starts."""
    print(f"\n{'='*70}")
    print("TOYOTA AAPI ADDED VALUE — ORDER ENTRY")
    print(f"{'='*70}\n")
    print(f"  Title   : {order.title}")
    print(f"  Flight  : {order.flight_start} – {order.flight_end}")
    print(f"  Weeks   : {', '.join(order.week_dates)}")
    print(f"  Lines   : {len(order.lines)} ({sum(1 for ln in order.lines if ln.is_bonus)} ROS)")
    print()

    # Contract code
    default_code = "TCAA Toyota AAPI May"
    code_input = input(f"Contract code [{default_code}]: ").strip()
    contract_code = code_input or default_code

    # Contract description
    default_desc = "Toyota SEA AAPI May 2026"
    desc_input = input(f"Contract description [{default_desc}]: ").strip()
    contract_desc = desc_input or default_desc

    # New or existing contract
    is_revision = input("\nNew contract or add to existing? [N=new / enter contract#]: ").strip()
    existing_contract_number = None
    if is_revision and is_revision.upper() != "N":
        existing_contract_number = is_revision

    # Market
    market_input = input(f"\nMarket [{DEFAULT_MARKET}]: ").strip().upper()
    market = market_input or DEFAULT_MARKET

    # Duration
    dur_input = input(f"Spot duration in seconds [{DEFAULT_DURATION_SEC}]: ").strip()
    duration = int(dur_input) if dur_input else DEFAULT_DURATION_SEC

    return {
        "contract_code": contract_code,
        "contract_desc": contract_desc,
        "market": market,
        "duration": duration,
        "separation": DEFAULT_SEPARATION,
        "existing_contract_number": existing_contract_number,
    }


# ---------------------------------------------------------------------------
# Direct DB entry
# ---------------------------------------------------------------------------

def _parse_date(s: str):
    """Parse MM/DD/YYYY or YYYY-MM-DD string to date object."""
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


def _secs_to_duration(seconds: int) -> str:
    return f":{seconds:02d}"


def _create_tcaa_av_contract_direct(order: ToyotaAVOrder, inputs: dict) -> Optional[int]:
    """Enter Toyota AAPI AV order directly via DB stored procedures (no browser).
    Returns contract_id on success, None on failure.
    """
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        market         = inputs.get("market", DEFAULT_MARKET)
        duration_str   = _secs_to_duration(inputs.get("duration", DEFAULT_DURATION_SEC))
        separation     = inputs.get("separation", DEFAULT_SEPARATION)
        existing_num   = inputs.get("existing_contract_number") or None
        is_revision    = bool(existing_num)

        if is_revision:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT ID_CONTRATTITESTATA FROM CONTRATTITESTATA WHERE NUMERO = %s",
                    (existing_num,),
                )
                row = cur.fetchone()
            if not row:
                print(f"[TCAA_AV DIRECT] ✗ Contract {existing_num!r} not found in CONTRATTITESTATA")
                conn.rollback()
                conn.close()
                return None
            contract_id = row[0]
            print(f"[TCAA_AV DIRECT] Adding to existing contract ID={contract_id} ({existing_num})")
        else:
            contract_id = client.create_contract_header(
                code=inputs["contract_code"],
                description=inputs["contract_desc"],
                customer_id=CUSTOMER_ID,
                contract_date=_parse_date(order.flight_start),
                contract_end_date=_parse_date(order.flight_end),
                billing_type="agency",
                note=CONTRACT_NOTES,
                allow_rename=True,
            )
            print(f"[TCAA_AV DIRECT] ✓ Contract header ID={contract_id}")

        row_status = 2 if is_revision else 0
        line_count = 0

        for line in order.lines:
            spots_pw = line.weekly_spots
            if isinstance(spots_pw, list):
                spots_pw_list = spots_pw
            else:
                spots_pw_list = [spots_pw] * len(order.week_dates)

            ranges = EtereClient.consolidate_weeks(
                spots_pw_list,
                order.week_dates,
                order.flight_end,
                flight_start=order.flight_start,
            )

            time_from, time_to = EtereClient.parse_time_range(line.time)
            time_range         = f"{time_from}-{time_to}"
            adj_days, _        = EtereClient.check_sunday_6_7a_rule(line.days, line.time)

            for rng in ranges:
                line_count += 1
                spw = rng["spots_per_week"]
                if isinstance(spw, list):
                    spw = spw[0]
                total = spw * rng["weeks"]
                print(f"  [{line_count}] {line.description}: "
                      f"{rng['start_date']}–{rng['end_date']} spw={spw} total={total}")

                client.add_contract_line(
                    market=market,
                    days=adj_days,
                    time_range=time_range,
                    description=line.description,
                    rate=0.0,
                    total_spots=total,
                    spots_per_week=spw,
                    date_from=_parse_date(rng["start_date"]),
                    date_to=_parse_date(rng["end_date"]),
                    duration=duration_str,
                    is_bonus=True,
                    booking_code=10,
                    is_billboard=True,
                    separation_intervals=separation,
                    contract_id=contract_id,
                    row_status=row_status,
                )

        conn.commit()
        conn.close()
        print(f"\n[TCAA_AV DIRECT] ✓ {line_count} lines committed")
        return contract_id

    except Exception as exc:
        print(f"[TCAA_AV DIRECT] ✗ {exc}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def gather_tcaa_av_inputs(pdf_path: str) -> Optional[dict]:
    """Parse PDF and gather all user inputs upfront (called by orchestrator)."""
    try:
        order = parse_toyota_av_pdf(pdf_path)
    except Exception as e:
        print(f"[TCAA AV] ✗ Failed to parse PDF: {e}")
        return None
    return gather_inputs(order)


def process_toyota_av_order(
    pdf_path: str,
    pre_gathered_inputs: Optional[dict] = None,
) -> bool:
    """Full pipeline: parse PDF → gather inputs → enter contract."""
    print(f"\n[AV] Parsing {Path(pdf_path).name} ...")
    order = parse_toyota_av_pdf(pdf_path)

    inputs = pre_gathered_inputs if pre_gathered_inputs is not None else gather_inputs(order)

    contract_id = _create_tcaa_av_contract_direct(order, inputs)
    return contract_id is not None


if __name__ == "__main__":
    pdf_path = input("Enter path to Toyota AAPI AV PDF: ").strip()
    success = process_toyota_av_order(pdf_path)
    print("\n✓ Done" if success else "\n✗ Failed — review errors above")
