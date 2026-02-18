"""
Impact Marketing Order Automation
Handles Etere entry for Impact Marketing agency orders.

═══════════════════════════════════════════════════════════════════════════════
BUSINESS RULES
═══════════════════════════════════════════════════════════════════════════════

CLIENT & MARKET
- Client: Big Valley Ford ONLY (Customer ID: 252)
- Market: Always CVC (Central Valley)
- Master Market: Always NYC (Crossings TV)

CONTRACT STRUCTURE
- Each PDF page = one quarter = one separate Etere contract
- Order Code:    "Impact BVFL 26Q{n}"           (e.g., "Impact BVFL 26Q1")
- Description:   "Big Valley Ford {mos}"         (e.g., "Big Valley Ford 2601-2603")
- Customer Ref:  "26Q{n}"                        (e.g., "26Q1")
- Charge To:     "Customer share indicating agency %"
- Invoice Header: "Agency"

LINES
- Spot code: Paid Commercial (2) for paid lines; BNS (10) for bonus/ROS lines
- Separation: 15/0/0 (customer=15, event=0, order=0)
- Bookend (:15 Bookend) sets scheduling type to "Top and Bottom" (value 6)
- Lines split when weekly spot counts differ across the quarter
- Sunday 6-7a rule applies (paid programming - remove Sunday from that slot)

SPECIAL LINE BOOKING RULES (from parser):
- Filipino News M-F 4p-5p / Talk 6p-7p → book M-F 4p-7p
- Hindi M-F 1p-2p / Hindi Variety Sat-Sun 1p-4p → book M-Su 1p-4p
- Chinese News M-Sat 6a-7a / M-Sun 7p-9p → book M-Su 6a-9p
- ROS lines use standard language block times (from get_ros_schedule())

═══════════════════════════════════════════════════════════════════════════════
ARCHITECTURE
═══════════════════════════════════════════════════════════════════════════════

This file contains ONLY Impact-specific business logic.
All Etere browser interactions are delegated to EtereClient.

Workflow (upfront input gathering → unattended automation):
  1. Parse PDF
  2. Gather all user input (codes, descriptions, duration, bookend)
  3. Login once, process all selected quarters, logout
"""

import math
from dataclasses import dataclass
from typing import Optional
from selenium import webdriver

from etere_client import EtereClient
from parsers.impact_parser import (
    ImpactQuarterOrder,
    ImpactLine,
    analyze_weekly_distribution,
    get_language_block_prefix,
    parse_impact_pdf,
    prompt_for_spot_duration,
)


# ═══════════════════════════════════════════════════════════════════════════
# DOMAIN CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

CUSTOMER_ID = 252          # Big Valley Ford — only client for Impact Marketing
MARKET = "CVC"             # All Impact lines go to Central Valley
MASTER_MARKET = "NYC"      # Crossings TV master market

CHARGE_TO = "Customer share indicating agency %"
INVOICE_HEADER = "Agency"

SEPARATION_CUSTOMER = 15   # Minutes between spots for same customer
SEPARATION_EVENT = 0
SEPARATION_ORDER = 0

SPOT_CODE_PAID = 2         # "Paid Commercial"
SPOT_CODE_BONUS = 10       # "BNS"


# ═══════════════════════════════════════════════════════════════════════════
# INPUT GATHERING (upfront — runs before any browser automation)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ImpactOrderInput:
    """All user-supplied inputs for a single quarter, collected before automation starts."""
    order: ImpactQuarterOrder
    order_code: str
    description: str
    spot_duration: int       # seconds: 15, 30, 45, or 60
    is_bookend: bool         # :15 Bookends → scheduling type "Top and Bottom"


