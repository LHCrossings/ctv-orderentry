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

import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from etere_client import EtereClient

# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOMER DATABASE ACCESS
# ═══════════════════════════════════════════════════════════════════════════════
from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
from browser_automation.parsers.igraphix_parser import (
    IGraphixOrder,
    parse_igraphix_pdf,
)
from src.domain.enums import BillingType, OrderType


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
        Tuple of (customer_minutes, order_minutes, event_minutes)
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

    print("\n[CONTRACT]")
    contract_code = input(f"  Code [{suggested_code}]: ").strip() or suggested_code
    description = input(f"  Description [{suggested_desc}]: ").strip() or suggested_desc

    # Customer Order Ref = just the purchase number
    customer_order_ref = order.purchase_number

    # Notes = channel description from PDF
    notes = order.channel_description

    # Billing (UNIVERSAL for ALL agency orders)
    billing = BillingType.CUSTOMER_SHARE_AGENCY
    print("\n[BILLING] ✓ Customer share indicating agency % / Agency")

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
# DIRECT DB ENTRY
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_date_yy(date_str: str):
    """Convert 'MM/DD/YY' string to date object (2-digit year)."""
    return datetime.strptime(date_str.strip(), '%m/%d/%y').date()


def _secs_to_duration(secs: int) -> str:
    """Convert integer seconds to HH:MM:SS:FF string for EtereDirectClient."""
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:00"


def process_igraphix_order_direct(user_input: dict) -> Optional[str]:
    """
    Enter an iGraphix order directly via DB stored procedures (no browser).

    Returns COD_CONTRATTO string on success, None on failure (rolls back fully).
    """
    from browser_automation.etere_direct_client import EtereDirectClient
    from browser_automation.etere_direct_client import connect as db_connect

    order: IGraphixOrder = user_input['order']

    print("\n" + "=" * 70)
    print("IGRAPHIX DIRECT DB ENTRY")
    print("=" * 70)

    flight_start = _parse_date_yy(order.flight_start)
    flight_end   = _parse_date_yy(order.flight_end)

    conn = None
    try:
        conn = db_connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=user_input['contract_code'],
            description=user_input['contract_description'],
            customer_id=int(user_input['customer_id']),
            contract_date=flight_start,
            contract_end_date=flight_end,
            contract_type=1,
            billing_type="agency",
            note=user_input['notes'],
            customer_order_ref=user_input['customer_order_ref'],
            master_market="NYC",
        )
        print(f"[CONTRACT] ✓ ID={contract_id}")

        duration_str = _secs_to_duration(order.spot_duration)

        print(f"\n[LINES] Adding {len(order.ad_codes)} line(s)...")

        for i, ad_code in enumerate(order.ad_codes, 1):
            is_bonus = ad_code.is_bonus
            rate = 0.0 if is_bonus else order.rate_per_spot

            if is_bonus:
                ros_days, ros_time_raw = _get_ros_schedule(order.language)
                days = ros_days
                time_raw = ros_time_raw
            else:
                days = order.paid_days
                time_raw = order.paid_time

            days, _ = EtereClient.check_sunday_6_7a_rule(days, time_raw)
            time_from, time_to = EtereClient.parse_time_range(time_raw)
            time_range = f"{time_from}-{time_to}"

            if is_bonus:
                line_desc = f"BNS {order.language} {time_raw} [{ad_code.ad_code}]"
            else:
                line_desc = f"{order.language} {time_raw} [{ad_code.ad_code}]"

            max_daily = _calculate_max_daily(
                ad_code.spots, days, ad_code.start_date, ad_code.end_date
            )
            separation = get_separation_intervals(order.language, is_bonus)
            date_from = _parse_date_yy(ad_code.start_date)
            date_to   = _parse_date_yy(ad_code.end_date)

            status_label = "BNS" if is_bonus else "PAID"
            print(f"\n[LINE {i}] {status_label} [{ad_code.ad_code}] {ad_code.description}")
            print(f"  Days: {days}, Time: {time_raw}  ({time_from}–{time_to})")
            print(f"  Spots: {ad_code.spots}  Rate: ${rate:.2f}  Max/day: {max_daily}")
            print(f"  Flight: {ad_code.start_date} – {ad_code.end_date}")
            print(f"  Separation: {separation}")

            line_id = client.add_contract_line(
                market=order.market,
                days=days,
                time_range=time_range,
                description=line_desc,
                rate=rate,
                total_spots=ad_code.spots,
                spots_per_week=0,
                max_daily_run=max_daily,
                date_from=date_from,
                date_to=date_to,
                duration=duration_str,
                is_bonus=is_bonus,
                booking_code=10 if is_bonus else 2,
                separation_intervals=separation,
            )
            print(f"  line_id={line_id}")

        conn.commit()

        ph = client._ph
        cur = conn.cursor()
        cur.execute(
            f"SELECT COD_CONTRATTO FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = {ph}",
            (contract_id,)
        )
        row = cur.fetchone()
        contract_code = str(row[0]).strip() if row else str(contract_id)
        conn.close()
        print(f"\n[DIRECT] ✓ Contract {contract_code} committed.")
        return contract_code

    except Exception as exc:
        print(f"\n[DIRECT] ✗ Error: {exc}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


def process_igraphix_order(pdf_path: str, user_input: dict = None) -> bool:
    """
    Process iGraphix order via direct DB entry.

    Args:
        pdf_path: Path to iGraphix PDF
        user_input: Pre-collected inputs from orchestrator (optional)

    Returns:
        True if successful, False otherwise
    """
    if user_input is None:
        user_input = gather_igraphix_inputs(pdf_path)
        if not user_input:
            return False

    contract_code = process_igraphix_order_direct(user_input)
    return bool(contract_code)


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
