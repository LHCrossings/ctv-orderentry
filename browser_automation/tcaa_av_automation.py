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
from pathlib import Path
from typing import Optional, Tuple
import sys

_src_path = Path(__file__).parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from browser_automation.etere_client import EtereClient
from src.domain.enums import BillingType
from parsers.tcaa_av_parser import parse_toyota_av_pdf, ToyotaAVOrder, ToyotaAVLine


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
    print(f"  Lines   : {len(order.lines)} ({sum(1 for l in order.lines if l.is_bonus)} ROS)")
    print()

    # Contract code
    default_code = "TCAA Toyota AAPI May"
    code_input = input(f"Contract code [{default_code}]: ").strip()
    contract_code = code_input or default_code

    # Contract description
    default_desc = f"Toyota SEA AAPI May 2026"
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

    # Separation
    from separation_utils import confirm_separation_intervals
    separation = confirm_separation_intervals(
        detected_separation=DEFAULT_SEPARATION[0],
        order_type="TCAA_AV",
    )

    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    print(f"  Code        : {contract_code}")
    print(f"  Description : {contract_desc}")
    print(f"  Market      : {market}")
    print(f"  Duration    : :{duration:02d}s")
    print(f"  Separation  : {separation}")
    if existing_contract_number:
        print(f"  Adding to existing contract: {existing_contract_number}")
    print(f"  Lines to enter: {len(order.lines)}")
    print()

    confirm = input("Proceed? [Y/n]: ").strip().lower()
    if confirm == 'n':
        raise SystemExit("Aborted by user.")

    return {
        "contract_code": contract_code,
        "contract_desc": contract_desc,
        "market": market,
        "duration": duration,
        "separation": separation,
        "existing_contract_number": existing_contract_number,
    }


# ---------------------------------------------------------------------------
# Contract creation
# ---------------------------------------------------------------------------

def create_toyota_av_contract(
    etere: EtereClient,
    order: ToyotaAVOrder,
    inputs: dict,
) -> bool:
    """Create the Toyota AV contract in Etere. Returns True on success."""
    try:
        print(f"\n[AV] Starting contract entry")

        contract_number = inputs.get("existing_contract_number")

        if not contract_number:
            # --- Create header ---
            contract_number = etere.create_contract_header(
                customer_id=CUSTOMER_ID,
                code=inputs["contract_code"],
                description=inputs["contract_desc"],
                contract_start=order.flight_start,
                contract_end=order.flight_end,
                customer_order_ref=None,
                notes=CONTRACT_NOTES,
                charge_to=BillingType.CUSTOMER_SHARE_AGENCY.get_charge_to(),
                invoice_header=BillingType.CUSTOMER_SHARE_AGENCY.get_invoice_header(),
            )
            if not contract_number:
                print("[AV] ✗ Failed to create contract header")
                return False
            print(f"[AV] ✓ Contract created: {contract_number}")
        else:
            print(f"[AV] Using existing contract: {contract_number}")

        # --- Add lines ---
        market = inputs["market"]
        duration = inputs["duration"]
        separation = inputs["separation"]

        line_count = 0

        for idx, line in enumerate(order.lines):
            # Consolidate weeks into contiguous Etere date ranges
            ranges = EtereClient.consolidate_weeks(
                line.weekly_spots,
                order.week_dates,
                order.flight_end,
                flight_start=order.flight_start,
            )

            # Parse time
            time_from, time_to = EtereClient.parse_time_range(line.time)

            # Apply Sunday 6-7a rule
            adj_days, _ = EtereClient.check_sunday_6_7a_rule(line.days, line.time)

            print(f"\n  Line {idx + 1}: {line.description}")
            print(f"    {adj_days}  {line.time}  → {len(ranges)} Etere range(s)")

            for rng in ranges:
                line_count += 1
                spots_pw = rng["spots_per_week"]
                if isinstance(spots_pw, list):
                    spots_pw = spots_pw[0]

                print(f"    [{line_count}] {rng['start_date']} – {rng['end_date']}  "
                      f"spw={spots_pw}  total={rng['spots']}")

                success = etere.add_contract_line(
                    contract_number=contract_number,
                    market=market,
                    start_date=rng["start_date"],
                    end_date=rng["end_date"],
                    days=adj_days,
                    time_from=time_from,
                    time_to=time_to,
                    description=line.description,
                    spot_code=10,           # BNS
                    duration_seconds=duration,
                    total_spots=rng["spots"],
                    spots_per_week=spots_pw,
                    rate=0.0,
                    separation_intervals=separation,
                    is_billboard=True,      # Top-of-break scheduling
                    is_bookend=False,
                )

                if not success:
                    print(f"    ✗ Failed to add line {line_count}")
                    return False

        print(f"\n[AV] ✓ All {line_count} lines added successfully")
        return True

    except Exception as e:
        print(f"\n[AV] ✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def process_toyota_av_order(driver, pdf_path: str) -> bool:
    """Full pipeline: parse PDF → gather inputs → enter contract."""
    print(f"\n[AV] Parsing {Path(pdf_path).name} ...")
    order = parse_toyota_av_pdf(pdf_path)

    inputs = gather_inputs(order)

    etere = EtereClient(driver)
    return create_toyota_av_contract(etere, order, inputs)


if __name__ == "__main__":
    from browser_automation.etere_session import EtereSession

    pdf_path = input("Enter path to Toyota AAPI AV PDF: ").strip()
    with EtereSession() as session:
        success = process_toyota_av_order(session.driver, pdf_path)
    print("\n✓ Done" if success else "\n✗ Failed — review errors above")
