"""
Impact Marketing Order Automation
Browser automation for entering Impact Marketing agency orders into Etere.

═══════════════════════════════════════════════════════════════════════════════
BUSINESS RULES
═══════════════════════════════════════════════════════════════════════════════

Client & Market:
    - Client: Big Valley Ford ONLY (Customer ID: 252)
    - Market: Always CVC (Central Valley)
    - Master Market: Always NYC (Crossings TV)

Contract Structure:
    - Each PDF page = one quarter = one separate Etere contract
    - Code:          "Impact BVFL 26Q{n}"          (e.g., "Impact BVFL 26Q1")
    - Description:   "Big Valley Ford {mos}"        (e.g., "Big Valley Ford 2601-2603")
    - Customer Ref:  "26Q{n}"                       (e.g., "26Q1")
    - Charge To:     "Customer share indicating agency %"
    - Invoice Header: "Agency"

Lines:
    - Spot code: Paid Commercial (2) for paid lines; BNS (10) for bonus/ROS lines
    - Separation: 15/0/0 (customer=15, event=0, order=0)
    - Bookend (:15 Bookend) sets scheduling type to "Top and Bottom" (value 6)
    - Lines split when weekly spot counts differ across the quarter
    - Sunday 6-7a rule applies (paid programming — remove Sunday from that slot)

Special Line Booking Rules (from parser):
    - Filipino News M-F 4p-5p / Talk 6p-7p   → book M-F 4p-7p
    - Hindi M-F 1p-2p / Hindi Variety Sa-Su   → book M-Su 1p-4p
    - Chinese News M-Sat 6a-7a / M-Sun 7p-9p  → book M-Su 6a-9p
    - ROS lines use standard language block times (from get_ros_schedule())

═══════════════════════════════════════════════════════════════════════════════
ARCHITECTURE
═══════════════════════════════════════════════════════════════════════════════

This file contains ONLY Impact-specific business logic.
All Etere browser interactions are delegated to EtereClient.

Orchestrator interface (matches iGraphix pattern):
    user_input = gather_impact_inputs(pdf_path)       # upfront, no browser
    success    = process_impact_order(driver,          # unattended automation
                                      pdf_path,
                                      user_input)
"""

import math
import os
import sys
from pathlib import Path
from typing import Optional

# ── Path setup (must come before local imports) ───────────────────────────────
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from etere_client import EtereClient
from src.domain.enums import BillingType, OrderType

from browser_automation.parsers.impact_parser import (
    ImpactQuarterOrder,
    ImpactLine,
    analyze_weekly_distribution,
    parse_impact_pdf,
    prompt_for_spot_duration,
)


# ═══════════════════════════════════════════════════════════════════════════════
# DOMAIN CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

CUSTOMER_ID   = 252    # Big Valley Ford — only client for Impact Marketing
MARKET        = "CVC"  # All Impact lines go to Central Valley
MASTER_MARKET = "NYC"  # Crossings TV master market

BILLING = BillingType.CUSTOMER_SHARE_AGENCY

SEPARATION = (15, 0, 0)   # (customer_minutes, event_minutes, order_minutes)

SPOT_CODE_PAID  = 2   # "Paid Commercial"
SPOT_CODE_BONUS = 10  # "BNS"

CUSTOMER_DB_PATH = os.path.join("data", "customers.db")


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOMER DATABASE (self-learning)
# ═══════════════════════════════════════════════════════════════════════════════