def gather_inputs_for_all_quarters(
    orders: list[ImpactQuarterOrder],
) -> list[ImpactOrderInput]:
    """
    Collect all user inputs for every quarter before automation begins.

    Presents defaults derived from the parsed PDF; user can accept or override.
    Returns only the quarters the user wants to process.
    """
    print("\n" + "=" * 70)
    print("IMPACT MARKETING — ORDER ENTRY")
    print("=" * 70)
    print(f"Found {len(orders)} quarter(s) in PDF.\n")

    # Spot duration / bookend is shared across all quarters in one PDF
    spot_duration, is_bookend = prompt_for_spot_duration()
    bookend_label = " (Bookend)" if is_bookend else ""
    print(f"\n✓ Spot duration: :{spot_duration}{bookend_label}\n")

    inputs: list[ImpactOrderInput] = []

    for order in orders:
        flight_start, flight_end = order.get_flight_dates()
        default_code = order.get_default_order_code()
        default_desc = order.get_default_description()
        default_ref  = f"26Q{order.quarter_num}"

        print("-" * 70)
        print(f"Quarter:  {order.quarter}")
        print(f"Flight:   {flight_start}  →  {flight_end}")
        print(f"Lines:    {len(order.get_lines_by_type(False))} paid  +  "
              f"{len(order.get_lines_by_type(True))} bonus")
        print()

        # --- Order Code ---
        raw = input(f"  Order Code    [{default_code}]: ").strip()
        order_code = raw if raw else default_code

        # --- Description ---
        raw = input(f"  Description   [{default_desc}]: ").strip()
        description = raw if raw else default_desc

        # --- Process this quarter? ---
        process = input(f"\n  Process {order.quarter}? [Y/n]: ").strip().lower()
        if process == "n":
            print(f"  → Skipping {order.quarter}")
            continue

        inputs.append(ImpactOrderInput(
            order=order,
            order_code=order_code,
            description=description,
            spot_duration=spot_duration,
            is_bookend=is_bookend,
        ))
        print(f"  ✓ Queued: {order_code}")

    return inputs


# ═══════════════════════════════════════════════════════════════════════════
# AUTOMATION ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

def run_impact_automation(pdf_path: str, driver: webdriver.Chrome) -> None:
    """
    Full Impact Marketing automation workflow.

    1. Parse PDF
    2. Gather all inputs upfront
    3. Login once
    4. Process all queued quarters
    5. Logout

    Args:
        pdf_path: Absolute path to the Impact Marketing PDF
        driver:   Selenium WebDriver instance (not yet logged in)
    """
    # ── Step 1: Parse ──────────────────────────────────────────────────────
    print(f"\n[IMPACT] Parsing PDF: {pdf_path}")
    orders = parse_impact_pdf(pdf_path)

    if not orders:
        print("[IMPACT] ✗ No orders found in PDF — aborting.")
        return

    # ── Step 2: Gather inputs (upfront, no browser yet) ───────────────────
    order_inputs = gather_inputs_for_all_quarters(orders)

    if not order_inputs:
        print("\n[IMPACT] Nothing to process — exiting.")
        return

    print(f"\n[IMPACT] {len(order_inputs)} quarter(s) queued for entry.")
    input("\nPress Enter to open the browser and begin automation...")

    # ── Step 3: Login ─────────────────────────────────────────────────────
    client = EtereClient(driver)
    client.login()

    # ── Step 4: Process each quarter ──────────────────────────────────────
    results: list[tuple[str, Optional[str]]] = []   # (order_code, contract_number | None)

    for order_input in order_inputs:
        print(f"\n{'=' * 70}")
        print(f"[IMPACT] Processing: {order_input.order_code}")
        print(f"{'=' * 70}")

        contract_number = _process_quarter(client, order_input)
        results.append((order_input.order_code, contract_number))

    # ── Step 5: Logout ────────────────────────────────────────────────────
    client.logout()

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print("IMPACT AUTOMATION COMPLETE")
    print("=" * 70)
    for code, contract_num in results:
        if contract_num:
            print(f"  ✓  {code}  →  Contract {contract_num}")
        else:
            print(f"  ✗  {code}  →  FAILED")


# ═══════════════════════════════════════════════════════════════════════════
# QUARTER PROCESSING
# ═══════════════════════════════════════════════════════════════════════════

