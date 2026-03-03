"""
Time Advertising Broadcast Order Automation

Handles input gathering and Etere entry for Time Advertising, Inc. orders
(e.g. Graton Casino on Crossings TV SF and Sacramento).

Business Rules:
- Agency: Time Advertising, Inc. (billed as agency)
- One PDF per market (SFO or CVC)
- Two paid dayparts: Cantonese News/Talk + Mandarin News/Drama
- One thematic (free/bonus) daypart: M-Sun ROS
- Rate: gross rate from PDF (no grossing needed — already gross)
- Separation: (15, 0, 0)
- Customer DB lookup by advertiser name
"""

import os
import re
import sys
from pathlib import Path
from typing import Optional

_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from browser_automation.etere_client import EtereClient
from browser_automation.parsers.timeadvertising_parser import (
    TimeAdvertisingOrder,
    parse_timeadvertising_pdf,
)
from src.domain.enums import BillingType, OrderType, SeparationInterval


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

TIMEADVERTISING_SEPARATION = SeparationInterval.TIMEADVERTISING.value  # (15, 0, 0)
CUSTOMER_DB_PATH = os.path.join("data", "customers.db")


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
    print(f"  Lines:       {len(order.paid_lines)} paid, {len(order.thematic_lines)} thematic")
    print()
    for ln in order.lines:
        kind = "THEMATIC" if ln.is_thematic else "PAID"
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
        kind = "THEMATIC" if ln.is_thematic else "PAID"
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


def _default_contract_code(order: TimeAdvertisingOrder) -> str:
    """Generate default contract code from advertiser + market + date."""
    # e.g. "GratonCasino-SFO-Mar26"
    name = order.advertiser.replace(' ', '')
    market = order.market
    if order.order_date:
        parts = order.order_date.split('/')
        if len(parts) == 3:
            from datetime import datetime
            try:
                dt = datetime(int(parts[2]), int(parts[0]), int(parts[1]))
                date_tag = dt.strftime('%b%y')
            except ValueError:
                date_tag = order.order_date.replace('/', '')
        else:
            date_tag = order.order_date.replace('/', '')
    else:
        date_tag = ""
    return f"{name}-{market}-{date_tag}"


def _default_description(order: TimeAdvertisingOrder) -> str:
    """Generate default description from advertiser + agency."""
    return f"{order.advertiser} / {order.agency}"


# ─────────────────────────────────────────────────────────────────────────────
# LINE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _days_for_etere(days_str: str) -> str:
    """Convert concatenated day codes to comma-separated: 'RF' → 'R,F'"""
    return ",".join(list(days_str))


def _line_description(program: str) -> str:
    """Strip the 'M-F: ' schedule prefix: 'M-F: Cant. News/Talk 7pm-8pm' → 'Cant. News/Talk 7pm-8pm'"""
    return re.sub(r'^[A-Za-z\-]+:\s*', '', program)


def _line_times(program: str, is_thematic: bool) -> tuple:
    """Return (time_from, time_to) in HH:MM 24h.
    Thematic/ROS has no time constraint → 06:00–23:59.
    Paid lines: extract trailing 'Xpm-Ypm' from program name.
    """
    if is_thematic:
        return ("06:00", "23:59")
    m = re.search(r'(\d+(?::\d+)?[ap]m-\d+(?::\d+)?[ap]m)\s*$', program, re.IGNORECASE)
    if m:
        return EtereClient.parse_time_range(m.group(1))
    return ("06:00", "23:59")


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT CREATION
# ─────────────────────────────────────────────────────────────────────────────

