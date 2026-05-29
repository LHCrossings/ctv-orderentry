"""
Time Advertising Broadcast Order Automation

Handles input gathering and Etere entry for Time Advertising, Inc. orders
(e.g. Graton Casino on Crossings TV SF and Sacramento).

Business Rules:
- Agency: Time Advertising, Inc. (billed as agency)
- One PDF per market (SFO or CVC)
- Two paid dayparts: Cantonese News/Talk + Mandarin News/Drama
- Bonus (free) spots: M-Sun ROS, no rate
- Rate: gross rate from PDF (no grossing needed — already gross)
- Separation: (15, 0, 0)
- Customer DB lookup by advertiser name
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from browser_automation.etere_client import EtereClient  # parse_time_range utility only
from browser_automation.etere_direct_client import EtereDirectClient, connect
from browser_automation.parsers.timeadvertising_parser import (
    TimeAdvertisingOrder,
    parse_timeadvertising_pdf,
)
from src.domain.enums import OrderType, SeparationInterval


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

TIMEADVERTISING_SEPARATION = SeparationInterval.TIMEADVERTISING.value  # (15, 0, 0)
from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
# ─────────────────────────────────────────────────────────────────────────────
# CUSTOMER DATABASE LOOKUP
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_customer(client_name: str) -> Optional[dict]:
    """Look up customer in the database by advertiser name."""
    if not os.path.exists(CUSTOMER_DB_PATH):
        return None
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        customer = (
            repo.find_by_name(client_name, OrderType.TIMEADVERTISING) or
            repo.find_by_name_fuzzy(client_name, OrderType.TIMEADVERTISING)
        )
        if customer:
            return {
                'customer_id': customer.customer_id,
                'abbreviation': customer.abbreviation,
            }
    except Exception as exc:
        print(f"[CUSTOMER DB] ⚠ Lookup failed: {exc}")
    return None


def _save_new_customer(
    customer_id: str,
    customer_name: str,
    default_market: str,
    db_path: str = CUSTOMER_DB_PATH,
) -> None:
    """Save a new Time Advertising customer to the database."""
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        repo = CustomerRepository(db_path)
        customer = Customer(
            customer_id=customer_id,
            customer_name=customer_name,
            order_type=OrderType.TIMEADVERTISING,
            abbreviation=customer_name[:6].upper(),
            default_market=default_market,
            billing_type="agency",
            separation_customer=TIMEADVERTISING_SEPARATION[0],
            separation_event=TIMEADVERTISING_SEPARATION[1],
            separation_order=TIMEADVERTISING_SEPARATION[2],
        )
        repo.save(customer)
        print(f"[CUSTOMER DB] ✓ Saved: {customer_name} → ID {customer_id}")
    except Exception as exc:
        print(f"[CUSTOMER DB] ✗ Save failed: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# UPFRONT INPUT GATHERING
# ─────────────────────────────────────────────────────────────────────────────

def gather_timeadvertising_inputs(pdf_path: str) -> Optional[dict]:
    """
    Parse PDF and gather user inputs before browser session.

    Args:
        pdf_path: Path to Time Advertising broadcast order PDF

    Returns:
        Dict with order data and user inputs, or None if cancelled
    """
    order = parse_timeadvertising_pdf(pdf_path)
    if not order:
        print("[TIME ADVERTISING] Failed to parse PDF")
        return None

    return _gather_inputs(order)


def _gather_inputs(order: TimeAdvertisingOrder) -> Optional[dict]:
    """Gather user inputs for the parsed order."""
    print(f"\n{'='*70}")
    print("TIME ADVERTISING - INPUT GATHERING")
    print(f"{'='*70}\n")

    print(f"  Advertiser:  {order.advertiser}")
    print(f"  Station:     {order.station}")
    print(f"  Market:      {order.market}")
    print(f"  Agency:      {order.agency}")
    print(f"  Duration:    :{order.duration_seconds}s")
    print(f"  Flight:      {order.flight_start} → {order.flight_end}")
    print(f"  Lines:       {len(order.paid_lines)} paid, {len(order.bonus_lines)} bonus")
    print()
    for ln in order.lines:
        kind = "BONUS" if ln.rate == 0 else "PAID"
        print(f"  [{kind}] {ln.program}  →  {ln.total_spots} spots @ ${ln.rate:.2f}")
    print()

    # ── Customer lookup ───────────────────────────────────────────────────
    customer = _lookup_customer(order.advertiser)
    if customer:
        print(f"[DB] Customer found: ID={customer['customer_id']}")
        customer_id = customer['customer_id']
    else:
        print(f"[DB] No customer record found for '{order.advertiser}'")
        customer_id = input("  Enter Etere customer ID: ").strip()
        if not customer_id:
            print("[CANCELLED]")
            return None
        save_yn = input(f"  Save '{order.advertiser}' (ID {customer_id}) to DB for next time? (y/n): ").strip().lower()
        if save_yn == 'y':
            _save_new_customer(customer_id, order.advertiser, order.market)

    # ── Contract code ─────────────────────────────────────────────────────
    default_code = _default_contract_code(order)
    print(f"\n[1/2] Contract Code")
    print(f"  Default: {default_code}")
    resp = input("  Use default? (y/n): ").strip().lower()
    contract_code = default_code if resp == 'y' else input("  Enter code: ").strip()
    if not contract_code:
        print("[CANCELLED]")
        return None

    # ── Description ───────────────────────────────────────────────────────
    default_desc = _default_description(order)
    print(f"\n[2/2] Contract Description")
    print(f"  Default: {default_desc}")
    resp = input("  Use default? (y/n): ").strip().lower()
    description = default_desc if resp == 'y' else input("  Enter description: ").strip()

    # ── Etere lines preview ───────────────────────────────────────────────
    print(f"\n{'─'*70}")
    print("ETERE LINES PREVIEW")
    print(f"{'─'*70}")
    total_etere = 0
    for ln in order.lines:
        kind = "BONUS" if ln.rate == 0 else "PAID"
        print(f"\n  [{kind}] {ln.program}")
        for k, spec in enumerate(ln.get_etere_lines(), 1):
            rate_str = f"${spec['rate']:.2f}" if spec['rate'] > 0 else "$0.00 (free)"
            print(f"    Line {k}: days={spec['days']:6s}  "
                  f"{spec['start_date']} – {spec['end_date']}  "
                  f"{spec['total_spots']} spots  {rate_str}")
            total_etere += 1
    print(f"\n  Total Etere lines: {total_etere}")
    print(f"{'─'*70}")
    confirm = input("\n  Proceed with automation? (y/n): ").strip().lower()
    if confirm != 'y':
        print("[CANCELLED]")
        return None

    return {
        'order': order,
        'customer_id': customer_id,
        'contract_code': contract_code,
        'description': description,
        'separation': TIMEADVERTISING_SEPARATION,
    }


def _yymm(order: TimeAdvertisingOrder) -> str:
    """Return 'yymm' from the broadcast month of the flight.

    Uses the Sunday of the first flight week — per broadcast calendar rules
    a week beginning in late month N belongs to month N+1 when its Sunday
    falls in N+1.  e.g. week of 4/27 → Sunday 5/3 → '2605'.
    Falls back to order_date when flight_start is unavailable.
    """
    from datetime import datetime, timedelta
    if order.flight_start:
        try:
            wk_start = datetime.strptime(order.flight_start, '%m/%d/%Y')
            ref = wk_start + timedelta(days=6)  # Sunday of that week
            return f"{ref.year % 100:02d}{ref.month:02d}"
        except ValueError:
            pass
    if order.order_date:
        parts = order.order_date.split('/')
        if len(parts) == 3:
            try:
                yy = str(int(parts[2]))[-2:]
                mm = f"{int(parts[0]):02d}"
                return f"{yy}{mm}"
            except ValueError:
                pass
    return ""


def _default_contract_code(order: TimeAdvertisingOrder) -> str:
    """Generate default contract code: 'Time Graton CVC 2603'"""
    return f"Time Graton {order.market} {_yymm(order)}"


def _default_description(order: TimeAdvertisingOrder) -> str:
    """Generate default description: 'Graton Casino 2603 - CVC'"""
    return f"{order.advertiser} {_yymm(order)} - {order.market}"


# ─────────────────────────────────────────────────────────────────────────────
# LINE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _days_for_etere(days_str: str) -> str:
    """Convert concatenated day codes to comma-separated: 'RF' → 'R,F'"""
    return ",".join(list(days_str))


def _line_description(program: str) -> str:
    """Strip the 'M-F: ' schedule prefix: 'M-F: Cant. News/Talk 7pm-8pm' → 'Cant. News/Talk 7pm-8pm'"""
    return re.sub(r'^[A-Za-z\-]+:\s*', '', program)


def _line_times(program: str) -> tuple:
    """Return (time_from, time_to) in HH:MM 24h.
    Extracts trailing 'Xpm-Ypm' range from the program name.
    Falls back to 06:00–23:59 for ROS/untimed lines.
    """
    m = re.search(r'(\d+(?::\d+)?[ap]m-\d+(?::\d+)?[ap]m)\s*$', program, re.IGNORECASE)
    if m:
        return EtereClient.parse_time_range(m.group(1))
    return ("06:00", "23:59")


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT CREATION
# ─────────────────────────────────────────────────────────────────────────────

def _create_timeadvertising_contract(
    order: TimeAdvertisingOrder,
    inputs: dict,
) -> bool:
    """
    Create the Etere contract for a Time Advertising broadcast order via direct DB.

    Workflow:
    1. Create contract header
    2. For each line (paid + bonus), expand to Etere lines via get_etere_lines()
    3. Add each Etere line to the contract
    """
    from datetime import date as _date

    customer_id = inputs.get('customer_id')
    separation  = inputs.get('separation', TIMEADVERTISING_SEPARATION)
    duration_str = f"00:00:{order.duration_seconds:02d}:00"

    print(f"[TIME ADVERTISING] Creating contract for {order.advertiser}")

    conn = connect()
    try:
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=True)
        client.set_master_market("NYC")

        flight_start = datetime.strptime(order.flight_start, '%m/%d/%Y').date()
        flight_end   = datetime.strptime(order.flight_end,   '%m/%d/%Y').date()

        contract_id = client.create_contract_header(
            customer_id=int(customer_id),
            code=inputs['contract_code'],
            description=inputs['description'],
            contract_date=flight_start,
            contract_end_date=flight_end,
            billing_type="agency",
        )

        if not contract_id:
            print("[TIME ADVERTISING] ✗ Failed to create contract header")
            return False

        print(f"[TIME ADVERTISING] ✓ Contract created: #{contract_id}")

        # ── Lines ─────────────────────────────────────────────────────────────
        line_count = 0

        for ln in order.lines:
            booking_code = 10 if ln.rate == 0 else 2
            time_from, time_to = _line_times(ln.program)
            time_range = f"{time_from}-{time_to}"
            base_desc = _line_description(ln.program)
            desc = f"BNS {base_desc}" if ln.rate == 0 else base_desc
            etere_specs = ln.get_etere_lines()

            kind = "BONUS" if ln.rate == 0 else "PAID"
            print(f"\n  {kind}: {desc}")
            print(f"    Time: {time_from}–{time_to}  |  {len(etere_specs)} Etere line(s)")

            for spec in etere_specs:
                line_count += 1
                days = _days_for_etere(spec['days'])
                date_from = datetime.strptime(spec['start_date'], '%m/%d/%Y').date()
                date_to   = datetime.strptime(spec['end_date'],   '%m/%d/%Y').date()
                rate_str  = f"${spec['rate']:.2f}" if spec['rate'] > 0 else "$0.00 (free)"
                print(f"    Line {line_count}: days={days}  "
                      f"{spec['start_date']} – {spec['end_date']}  "
                      f"{spec['total_spots']} spots  {rate_str}")

                line_id = client.add_contract_line(
                    contract_id=contract_id,
                    market=order.market,
                    days=days,
                    time_range=time_range,
                    description=desc,
                    rate=float(spec['rate']),
                    total_spots=spec['total_spots'],
                    spots_per_week=0,
                    max_daily_run=spec['per_day_max'],
                    date_from=date_from,
                    date_to=date_to,
                    duration=duration_str,
                    is_bonus=(ln.rate == 0),
                    booking_code=booking_code,
                    separation_intervals=separation,
                )
                if line_id <= 0:
                    print(f"    ✗ Failed to add line {line_count}: {desc} ({spec['start_date']}–{spec['end_date']})")
                    return False
                print(f"    → line_id = {line_id}")

        print(f"\n[TIME ADVERTISING] ✓ All {line_count} Etere lines added successfully")
        return True

    except Exception as exc:
        print(f"\n[TIME ADVERTISING] ✗ Error creating contract: {exc}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PROCESSING FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def process_timeadvertising_order(
    driver=None,
    pdf_path: str = "",
    shared_session=None,
    pre_gathered_inputs: Optional[dict] = None,
) -> bool:
    """
    Process a Time Advertising broadcast order PDF and create the contract in Etere.

    Args:
        driver:               Unused — kept for interface compatibility with orchestrator
        pdf_path:             Path to Time Advertising PDF
        shared_session:       Unused — kept for interface compatibility
        pre_gathered_inputs:  Pre-gathered inputs dict (skips upfront prompts)

    Returns:
        True if contract created successfully, False otherwise.
    """
    try:
        if pre_gathered_inputs:
            inputs = pre_gathered_inputs
            order  = inputs['order']
            print("[INFO] Using pre-gathered inputs\n")
        else:
            inputs = gather_timeadvertising_inputs(pdf_path)
            if not inputs:
                print("\n✗ Input gathering cancelled")
                return False
            order = inputs['order']

        print(f"\n{'=' * 70}")
        print("TIME ADVERTISING ORDER PROCESSING")
        print(f"{'=' * 70}")
        print(f"Advertiser:  {order.advertiser}")
        print(f"Market:      {order.market}")
        print(f"Flight:      {order.flight_start} – {order.flight_end}")
        print(f"Lines:       {len(order.paid_lines)} paid, {len(order.bonus_lines)} bonus")
        print(f"{'=' * 70}\n")

        success = _create_timeadvertising_contract(order, inputs)

        if success:
            print(f"\n{'=' * 70}")
            print("✓ TIME ADVERTISING ORDER PROCESSING COMPLETE")
            print(f"{'=' * 70}")
        else:
            print(f"\n{'=' * 70}")
            print("✗ TIME ADVERTISING ORDER PROCESSING FAILED")
            print(f"{'=' * 70}")

        return success

    except Exception as exc:
        print(f"\n✗ Error processing Time Advertising order: {exc}")
        import traceback
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE TEST ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pdf = input("Enter path to Time Advertising PDF: ").strip()
    success = process_timeadvertising_order(pdf_path=pdf)
    print("\n✓ Done" if success else "\n✗ Failed")
