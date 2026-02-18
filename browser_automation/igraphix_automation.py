"""
iGraphix Order Automation
Browser automation for entering iGraphix agency orders into Etere.

═══════════════════════════════════════════════════════════════════════════════
IGRAPHIX BUSINESS RULES
═══════════════════════════════════════════════════════════════════════════════

Known Customers:
    1. Pechanga Resort Casino (ID: 26) → LAX market ONLY
    2. Sky River Casino (ID: 191) → SFO or CVC markets

Billing (Universal for ALL iGraphix):
    - Charge To: "Customer share indicating agency %"
    - Invoice Header: "Agency"

Separation Intervals (Language-Specific):
    - Filipino Paid:    (30, 0, 0)
    - Filipino Bonus:   (30, 0, 0)
    - Vietnamese Paid:  (30, 0, 0)
    - Vietnamese Bonus: (20, 0, 0)
    - Hmong Paid:       (20, 0, 0)
    - Hmong Bonus:      (15, 0, 0)
    - All Others:       (30, 0, 0)

Contract Format:
    Pechanga:   Code: "IG Pechanga {purchase_number}"
                Desc: "Pechanga Resort Casino {purchase_number}"
    Sky River:  Code: "IG SRC {purchase_number} {lang_abbrev} {market_abbrev}"
                Desc: "Sky River Casino Est {purchase_number} {lang_abbrev} {market_abbrev}"

Line Structure:
    - One Etere line per ad code entry (after paid/bonus allocation split)
    - Paid lines: language block time + rate
    - Bonus lines: ROS time + rate=0.0
    - spots_per_week = 0 (iGraphix uses total spots, not weekly)
    - Description format: "[BNS] {language} {time} [{ad_code}]"

Customer Order Ref:
    - Just the purchase number (e.g., "30141")

Notes:
    - Channel description from PDF

═══════════════════════════════════════════════════════════════════════════════
IMPORTS
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import math
from pathlib import Path
from datetime import datetime
from typing import Optional

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from etere_client import EtereClient
from src.domain.enums import BillingType, OrderType, SeparationInterval

from browser_automation.parsers.igraphix_parser import (
    parse_igraphix_pdf,
    IGraphixOrder,
    IGraphixAdCode,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOMER DATABASE ACCESS
# ═══════════════════════════════════════════════════════════════════════════════

CUSTOMER_DB_PATH = os.path.join("data", "customers.db")


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
                     separation_customer, separation_event, separation_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (customer_id, customer_name, OrderType.IGRAPHIX.value,
                 abbreviation, market, "agency", 30, 0, 0)
            )
        print(f"[CUSTOMER DB] ✓ Saved: {customer_name} → ID {customer_id}")
    except Exception as e:
        print(f"[CUSTOMER DB] ✗ Save failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# SEPARATION INTERVALS (Language + Paid/Bonus specific)
# ═══════════════════════════════════════════════════════════════════════════════

def get_separation_intervals(language: str, is_bonus: bool) -> tuple[int, int, int]:
    """
    Get separation intervals for iGraphix by language and line type.

    Returns:
        Tuple of (customer_minutes, event_minutes, order_minutes)
    """
    intervals = {
        'Filipino':   {'paid': 30, 'bonus': 30},
        'Vietnamese': {'paid': 30, 'bonus': 20},
        'Hmong':      {'paid': 20, 'bonus': 15},
    }
    lang_map = intervals.get(language, {'paid': 30, 'bonus': 30})
    customer = lang_map['bonus'] if is_bonus else lang_map['paid']
    return (customer, 0, 0)


# ═══════════════════════════════════════════════════════════════════════════════
# UPFRONT INPUT COLLECTION
# ═══════════════════════════════════════════════════════════════════════════════

def gather_igraphix_inputs(pdf_path: str) -> Optional[dict]:
    """
    Gather ALL user inputs BEFORE browser automation starts.

    Workflow:
    1. Parse the PDF to extract order details
    2. Show parsed results and confirm
    3. Allow overrides for contract code/description
    4. Prepare all data needed for unattended automation

    Args:
        pdf_path: Path to iGraphix PDF

    Returns:
        Dictionary with all inputs needed for automation, or None if cancelled
    """
    print("\n" + "=" * 70)
    print("IGRAPHIX ORDER - UPFRONT INPUT COLLECTION")
    print("=" * 70)

    # Parse PDF
    print("\n[PARSE] Reading PDF...")
    try:
        order = parse_igraphix_pdf(pdf_path)
    except Exception as e:
        print(f"[PARSE] ✗ Failed: {e}")
        return None

    print(f"[PARSE] ✓ Purchase #: {order.purchase_number}")
    print(f"[PARSE] ✓ Client: {order.client}")
    print(f"[PARSE] ✓ Market: {order.market}")
    print(f"[PARSE] ✓ Language: {order.language}")
    print(f"[PARSE] ✓ Paid spots: {order.paid_spots} × ${order.rate_per_spot:.2f}")
    print(f"[PARSE] ✓ Bonus spots: {order.bonus_spots}")
    print(f"[PARSE] ✓ Duration: {order.spot_duration}s")
    print(f"[PARSE] ✓ Flight: {order.flight_start} – {order.flight_end}")
    print(f"[PARSE] ✓ Ad codes: {len(order.ad_codes)}")

    # Show ad code allocation
    print("\n[AD CODES] Paid/Bonus allocation:")
    for ac in order.ad_codes:
        status = "BNS" if ac.is_bonus else "PAID"
        print(f"  {status} [{ac.ad_code}] {ac.description} — {ac.spots} spots  {ac.start_date}–{ac.end_date}")

    # Customer ID confirmation
    print(f"\n[CUSTOMER] Detected: {order.client} → ID {order.customer_id}")
    confirm = input(f"  Use Customer ID {order.customer_id}? (Y/n): ").strip().lower()
    if confirm and confirm != 'y':
        new_id = input("  Enter correct Customer ID: ").strip()
        try:
            customer_id = int(new_id)
        except ValueError:
            print("[CUSTOMER] ✗ Invalid Customer ID")
            return None
        # Save to db for future discovery
        save_new_customer(
            customer_id=str(customer_id),
            customer_name=order.client,
            abbreviation=order.client.split()[0],
            market=order.market,
        )
    else:
        customer_id = order.customer_id
        # Self-learning: ensure this customer is in db
        save_new_customer(
            customer_id=str(customer_id),
            customer_name=order.client,
            abbreviation=order.client.split()[0],
            market=order.market,
        )

    print(f"[CUSTOMER] ✓ Customer ID: {customer_id}")

    # Contract code & description with smart defaults
    suggested_code = order.get_contract_code()
    suggested_desc = order.get_contract_description()

    print(f"\n[CONTRACT]")
    contract_code = input(f"  Code [{suggested_code}]: ").strip() or suggested_code
    description = input(f"  Description [{suggested_desc}]: ").strip() or suggested_desc

    # Customer Order Ref = just the purchase number
    customer_order_ref = order.purchase_number

    # Notes = channel description from PDF
    notes = order.channel_description

    # Billing (UNIVERSAL for ALL agency orders)
    billing = BillingType.CUSTOMER_SHARE_AGENCY
    print(f"\n[BILLING] ✓ Customer share indicating agency % / Agency")

    print("\n" + "=" * 70)
    print("INPUT COLLECTION COMPLETE - Ready for automation")
    print("=" * 70)

    return {
        'order': order,
        'customer_id': customer_id,
        'market': order.market,
        'contract_code': contract_code,
        'contract_description': description,
        'customer_order_ref': customer_order_ref,
        'notes': notes,
        'billing': billing,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# BROWSER AUTOMATION
# ═══════════════════════════════════════════════════════════════════════════════

def process_igraphix_order(
    driver,
    pdf_path: str,
    user_input: dict = None
) -> bool:
    """
    Process iGraphix order with completely unattended automation.

    Matches Daviselen pattern for consistency across all automations.

    Workflow:
    1. Use pre-collected inputs (from orchestrator) OR gather them now
    2. Create contract header
    3. Add one contract line per ad code (after paid/bonus split)
    4. Return success status

    Args:
        driver: Selenium WebDriver (raw driver, not session)
        pdf_path: Path to iGraphix PDF
        user_input: Pre-collected inputs from orchestrator (optional)

    Returns:
        True if successful, False otherwise
    """
    # ═══════════════════════════════════════════════════════════════
    # GET INPUTS (pre-collected OR gather now)
    # ═══════════════════════════════════════════════════════════════

    if user_input is None:
        user_input = gather_igraphix_inputs(pdf_path)
        if not user_input:
            return False

    order: IGraphixOrder = user_input['order']

    # ═══════════════════════════════════════════════════════════════
    # BROWSER AUTOMATION (COMPLETELY UNATTENDED)
    # ═══════════════════════════════════════════════════════════════

    print("\n" + "=" * 70)
    print("STARTING BROWSER AUTOMATION")
    print("=" * 70)

    all_success = True

    # Create Etere client (same pattern as Daviselen/TCAA)
    etere = EtereClient(driver)

    try:
        # Master market already set by session (NYC for Crossings TV)

        # ═══════════════════════════════════════════════════════════
        # CREATE CONTRACT HEADER
        # ═══════════════════════════════════════════════════════════

        billing = user_input['billing']

        contract_number = etere.create_contract_header(
            customer_id=int(user_input['customer_id']),
            code=user_input['contract_code'],
            description=user_input['contract_description'],
            contract_start=order.flight_start,
            contract_end=order.flight_end,
            customer_order_ref=user_input['customer_order_ref'],
            notes=user_input['notes'],
            charge_to=billing.get_charge_to(),
            invoice_header=billing.get_invoice_header(),
        )

        if not contract_number:
            print("[CONTRACT] ✗ Failed to create contract")
            return False

        print(f"[CONTRACT] ✓ Created: {contract_number}")

        # ═══════════════════════════════════════════════════════════
        # ADD CONTRACT LINES (one per ad code after allocation split)
        # ═══════════════════════════════════════════════════════════

        print(f"\n[LINES] Adding {len(order.ad_codes)} line(s)...")

        for i, ad_code in enumerate(order.ad_codes, 1):
            is_bonus = ad_code.is_bonus
            spot_code = 10 if is_bonus else 2  # BNS=10, Paid Commercial=2
            rate = 0.0 if is_bonus else order.rate_per_spot

            # Time and days
            if is_bonus:
                # ROS schedule for this language
                ros_days, ros_time_raw = _get_ros_schedule(order.language)
                days = ros_days
                time_raw = ros_time_raw
            else:
                days = order.paid_days
                time_raw = order.paid_time

            # Apply Sunday 6-7a rule
            days, _ = EtereClient.check_sunday_6_7a_rule(days, time_raw)

            # Parse time to 24h format
            time_from, time_to = EtereClient.parse_time_range(time_raw)

            # Build description: [BNS] {language} {time} [{ad_code}]
            if is_bonus:
                line_desc = f"BNS {order.language} {time_raw} [{ad_code.ad_code}]"
            else:
                line_desc = f"{order.language} {time_raw} [{ad_code.ad_code}]"

            # Max daily run: ceil(total_spots / available_days)
            max_daily = _calculate_max_daily(
                ad_code.spots, days, ad_code.start_date, ad_code.end_date
            )

            # Language-specific separation intervals
            separation = get_separation_intervals(order.language, is_bonus)

            status_label = "BNS" if is_bonus else "PAID"
            print(f"\n[LINE {i}] {status_label} [{ad_code.ad_code}] {ad_code.description}")
            print(f"  Days: {days}, Time: {time_raw}  ({time_from}–{time_to})")
            print(f"  Spots: {ad_code.spots}  Rate: ${rate:.2f}  Max/day: {max_daily}")
            print(f"  Flight: {ad_code.start_date} – {ad_code.end_date}")
            print(f"  Separation: {separation}")

            success = etere.add_contract_line(
                contract_number=contract_number,
                market=order.market,
                start_date=ad_code.start_date,
                end_date=ad_code.end_date,
                days=days,
                time_from=time_from,
                time_to=time_to,
                description=line_desc,
                spot_code=spot_code,
                duration_seconds=order.spot_duration,
                total_spots=ad_code.spots,
                spots_per_week=0,       # iGraphix: no weekly target, total only
                max_daily_run=max_daily,
                rate=rate,
                separation_intervals=separation,
            )

            if not success:
                print(f"  [LINE {i}] ✗ Failed")
                all_success = False

        print(f"\n[COMPLETE] Contract {contract_number} — {len(order.ad_codes)} lines processed")

    except Exception as e:
        print(f"\n[ERROR] Browser automation failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    return all_success


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_ros_schedule(language: str) -> tuple[str, str]:
    """Get ROS days and time string for a language."""
    schedules = {
        'Filipino':   ('M-Su', '4p-7p'),
        'Vietnamese': ('M-Su', '11a-1p'),
        'Hmong':      ('Sa-Su', '6p-8p'),
        'Korean':     ('M-Su', '8a-10a'),
        'Chinese':    ('M-Su', '6a-11:59p'),
        'Mandarin':   ('M-Su', '6a-11:59p'),
        'Cantonese':  ('M-Su', '6a-11:59p'),
        'South Asian':('M-Su', '1p-4p'),
        'Punjabi':    ('M-Su', '1p-4p'),
        'Japanese':   ('M-F', '10a-11a'),
    }
    return schedules.get(language, ('M-Su', '6a-11:59p'))


def _calculate_max_daily(
    total_spots: int,
    days_pattern: str,
    start_date: str,
    end_date: str,
) -> int:
    """
    Calculate max daily spots: ceil(total_spots / available_days).

    Uses actual calendar days, adjusted for the day pattern.
    """
    start_dt = datetime.strptime(start_date, '%m/%d/%y')
    end_dt = datetime.strptime(end_date, '%m/%d/%y')
    total_days = (end_dt - start_dt).days + 1

    # Fraction of days active per pattern
    fractions = {
        'M-F':   5 / 7,
        'Sa-Su': 2 / 7,
        'M-Sa':  6 / 7,
    }
    fraction = fractions.get(days_pattern, 1.0)
    available_days = max(1, int(total_days * fraction))

    return max(1, math.ceil(total_spots / available_days))


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("IGRAPHIX AUTOMATION - STANDALONE MODE NOT SUPPORTED")
    print("=" * 70)
    print()
    print("This automation must be run through the orchestrator (main.py)")
    print("which provides the browser session.")
    print()
    print("To process iGraphix orders:")
    print("  1. Place PDF in incoming\\ folder")
    print("  2. Run: python main.py")
    print("  3. Select the iGraphix order from the menu")
    print("=" * 70)
    sys.exit(1)