def _create_timeadvertising_contract(
    etere: EtereClient,
    order: TimeAdvertisingOrder,
    inputs: dict,
) -> bool:
    """
    Create the Etere contract for a Time Advertising broadcast order.

    Workflow:
    1. Create contract header
    2. For each line (paid + thematic), expand to Etere lines via get_etere_lines()
    3. Add each Etere line to the contract
    """
    try:
        customer_id = inputs.get('customer_id')
        separation  = inputs.get('separation', TIMEADVERTISING_SEPARATION)

        print(f"[TIME ADVERTISING] Creating contract for {order.advertiser}")

        # ── Contract header ───────────────────────────────────────────────────
        contract_number = etere.create_contract_header(
            customer_id=int(customer_id),
            code=inputs['contract_code'],
            description=inputs['description'],
            contract_start=order.flight_start,
            contract_end=order.flight_end,
            charge_to=BillingType.CUSTOMER_SHARE_AGENCY.get_charge_to(),
            invoice_header=BillingType.CUSTOMER_SHARE_AGENCY.get_invoice_header(),
        )

        if not contract_number:
            print("[TIME ADVERTISING] ✗ Failed to create contract header")
            return False

        print(f"[TIME ADVERTISING] ✓ Contract created: {contract_number}")

        # ── Lines ─────────────────────────────────────────────────────────────
        line_count = 0

        for ln in order.lines:
            spot_code  = 10 if ln.is_thematic else 2
            time_from, time_to = _line_times(ln.program, ln.is_thematic)
            desc = _line_description(ln.program)
            etere_specs = ln.get_etere_lines()

            print(f"\n  {'THEMATIC' if ln.is_thematic else 'PAID'}: {desc}")
            print(f"    Time: {time_from}–{time_to}  |  Splits into {len(etere_specs)} Etere line(s)")

            for spec in etere_specs:
                line_count += 1
                days = _days_for_etere(spec['days'])
                rate_str = f"${spec['rate']:.2f}" if spec['rate'] > 0 else "$0.00 (free)"
                print(f"    Creating line {line_count}: "
                      f"days={days}  "
                      f"{spec['start_date']} – {spec['end_date']}  "
                      f"{spec['total_spots']} spots  {rate_str}")

                ok = etere.add_contract_line(
                    contract_number=contract_number,
                    market=order.market,
                    start_date=spec['start_date'],
                    end_date=spec['end_date'],
                    days=days,
                    time_from=time_from,
                    time_to=time_to,
                    description=desc,
                    spot_code=spot_code,
                    duration_seconds=order.duration_seconds,
                    total_spots=spec['total_spots'],
                    spots_per_week=0,
                    max_daily_run=spec['per_day_max'],
                    rate=float(spec['rate']),
                    separation_intervals=separation,
                )
                if not ok:
                    print(f"    ✗ Failed to add line {line_count}: {desc} ({spec['start_date']}–{spec['end_date']})")
                    return False

        print(f"\n[TIME ADVERTISING] ✓ All {line_count} Etere lines added successfully")
        return True

    except Exception as exc:
        print(f"\n[TIME ADVERTISING] ✗ Error creating contract: {exc}")
        import traceback
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PROCESSING FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def process_timeadvertising_order(
    driver,
    pdf_path: str,
    shared_session=None,
    pre_gathered_inputs: Optional[dict] = None,
) -> bool:
    """
    Process a Time Advertising broadcast order PDF and create the contract in Etere.

    Args:
        driver:               Selenium WebDriver
        pdf_path:             Path to Time Advertising PDF
        shared_session:       Optional shared Etere session
        pre_gathered_inputs:  Pre-gathered inputs dict (skips upfront prompts)

    Returns:
        True if contract created successfully, False otherwise.
    """
    try:
        order = parse_timeadvertising_pdf(pdf_path)
        if not order:
            print("[TIME ADVERTISING] ✗ Failed to parse PDF")
            return False

        print(f"\n{'=' * 70}")
        print("TIME ADVERTISING ORDER PROCESSING")
        print(f"{'=' * 70}")
        print(f"Advertiser:  {order.advertiser}")
        print(f"Market:      {order.market}")
        print(f"Flight:      {order.flight_start} – {order.flight_end}")
        print(f"Lines:       {len(order.paid_lines)} paid, {len(order.thematic_lines)} thematic")
        print(f"{'=' * 70}\n")

        if pre_gathered_inputs:
            inputs = pre_gathered_inputs
            print("[INFO] Using pre-gathered inputs\n")
        else:
            inputs = gather_timeadvertising_inputs(pdf_path)

        if not inputs:
            print("\n✗ Input gathering cancelled")
            return False

        etere = EtereClient(driver)

        success = _create_timeadvertising_contract(etere, order, inputs)

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
    from browser_automation.etere_session import EtereSession

    pdf = input("Enter path to Time Advertising PDF: ").strip()

    with EtereSession() as session:
        session.set_market("NYC")
        success = process_timeadvertising_order(session.driver, pdf)
        print("\n✓ Done" if success else "\n✗ Failed")
