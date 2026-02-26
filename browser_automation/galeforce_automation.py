"""
GaleForceMedia Generic Order Automation

Handles browser automation for GaleForceMedia orders that are NOT Sagent
(e.g. BMO/PACO Collective BO-3189 style).

Business Rules:
- Single-market order (market from PDF header)
- Customer: looked up by advertiser name in DB
- Gross-up: ask per order (net / 0.85 if yes)
- Separation: (25, 0, 0)
- Description: "{days} {time} {program.title()}" with BNS prefix for bonus
- Master market: NYC (standard for Crossings TV)
"""

import os
import re
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional

# Add project root to path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from browser_automation.etere_client import EtereClient
from browser_automation.ros_definitions import ROS_SCHEDULES
from browser_automation.parsers.galeforce_parser import (
    GaleForceOrder,
    GaleForceLine,
    parse_galeforce_pdf,
)
from src.domain.enums import BillingType, OrderType, SeparationInterval


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

GALEFORCE_SEPARATION = SeparationInterval.GALEFORCE.value  # (25, 0, 0)
CUSTOMER_DB_PATH = os.path.join("data", "customers.db")


# ─────────────────────────────────────────────────────────────────────────────
# CUSTOMER DATABASE LOOKUP
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_customer(client_name: str) -> Optional[dict]:
    """Look up GaleForce customer in the database by advertiser name."""
    if not os.path.exists(CUSTOMER_DB_PATH):
        return None
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        customer = (
            repo.find_by_name(client_name, OrderType.GALEFORCE) or
            repo.find_by_name_fuzzy(client_name, OrderType.GALEFORCE)
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
    db_path: str = CUSTOMER_DB_PATH,
) -> None:
    """Save a new GaleForce customer to the database."""
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        repo = CustomerRepository(db_path)
        customer = Customer(
            customer_id=customer_id,
            customer_name=customer_name,
            order_type=OrderType.GALEFORCE,
            abbreviation=customer_name[:6].upper(),
            default_market="LAX",
            billing_type="agency",
            separation_customer=GALEFORCE_SEPARATION[0],
            separation_event=GALEFORCE_SEPARATION[1],
            separation_order=GALEFORCE_SEPARATION[2],
        )
        repo.save(customer)
        print(f"[CUSTOMER DB] ✓ Saved: {customer_name} → ID {customer_id}")
    except Exception as exc:
        print(f"[CUSTOMER DB] ✗ Save failed: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# UPFRONT INPUT GATHERING
# ─────────────────────────────────────────────────────────────────────────────

def gather_galeforce_inputs(pdf_path: str) -> Optional[dict]:
    """
    Parse PDF and gather all user inputs BEFORE the browser session opens.

    Called by the orchestrator upfront-gathering phase.

    Returns:
        Dict with contract_code, description, notes, gross_up (bool),
        customer_id (int or None), separation.
        Returns None if the user cancels.
    """
    print("\n" + "=" * 70)
    print("GALEFORCE ORDER - INPUT COLLECTION")
    print("=" * 70)

    print("\n[PARSE] Reading PDF…")
    try:
        order = parse_galeforce_pdf(pdf_path)
    except Exception as exc:
        print(f"[PARSE] ✗ Failed: {exc}")
        return None

    print(f"\nAdvertiser:  {order.advertiser}")
    print(f"Campaign:    {order.campaign}")
    print(f"Flight:      {order.flight_start} – {order.flight_end}")
    print(f"Market:      {order.market}")
    print(f"Order #:     {order.order_number}")
    print(f"Estimate #:  {order.estimate_number} (stripped: {order.estimate_stripped})")
    print(f"Agency:      {order.agency}")
    print(f"Lines:       {len(order.lines)}")
    print(f"Total spots: {sum(l.total_spots for l in order.lines)}")
    print()

    # ── Customer lookup ──────────────────────────────────────────────────────
    customer_info = _lookup_customer(order.advertiser)
    customer_id: Optional[int] = None

    if customer_info:
        customer_id = customer_info['customer_id']
        print(f"[CUSTOMER] ✓ Found in DB: {order.advertiser} → ID {customer_id}")
    else:
        print(f"[CUSTOMER] Not found in DB for '{order.advertiser}'")
        raw_id = input("  Enter Etere customer ID (or blank to select in browser): ").strip()
        if raw_id.isdigit():
            customer_id = int(raw_id)
            save_yn = input(f"  Save '{order.advertiser}' (ID {customer_id}) to DB for next time? (y/n): ").strip().lower()
            if save_yn == 'y':
                _save_new_customer(str(customer_id), order.advertiser)
    print()

    # ── Contract code ─────────────────────────────────────────────────────────
    print("[1/4] Contract Code")
    print("-" * 70)
    default_code = order.get_default_contract_code()
    print(f"Default: {default_code}")
    use_default = input("Use default? (y/n): ").strip().lower()
    contract_code = default_code if use_default == 'y' else input("Enter contract code: ").strip()
    print(f"✓ {contract_code}\n")

    # ── Contract description ──────────────────────────────────────────────────
    print("[2/4] Contract Description")
    print("-" * 70)
    default_desc = order.get_default_description()
    print(f"Default: {default_desc}")
    use_default = input("Use default? (y/n): ").strip().lower()
    description = default_desc if use_default == 'y' else input("Enter description: ").strip()
    print(f"✓ {description}\n")

    # ── Notes ─────────────────────────────────────────────────────────────────
    print("[3/4] Contract Notes")
    print("-" * 70)
    default_notes = order.get_default_notes()
    print(f"Default: {default_notes}")
    use_default = input("Use default? (y/n): ").strip().lower()
    notes = default_notes if use_default == 'y' else input("Enter notes: ").strip()
    print(f"✓ {notes}\n")

    # ── Gross-up ─────────────────────────────────────────────────────────────
    print("[4/4] Rate Gross-up")
    print("-" * 70)
    print("Apply agency gross-up? (net rate ÷ 0.85 = gross rate)")
    gross_yn = input("Apply gross-up? (y/n): ").strip().lower()
    gross_up = gross_yn == 'y'
    print(f"✓ Gross-up: {'Yes' if gross_up else 'No'}\n")

    print("=" * 70)
    print("✓ All inputs gathered — ready for automation")
    print("=" * 70 + "\n")

    return {
        'contract_code': contract_code,
        'description': description,
        'notes': notes,
        'gross_up': gross_up,
        'customer_id': customer_id,
        'separation': GALEFORCE_SEPARATION,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PROCESSING FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def process_galeforce_order(
    driver,
    pdf_path: str,
    shared_session=None,
    pre_gathered_inputs: Optional[dict] = None,
) -> bool:
    """
    Process a GaleForceMedia order PDF and create the contract in Etere.

    Args:
        driver:               Selenium WebDriver
        pdf_path:             Path to GaleForce PDF
        shared_session:       Optional shared Etere session
        pre_gathered_inputs:  Pre-gathered inputs dict (skips upfront prompts)

    Returns:
        True if contract created successfully, False otherwise.
    """
    try:
        order = parse_galeforce_pdf(pdf_path)

        print(f"\n{'=' * 70}")
        print("GALEFORCE ORDER PROCESSING")
        print(f"{'=' * 70}")
        print(f"Advertiser:  {order.advertiser}")
        print(f"Campaign:    {order.campaign}")
        print(f"Order #:     {order.order_number}")
        print(f"Estimate:    {order.estimate_stripped}")
        print(f"Market:      {order.market}")
        print(f"Lines:       {len(order.lines)}")
        print(f"{'=' * 70}\n")

        if pre_gathered_inputs:
            inputs = pre_gathered_inputs
            print("[INFO] Using pre-gathered inputs\n")
        else:
            inputs = gather_galeforce_inputs(pdf_path)

        if not inputs:
            print("\n✗ Input gathering cancelled")
            return False

        etere = EtereClient(driver)

        success = _create_galeforce_contract(etere, order, inputs)

        if success:
            print(f"\n{'=' * 70}")
            print("✓ GALEFORCE ORDER PROCESSING COMPLETE")
            print(f"{'=' * 70}")
        else:
            print(f"\n{'=' * 70}")
            print("✗ GALEFORCE ORDER PROCESSING FAILED")
            print(f"{'=' * 70}")

        return success

    except Exception as exc:
        print(f"\n✗ Error processing GaleForce order: {exc}")
        import traceback
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT CREATION
# ─────────────────────────────────────────────────────────────────────────────

def _gross_up(rate: Decimal) -> Decimal:
    """Gross-up: net ÷ 0.85, rounded to 2 dp."""
    if rate == Decimal("0.00"):
        return Decimal("0.00")
    return (rate / Decimal("0.85")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _create_galeforce_contract(
    etere: EtereClient,
    order: GaleForceOrder,
    inputs: dict,
) -> bool:
    """
    Create the Etere contract for a GaleForceMedia order.

    Workflow:
    1. Create contract header
    2. For each line with spots > 0:
       - Apply gross-up if requested
       - Consolidate consecutive identical weeks
       - Add Etere contract line(s)
    """
    try:
        customer_id = inputs.get('customer_id')
        separation  = inputs.get('separation', GALEFORCE_SEPARATION)

        print(f"[GALEFORCE] Creating contract for {order.advertiser}")

        # ── Contract header ───────────────────────────────────────────────────
        contract_number = etere.create_contract_header(
            customer_id=customer_id,
            code=inputs['contract_code'],
            description=inputs['description'],
            contract_start=order.flight_start,
            contract_end=order.flight_end,
            customer_order_ref=order.order_number,
            notes=inputs['notes'],
            charge_to=BillingType.CUSTOMER_SHARE_AGENCY.get_charge_to(),
            invoice_header=BillingType.CUSTOMER_SHARE_AGENCY.get_invoice_header(),
        )

        if not contract_number:
            print("[GALEFORCE] ✗ Failed to create contract header")
            return False

        print(f"[GALEFORCE] ✓ Contract created: {contract_number}")

        # ── Lines ─────────────────────────────────────────────────────────────
        line_count = 0

        for line in order.lines:
            if line.total_spots == 0:
                print(f"  Line {line.line_number}: skipped (0 spots)")
                continue

            # Apply gross-up?
            if inputs.get('gross_up') and not line.is_bonus:
                rate = _gross_up(line.net_rate)
            else:
                rate = line.net_rate  # bonus lines always stay 0

            etere_days = line.get_etere_days()
            etere_time = line.get_etere_time()
            description = line.get_description(etere_days, etere_time)
            spot_code   = 10 if line.is_bonus else 2
            duration_s  = line.get_duration_seconds()

            # Parse time for Etere fields (handles semicolons automatically)
            time_from, time_to = EtereClient.parse_time_range(etere_time)

            # ROS bonus override (not for billboards):
            # Detected by: is_bonus + not billboard + :15/:30 + all-day time (12a-12a).
            # Match language by keyword; use standard ROS time from ros_definitions.
            # Description: "{days} BNS {Language} ROS"
            is_ros = (
                line.is_bonus
                and not line.is_billboard
                and line.length in (':15', ':30')
                and etere_time == '12a-12a'
            )
            if is_ros:
                prog_lower = line.program.lower()
                for language, sched in ROS_SCHEDULES.items():
                    if language.lower() in prog_lower:
                        time_from, time_to = EtereClient.parse_time_range(sched['time'])
                        description = f"{etere_days} BNS {language} ROS"
                        print(f"    [ROS] {language} — {sched['time']}, desc: {description!r}")
                        break

            # Sunday 6-7a rule
            adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(etere_days, etere_time)

            # Consolidate consecutive weeks with same spot count
            ranges = EtereClient.consolidate_weeks(
                line.weekly_spots,
                order.week_start_dates,
                flight_end=order.flight_end,
            )

            print(f"\n  Line {line.line_number}: {description}")
            print(f"    Rate: ${line.net_rate}" + (f" → ${rate} (gross-up)" if inputs.get('gross_up') and not line.is_bonus else ""))
            print(f"    Splits into {len(ranges)} Etere line(s)")

            for rng in ranges:
                line_count += 1
                total_spots = rng['spots_per_week'] * rng['weeks']

                print(f"    Creating line {line_count}: "
                      f"{rng['start_date']} – {rng['end_date']} "
                      f"({rng['spots_per_week']} spots/wk × {rng['weeks']} wks = {total_spots})")

                success = etere.add_contract_line(
                    contract_number=contract_number,
                    market=order.market,
                    start_date=rng['start_date'],
                    end_date=rng['end_date'],
                    days=adjusted_days,
                    time_from=time_from,
                    time_to=time_to,
                    description=description,
                    spot_code=spot_code,
                    duration_seconds=duration_s,
                    total_spots=total_spots,
                    spots_per_week=rng['spots_per_week'],
                    rate=float(rate),
                    separation_intervals=separation,
                    is_bookend=False,
                    is_billboard=line.is_billboard,
                )

                if not success:
                    print(f"    ✗ Failed to add line {line_count}")
                    return False

        print(f"\n[GALEFORCE] ✓ All {line_count} Etere lines added successfully")
        return True

    except Exception as exc:
        print(f"\n[GALEFORCE] ✗ Error creating contract: {exc}")
        import traceback
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE TEST ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from browser_automation.etere_session import EtereSession

    pdf = input("Enter path to GaleForce PDF: ").strip()

    with EtereSession() as session:
        session.set_market("NYC")
        success = process_galeforce_order(session.driver, pdf)
        print("\n✓ Done" if success else "\n✗ Failed")