def save_new_customer(
    customer_id: str,
    customer_name: str,
    abbreviation: str,
    market: str,
    db_path: str = CUSTOMER_DB_PATH,
) -> None:
    """Save a new customer to the database on first discovery (INSERT OR IGNORE)."""
    try:
        import sqlite3
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO customers
                    (customer_id, customer_name, order_type, abbreviation,
                     default_market, billing_type,
                     separation_customer, separation_event, separation_order,
                     default_code_template, default_desc_template)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    customer_id, customer_name, OrderType.IMPACT.value,
                    abbreviation, market, "agency",
                    SEPARATION[0], SEPARATION[1], SEPARATION[2],
                    "Impact BVFL 26Q{n}",       # e.g. Impact BVFL 26Q1
                    "Big Valley Ford {months}",  # e.g. Big Valley Ford 2601-2603
                )
            )
        print(f"[CUSTOMER DB] ✓ Saved: {customer_name} → ID {customer_id}")
    except Exception as e:
        print(f"[CUSTOMER DB] ✗ Save failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# UPFRONT INPUT COLLECTION
# ═══════════════════════════════════════════════════════════════════════════════

def gather_impact_inputs(pdf_path: str) -> Optional[dict]:
    """
    Gather ALL user inputs BEFORE browser automation starts.

    Workflow:
    1. Parse the PDF to extract all quarterly orders
    2. Prompt for spot duration / bookend (shared across all quarters)
    3. Confirm customer ID (always Big Valley Ford / 252)
    4. For each quarter: show defaults, allow overrides, confirm or skip
    5. Return dict ready for process_impact_order()

    Args:
        pdf_path: Path to Impact Marketing PDF

    Returns:
        Dictionary with all inputs needed for automation, or None if cancelled.
    """
    print("\n" + "=" * 70)
    print("IMPACT MARKETING — UPFRONT INPUT COLLECTION")
    print("=" * 70)

    # ── Parse PDF ─────────────────────────────────────────────────────────────
    print("\n[PARSE] Reading PDF...")
    try:
        orders = parse_impact_pdf(pdf_path)
    except Exception as e:
        print(f"[PARSE] ✗ Failed: {e}")
        return None

    if not orders:
        print("[PARSE] ✗ No quarterly orders found in PDF")
        return None

    print(f"[PARSE] ✓ Found {len(orders)} quarter(s)")

    # ── Spot duration / bookend (shared across all quarters) ─────────────────
    spot_duration, is_bookend = prompt_for_spot_duration()
    bookend_label = " (Bookend)" if is_bookend else ""
    print(f"\n✓ Spot duration: :{spot_duration}{bookend_label}")

    # ── Customer confirmation (always Big Valley Ford / 252) ─────────────────
    print(f"\n[CUSTOMER] Big Valley Ford → ID {CUSTOMER_ID}")
    confirm = input(f"  Use Customer ID {CUSTOMER_ID}? (Y/n): ").strip().lower()
    if confirm and confirm != "y":
        raw = input("  Enter correct Customer ID: ").strip()
        try:
            customer_id = int(raw)
        except ValueError:
            print("[CUSTOMER] ✗ Invalid Customer ID — aborting")
            return None
        save_new_customer(
            customer_id=str(customer_id),
            customer_name="Big Valley Ford",
            abbreviation="BVFL",
            market=MARKET,
        )
    else:
        customer_id = CUSTOMER_ID
        # Self-learning: ensure this customer is in the db
        save_new_customer(
            customer_id=str(customer_id),
            customer_name="Big Valley Ford",
            abbreviation="BVFL",
            market=MARKET,
        )

    print(f"[CUSTOMER] ✓ Customer ID: {customer_id}")

    # ── Quarter selection menu ────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print("SELECT QUARTERS TO PROCESS THIS PASS")
    print("=" * 70)
    for i, order in enumerate(orders, 1):
        flight_start, flight_end = order.get_flight_dates()
        print(f"  [{i}] {order.quarter}  ({flight_start} → {flight_end})  "
              f"{len(order.get_lines_by_type(False))} paid + "
              f"{len(order.get_lines_by_type(True))} bonus")
    print()
    print("  Enter quarter numbers to process (e.g. '1' or '1 3' or 'all')")
    print("  Press Enter to cancel")

    while True:
        raw = input("\n  Your selection: ").strip().lower()
        if not raw:
            print("\n[CANCELLED] No quarters selected")
            return None
        if raw == "all":
            selected_orders = orders
            break
        try:
            indices = [int(x) - 1 for x in raw.replace(",", " ").split()]
            if all(0 <= i < len(orders) for i in indices):
                selected_orders = [orders[i] for i in indices]
                break
            else:
                print(f"  Invalid selection — enter numbers between 1 and {len(orders)}")
        except ValueError:
            print("  Invalid input — enter numbers, ranges, or 'all'")

    print(f"\n  ✓ {len(selected_orders)} quarter(s) selected: "
          f"{', '.join(o.quarter for o in selected_orders)}")

    # ── Per-quarter inputs (only for selected quarters) ───────────────────────
    quarter_inputs: list[dict] = []

    for order in selected_orders:
        flight_start, flight_end = order.get_flight_dates()
        default_code = order.get_default_order_code()
        default_desc = order.get_default_description()

        print(f"\n{'-' * 70}")
        print(f"Quarter:  {order.quarter}")
        print(f"Flight:   {flight_start}  →  {flight_end}")
        print(f"Lines:    {len(order.get_lines_by_type(False))} paid  +  "
              f"{len(order.get_lines_by_type(True))} bonus")
        print()

        raw = input(f"  Order Code    [{default_code}]: ").strip()
        order_code = raw if raw else default_code

        raw = input(f"  Description   [{default_desc}]: ").strip()
        description = raw if raw else default_desc

        quarter_inputs.append({
            "order":       order,
            "order_code":  order_code,
            "description": description,
        })
        print(f"  ✓ Queued: {order_code}")

    if not quarter_inputs:
        print("\n[IMPACT] No quarters selected — nothing to process")
        return None

    print(f"\n[BILLING] ✓ {BILLING.get_charge_to()} / {BILLING.get_invoice_header()}")
    print("\n" + "=" * 70)
    print("INPUT COLLECTION COMPLETE — Ready for automation")
    print("=" * 70)

    return {
        "quarter_inputs": quarter_inputs,
        "customer_id":    customer_id,
        "spot_duration":  spot_duration,
        "is_bookend":     is_bookend,
        "billing":        BILLING,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# BROWSER AUTOMATION
# ═══════════════════════════════════════════════════════════════════════════════

def process_impact_order(
    driver,
    pdf_path: str,
    user_input: dict = None,
) -> bool:
    """
    Process Impact Marketing order with completely unattended automation.

    Matches iGraphix pattern for consistency across all automations.

    Workflow:
    1. Use pre-collected inputs (from orchestrator) OR gather them now
    2. Login once
    3. Create one contract per queued quarter
    4. Add all lines for each contract
    5. Logout
    6. Return success status

    Args:
        driver:     Selenium WebDriver (raw driver, not yet logged in)
        pdf_path:   Path to Impact Marketing PDF
        user_input: Pre-collected inputs from gather_impact_inputs() (optional)

    Returns:
        True if all contracts succeeded, False if any failed.
    """
    # ── Get inputs (pre-collected or gather now) ──────────────────────────────
    if user_input is None:
        user_input = gather_impact_inputs(pdf_path)
        if not user_input:
            return False

    quarter_inputs: list[dict] = user_input["quarter_inputs"]
    customer_id:    int         = user_input["customer_id"]
    spot_duration:  int         = user_input["spot_duration"]
    is_bookend:     bool        = user_input["is_bookend"]
    billing:        BillingType = user_input["billing"]

    # ── Browser automation (completely unattended from here) ──────────────────
    print("\n" + "=" * 70)
    print("STARTING BROWSER AUTOMATION")
    print("=" * 70)

    etere = EtereClient(driver)

    # Login only in standalone mode. When called from the orchestrator,
    # user_input is pre-collected and the shared session is already logged in.
    standalone = user_input is None
    if standalone:
        etere.login()

    all_success = True
    results: list[tuple[str, Optional[str]]] = []

    try:
        for qi in quarter_inputs:
            order: ImpactQuarterOrder = qi["order"]
            order_code:  str          = qi["order_code"]
            description: str          = qi["description"]

            print(f"\n{'=' * 70}")
            print(f"[IMPACT] Processing: {order_code}")
            print(f"{'=' * 70}")

            contract_number = _process_quarter(
                etere=etere,
                order=order,
                order_code=order_code,
                description=description,
                customer_id=customer_id,
                spot_duration=spot_duration,
                is_bookend=is_bookend,
                billing=billing,
            )

            results.append((order_code, contract_number))
            if not contract_number:
                all_success = False

    except Exception as e:
        print(f"\n[ERROR] Browser automation failed: {e}")
        import traceback
        traceback.print_exc()
        all_success = False

    finally:
        # Logout only in standalone mode — orchestrator manages session lifecycle
        if standalone:
            etere.logout()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print("IMPACT AUTOMATION COMPLETE")
    print("=" * 70)
    for code, contract_num in results:
        if contract_num:
            print(f"  ✓  {code}  →  Contract {contract_num}")
        else:
            print(f"  ✗  {code}  →  FAILED")

    return all_success


# ═══════════════════════════════════════════════════════════════════════════════
# QUARTER PROCESSING
# ═══════════════════════════════════════════════════════════════════════════════

def _process_quarter(
    etere: EtereClient,
    order: ImpactQuarterOrder,
    order_code: str,
    description: str,
    customer_id: int,
    spot_duration: int,
    is_bookend: bool,
    billing: BillingType,
) -> Optional[str]:
    """
    Create one Etere contract for a single Impact Marketing quarter.

    Returns the contract number if successful, None otherwise.
    """
    flight_start, flight_end = order.get_flight_dates()
    notes = (
        f"Agency: {order.agency}\n"
        f"Contact: {order.contact} ({order.email})\n"
        f"Quarter: {order.quarter}\n"
        f"Description: {description}"
    )

    # Set master market before creating contract
    etere.set_master_market(MASTER_MARKET)

    contract_number = etere.create_contract_header(
        customer_id=customer_id,
        code=order_code,
        description=description,
        contract_start=flight_start,
        contract_end=flight_end,
        customer_order_ref=f"26Q{order.quarter_num}",
        notes=notes,
        charge_to=billing.get_charge_to(),
        invoice_header=billing.get_invoice_header(),
    )

    if not contract_number:
        print(f"[CONTRACT] ✗ Failed to create contract for {order_code}")
        return None

    print(f"[CONTRACT] ✓ Created: {contract_number}")

    lines_added = 0
    for line in order.lines:
        lines_added += _add_line(
            etere=etere,
            contract_number=contract_number,
            line=line,
            week_dates=order.week_start_dates,
            flight_end=flight_end,
            spot_duration=spot_duration,
            is_bookend=is_bookend,
        )

    print(f"\n[LINES] ✓ {lines_added} Etere line(s) added to contract {contract_number}")
    return contract_number


# ═══════════════════════════════════════════════════════════════════════════════
# LINE ADDITION
# ═══════════════════════════════════════════════════════════════════════════════

def _add_line(
    etere: EtereClient,
    contract_number: str,
    line: ImpactLine,
    week_dates: list[str],
    flight_end: str,
    spot_duration: int,
    is_bookend: bool,
) -> int:
    """
    Add all Etere lines for a single ImpactLine (may split into multiple ranges).

    Returns the number of Etere lines successfully added.
    """
    ranges = analyze_weekly_distribution(
        weekly_spots=line.weekly_spots,
        week_dates=week_dates,
        contract_end_date=flight_end,
    )

    desc           = line.get_description()
    spot_code      = SPOT_CODE_BONUS if line.is_bonus else SPOT_CODE_PAID

    # ROS lines resolve to standard language block times; paid lines use parsed times
    actual_days, actual_time = line.get_ros_schedule()

    # Apply Sunday 6-7a paid programming rule
    adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(actual_days, actual_time)

    # Parse to 24-hour format for Etere
    time_from, time_to = EtereClient.parse_time_range(actual_time)

    if len(ranges) > 1:
        print(f"\n  [LINE] {desc}  →  splits into {len(ranges)} range(s)")
    else:
        print(f"\n  [LINE] {desc}")

    lines_added = 0

    for idx, rng in enumerate(ranges, 1):
        spots_this_range = rng["spots_per_week"] * rng["weeks"]

        day_count = EtereClient._count_active_days(adjusted_days)
        max_daily = (
            math.ceil(rng["spots_per_week"] / day_count)
            if day_count > 0
            else rng["spots_per_week"]
        )

        if len(ranges) > 1:
            print(
                f"    Range {idx}: {rng['start_date']} – {rng['end_date']} | "
                f"{rng['spots_per_week']}/week × {rng['weeks']} weeks = {spots_this_range} spots"
            )

        success = etere.add_contract_line(
            contract_number=contract_number,
            market=MARKET,
            start_date=rng["start_date"],
            end_date=rng["end_date"],
            days=adjusted_days,
            time_from=time_from,
            time_to=time_to,
            description=desc,
            spot_code=spot_code,
            duration_seconds=spot_duration,
            total_spots=spots_this_range,
            spots_per_week=rng["spots_per_week"],
            max_daily_run=max_daily,
            rate=line.rate,
            separation_intervals=SEPARATION,
            is_bookend=is_bookend,
        )

        if success:
            lines_added += 1
            if len(ranges) > 1:
                print(f"      ✓ Range {idx} added")
        else:
            print(f"      ✗ Range {idx} FAILED")

    return lines_added


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("IMPACT AUTOMATION - STANDALONE MODE NOT SUPPORTED")
    print("=" * 70)
    print()
    print("This automation must be run through the orchestrator (main.py)")
    print("which provides the browser session.")
    print()
    print("To process Impact Marketing orders:")
    print("  1. Place PDF in incoming\\ folder")
    print("  2. Run: python main.py")
    print("  3. Select the Impact Marketing order from the menu")
    print("=" * 70)
    sys.exit(1)
