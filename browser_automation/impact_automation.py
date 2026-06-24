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
    success    = process_impact_order(pdf_path,        # unattended automation
                                      pre_gathered_inputs=user_input)
"""

import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Path setup (must come before local imports) ───────────────────────────────
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from etere_client import EtereClient

from browser_automation.parsers.impact_parser import (
    ImpactQuarterOrder,
    analyze_weekly_distribution,
    parse_impact_pdf,
    prompt_for_spot_duration,
)
from src.domain.enums import BillingType, OrderType

# ═══════════════════════════════════════════════════════════════════════════════
# DOMAIN CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

CUSTOMER_ID   = 252    # Big Valley Ford — only client for Impact Marketing
MARKET        = "CVC"  # All Impact lines go to Central Valley
MASTER_MARKET = "NYC"  # Crossings TV master market

BILLING = BillingType.CUSTOMER_SHARE_AGENCY

SEPARATION = (15, 0, 0)   # (customer_minutes, order_minutes, event_minutes)

SPOT_CODE_PAID  = 2   # "Paid Commercial"
SPOT_CODE_BONUS = 10  # "BNS"

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH

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
# DIRECT DB HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_date(s):
    """Parse MM/DD/YYYY, YYYY-MM-DD, or MM/DD/YY to date. Accepts date objects."""
    from datetime import date
    if isinstance(s, date):
        return s
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


def _create_impact_contracts_direct(user_input: dict) -> bool:
    """Enter all selected Impact quarters directly via DB stored procedures (no browser).
    Returns True on success, False on failure.
    """
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    quarter_inputs = user_input["quarter_inputs"]
    customer_id    = user_input["customer_id"]
    spot_duration  = user_input["spot_duration"]
    is_bookend     = user_input["is_bookend"]

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        for qi in quarter_inputs:
            order: ImpactQuarterOrder = qi["order"]
            order_code:  str          = qi["order_code"]
            description: str          = qi["description"]

            flight_start, flight_end = order.get_flight_dates()

            notes = (
                f"Agency: {order.agency}\n"
                f"Contact: {order.contact} ({order.email})\n"
                f"Quarter: {order.quarter}"
            )

            contract_id = client.create_contract_header(
                code=order_code,
                description=description,
                customer_id=int(customer_id),
                contract_date=_parse_date(flight_start),
                contract_end_date=_parse_date(flight_end),
                billing_type="agency",
                note=notes,
                allow_rename=True,
            )
            print(f"[IMPACT DIRECT] ✓ Contract ID={contract_id} ({order_code})")

            lines_added = 0
            for line in order.lines:
                ranges = analyze_weekly_distribution(
                    weekly_spots=line.weekly_spots,
                    week_dates=order.week_start_dates,
                    contract_end_date=flight_end,
                )

                desc         = line.get_description()
                booking_code = SPOT_CODE_BONUS if line.is_bonus else SPOT_CODE_PAID
                actual_days, actual_time = line.get_ros_schedule()
                adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(actual_days, actual_time)
                time_from, time_to = EtereClient.parse_time_range(actual_time)
                time_range = f"{time_from}-{time_to}"

                for rng in ranges:
                    spots_per_week   = rng["spots_per_week"]
                    spots_this_range = spots_per_week * rng["weeks"]
                    rate             = line.rate

                    if is_bookend:
                        if spots_per_week % 2 != 0 or spots_this_range % 2 != 0:
                            raise ValueError(
                                f"BOOKEND ERROR: '{desc}' has an odd spot count "
                                f"(spw={spots_per_week}, total={spots_this_range})."
                            )
                        spots_per_week   = spots_per_week   // 2
                        spots_this_range = spots_this_range // 2
                        rate             = rate * 2

                    day_count = EtereClient._count_active_days(adjusted_days)
                    max_daily = (
                        math.ceil(spots_per_week / day_count)
                        if day_count > 0 else spots_per_week
                    )

                    client.add_contract_line(
                        market=MARKET,
                        days=adjusted_days,
                        time_range=time_range,
                        description=desc,
                        rate=float(rate),
                        total_spots=spots_this_range,
                        spots_per_week=spots_per_week,
                        date_from=_parse_date(rng["start_date"]),
                        date_to=_parse_date(rng["end_date"]),
                        duration=str(spot_duration),
                        is_bonus=line.is_bonus,
                        booking_code=booking_code,
                        separation_intervals=SEPARATION,
                        is_bookend=is_bookend,
                        max_daily_run=max_daily,
                        contract_id=contract_id,
                    )
                    lines_added += 1

            print(f"[IMPACT DIRECT] ✓ {lines_added} lines added to {order_code}")

        conn.commit()
        conn.close()
        return True

    except Exception as exc:
        print(f"[IMPACT DIRECT] ✗ {exc}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# BROWSER AUTOMATION
# ═══════════════════════════════════════════════════════════════════════════════

def process_impact_order(
    pdf_path: str,
    shared_session=None,
    pre_gathered_inputs=None,
) -> bool:
    """
    Process Impact Marketing order via direct DB entry.

    Args:
        pdf_path:             Path to Impact Marketing PDF
        shared_session:       Unused (retained for interface compatibility)
        pre_gathered_inputs:  Pre-collected inputs from gather_impact_inputs() (optional)

    Returns:
        True if all contracts succeeded, False if any failed.
    """
    user_input = pre_gathered_inputs
    if user_input is None:
        user_input = gather_impact_inputs(pdf_path)
        if not user_input:
            return False

    return _create_impact_contracts_direct(user_input)


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