def _process_quarter(
    client: EtereClient,
    order_input: ImpactOrderInput,
) -> Optional[str]:
    """
    Create one Etere contract for a single Impact Marketing quarter.

    Returns the contract number if successful, None otherwise.
    """
    order = order_input.order
    flight_start, flight_end = order.get_flight_dates()
    notes = (
        f"Agency: {order.agency}\n"
        f"Contact: {order.contact} ({order.email})\n"
        f"Quarter: {order.quarter}\n"
        f"Description: {order_input.description}"
    )

    # ── Set master market ─────────────────────────────────────────────────
    client.set_master_market(MASTER_MARKET)

    # ── Create contract header ────────────────────────────────────────────
    contract_number = client.create_contract_header(
        customer_id=CUSTOMER_ID,
        code=order_input.order_code,
        description=order_input.description,
        contract_start=flight_start,
        contract_end=flight_end,
        customer_order_ref=f"26Q{order.quarter_num}",
        notes=notes,
        charge_to=CHARGE_TO,
        invoice_header=INVOICE_HEADER,
    )

    if not contract_number:
        print(f"[IMPACT] ✗ Failed to create contract for {order_input.order_code}")
        return None

    print(f"[IMPACT] ✓ Contract created: {contract_number}")

    # ── Add lines ─────────────────────────────────────────────────────────
    lines_added = 0

    for line in order.lines:
        count = _add_line(
            client=client,
            contract_number=contract_number,
            line=line,
            week_dates=order.week_start_dates,
            flight_end=flight_end,
            spot_duration=order_input.spot_duration,
            is_bookend=order_input.is_bookend,
        )
        lines_added += count

    print(f"\n[IMPACT] ✓ {lines_added} Etere line(s) added to contract {contract_number}")
    return contract_number


# ═══════════════════════════════════════════════════════════════════════════
# LINE ADDITION
# ═══════════════════════════════════════════════════════════════════════════

def _add_line(
    client: EtereClient,
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
    # Split line into date ranges where weekly spot count is consistent
    ranges = analyze_weekly_distribution(
        weekly_spots=line.weekly_spots,
        week_dates=week_dates,
        contract_end_date=flight_end,
    )

    desc = line.get_description()
    block_prefixes = get_language_block_prefix(line.language) if line.language else []

    # For ROS lines use standard language block times; otherwise use parsed times
    actual_days, actual_time = line.get_ros_schedule()

    # Apply Sunday 6-7a paid programming rule
    adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(actual_days, actual_time)

    # Parse time to 24-hour format using EtereClient universal parser
    time_from, time_to = EtereClient.parse_time_range(actual_time)

    spot_code = SPOT_CODE_BONUS if line.is_bonus else SPOT_CODE_PAID

    if len(ranges) > 1:
        print(f"\n  [LINE] {desc}  →  splits into {len(ranges)} range(s)")
    else:
        print(f"\n  [LINE] {desc}")

    lines_added = 0

    for idx, rng in enumerate(ranges, 1):
        spots_this_range = rng["spots_per_week"] * rng["weeks"]

        # Calculate max_daily_run for this range's adjusted days
        day_count = EtereClient._count_active_days(adjusted_days)
        max_daily = math.ceil(rng["spots_per_week"] / day_count) if day_count > 0 else rng["spots_per_week"]

        if len(ranges) > 1:
            print(f"    Range {idx}: {rng['start_date']} – {rng['end_date']} | "
                  f"{rng['spots_per_week']}/week × {rng['weeks']} weeks = {spots_this_range} spots")

        success = client.add_contract_line(
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
            block_prefixes=block_prefixes if block_prefixes else None,
            separation_intervals=(SEPARATION_CUSTOMER, SEPARATION_EVENT, SEPARATION_ORDER),
            is_bookend=is_bookend,
        )

        if success:
            lines_added += 1
            if len(ranges) > 1:
                print(f"      ✓ Range {idx} added")
        else:
            print(f"      ✗ Range {idx} FAILED")

    return lines_added


# ═══════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options

    if len(sys.argv) < 2:
        print("Usage: python impact_automation.py <path_to_pdf>")
        sys.exit(1)

    pdf_path = sys.argv[1]

    options = Options()
    options.add_argument("--start-maximized")

    driver = webdriver.Chrome(options=options)

    try:
        run_impact_automation(pdf_path=pdf_path, driver=driver)
    finally:
        driver.quit()
