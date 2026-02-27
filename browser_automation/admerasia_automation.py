"""
Admerasia Order Automation
Browser automation for entering Admerasia agency orders into Etere.

═══════════════════════════════════════════════════════════════════════════════
ADMERASIA BUSINESS RULES
═══════════════════════════════════════════════════════════════════════════════

Known Customers:
    1. McDonald's (ID: 42) → ALL markets
       - All Admerasia orders are McDonald's
       - Separation: 3, 5, 0 (customer=3, event=0, order=5)

Billing (Universal for ALL Admerasia):
    - Charge To: "Customer share indicating agency %"
    - Invoice Header: "Agency"

Contract Format:
    - Code: "Admerasia McD {estimate}"
      where estimate = "{prefix}{market_code} {YYMM}"
      Example: "Admerasia McD 14HO 2602"
    - Description: "McDonald's Est {prefix} {etere_market} {YYMM}"
      Example: "McDonald's Est 14 HOU 2602"

Rate Handling:
    - PDF rates are NET
    - Must gross up: net / 0.85
    - Parser already does this in get_etere_lines()

Line Generation:
    - Parser analyzes daily spot calendar grid
    - Groups days by per-day-max within each week
    - Merges consecutive weeks with identical patterns
    - Returns ready-to-enter Etere line specs

Blocks:
    - No block filtering (skip blocks tab entirely)
    - Legacy "select all" approach removed — matches TCAA/Daviselen behavior

Separation Intervals:
    - ALWAYS (3, 5, 0) → customer=3, event=0, order=5
    - NOTE: Etere fixed the backend swap, so enter them correctly:
      customer=3, event=0, order=5

Market:
    - Single market per order (detected from DMA field in PDF)
    - Master market always NYC; individual lines use their specific market

═══════════════════════════════════════════════════════════════════════════════
IMPORTS - Universal utilities, no duplication
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
from pathlib import Path
from datetime import date
from typing import Optional

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from etere_client import EtereClient
from src.domain.enums import BillingType, OrderType

from parsers.admerasia_parser import (
    parse_admerasia_pdf,
    AdmerasiaOrder,
    AdmerasiaLine,
    get_default_order_code,
    get_default_order_description,
    get_default_customer_order_ref,
    get_default_notes,
    get_default_separation_intervals,
    extract_order_total_from_pdf,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# McDonald's is the only known Admerasia customer
MCDONALDS_CUSTOMER_ID = 42

# Admerasia separation intervals: (customer, event, order) = (3, 0, 5)
# NOTE: get_default_separation_intervals() returns (3, 5, 0) in the
# original (customer, order, event) format.
# Etere fields: customer=3, event=0, order=5
ADMERASIA_SEPARATION = (3, 0, 5)

# Default database path (for future customer DB integration)
CUSTOMER_DB_PATH = os.path.join("data", "customers.db")


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOMER LOOKUP
# ═══════════════════════════════════════════════════════════════════════════════

def lookup_customer(
    header_text: str,
    order_number: str = "",
    db_path: str = CUSTOMER_DB_PATH
) -> Optional[dict]:
    """
    Look up customer from Admerasia order.

    All Admerasia orders are McDonald's (Customer ID: 42).
    Detection checks header text AND order number (which contains "MD10").

    Args:
        header_text: Header text from parsed order
        order_number: Order number (e.g., "05-MD10-2602FT")
        db_path: Path to customers.db

    Returns:
        Dict with customer info or None if not found
    """
    # Combine texts for detection
    search_text = f"{header_text} {order_number}".upper()

    # Try database first
    if os.path.exists(db_path):
        try:
            from src.data_access.repositories.customer_repository import CustomerRepository

            repo = CustomerRepository(db_path)
            customer = repo.find_by_name("McDonald's", OrderType.ADMERASIA)

            if customer:
                return {
                    'customer_id': customer.customer_id,
                    'abbreviation': customer.abbreviation or 'McD',
                    'separation': (
                        customer.separation_customer,
                        customer.separation_event,
                        customer.separation_order
                    ),
                    'billing_type': customer.billing_type,
                }
        except Exception as e:
            print(f"[CUSTOMER DB] ⚠ Database lookup failed: {e}")

    # Fallback: Hardcoded McDonald's
    if "MCDONALD" in search_text or "MD10" in search_text:
        return {
            'customer_id': str(MCDONALDS_CUSTOMER_ID),
            'abbreviation': 'McD',
            'separation': ADMERASIA_SEPARATION,
            'billing_type': 'agency',
        }

    return None


def _save_customer_to_db(
    customer_name: str,
    customer_id: str,
    db_path: str = CUSTOMER_DB_PATH
) -> None:
    """
    Save customer to database for future lookups (self-learning).

    Uses INSERT OR IGNORE so it only writes on first discovery —
    subsequent calls for the same (customer_name, order_type) are no-ops.

    Args:
        customer_name: Customer display name (e.g., "McDonald's")
        customer_id: Etere customer ID (e.g., "42")
        db_path: Path to customers.db
    """
    try:
        import sqlite3
        from pathlib import Path

        db = Path(db_path)
        if not db.parent.exists():
            db.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(db) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO customers (customer_id, customer_name, order_type)
                VALUES (?, ?, ?)
                """,
                (str(customer_id), customer_name, "ADMERASIA")
            )
            conn.commit()

        print(f"[CUSTOMER DB] ✓ Ensured '{customer_name}' (ID: {customer_id}) in database")

    except Exception as e:
        # Non-fatal: database save failure shouldn't block order processing
        print(f"[CUSTOMER DB] ⚠ Could not save to database: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET MAPPING
# ═══════════════════════════════════════════════════════════════════════════════

def map_market_to_code(market_name: str) -> str:
    """
    Map market name from PDF DMA field to standard Etere market code.

    Args:
        market_name: Market name from PDF (e.g., "Houston", "Los Angeles")

    Returns:
        Market code (e.g., "HOU", "LAX")
    """
    market_upper = market_name.upper().strip()

    # Already a valid market code
    if market_upper in ["LAX", "SEA", "SFO", "HOU", "NYC", "CVC", "DAL", "WDC", "MMT", "CMP"]:
        return market_upper

    # Map from market name
    if "SEATTLE" in market_upper or "TACOMA" in market_upper:
        return "SEA"
    elif "SAN FRANCISCO" in market_upper:
        return "SFO"
    elif "LOS ANGELES" in market_upper:
        return "LAX"
    elif "SACRAMENTO" in market_upper:
        return "CVC"
    elif "HOUSTON" in market_upper:
        return "HOU"
    elif "NEW YORK" in market_upper:
        return "NYC"
    elif "DALLAS" in market_upper:
        return "DAL"
    elif "WASHINGTON" in market_upper and "DC" in market_upper:
        return "WDC"
    else:
        return "UNKNOWN"


# ═══════════════════════════════════════════════════════════════════════════════
# ORDER VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

def _verify_parsed_order(order: AdmerasiaOrder, pdf_path: str) -> Optional[AdmerasiaOrder]:
    """
    Display a formatted verification table of parsed line items and let the user
    confirm, abort, or edit before automation begins.

    Returns the (possibly edited) order, or None if the user aborts.
    """
    while True:
        flight_start, flight_end = order.get_flight_dates()
        start_str = flight_start.strftime('%-m/%-d/%Y')
        end_str = flight_end.strftime('%-m/%-d/%Y')

        print("\n" + "=" * 70)
        print(f"ORDER VERIFICATION — {order.order_number}")
        print(f"{order.language} | {', '.join(order.markets)} | {start_str} – {end_str}")
        print("=" * 70)

        header = f"{'#':>2}  {'Day Pattern':<14}{'Time':<18}{'Rate':>9}  {'Spots':>5}"
        print(header)

        total_spots = 0
        for i, line in enumerate(order.lines, 1):
            spots = line.get_total_spots()
            total_spots += spots
            rate_str = f"${float(line.net_rate):>7.2f}"
            print(f"{i:>2}  {line.days:<14}{line.time:<18}{rate_str}  {spots:>5}")

        print(f"{'':>43} {'--------':>5}")
        print(f"{'Total:':>44} {total_spots:>5}")

        # Cross-check against PDF's own "Order Total"
        pdf_total = extract_order_total_from_pdf(pdf_path)
        if pdf_total is not None:
            if pdf_total == total_spots:
                print(f"\n PDF Order Total: {pdf_total}  \u2713 MATCHES")
            else:
                print(f"\n PDF Order Total: {pdf_total}  \u2717 MISMATCH ({total_spots} vs {pdf_total})")

        print("=" * 70)
        choice = input("Does this look correct? [Y/n/e(dit)]: ").strip().lower()

        if choice in ("", "y", "yes"):
            return order

        if choice in ("n", "no"):
            print("\nPlease recheck the IO and re-run.")
            return None

        if choice.startswith("e"):
            # Edit mode — let user correct times line by line
            while True:
                line_input = input("\nEnter line number to edit (or Enter to finish): ").strip()
                if not line_input:
                    break
                try:
                    line_num = int(line_input)
                    if line_num < 1 or line_num > len(order.lines):
                        print(f"  Invalid line number. Enter 1–{len(order.lines)}.")
                        continue
                except ValueError:
                    print("  Please enter a number.")
                    continue

                target = order.lines[line_num - 1]
                corrected = input(f"  Line {line_num} current time: {target.time}\n"
                                  f"  Enter corrected time (or Enter to keep): ").strip()
                if corrected:
                    target.time = corrected
                    print(f"  Updated to: {corrected}")

            # Redisplay the table after edits
            continue


# ═══════════════════════════════════════════════════════════════════════════════
# UPFRONT INPUT COLLECTION
# ═══════════════════════════════════════════════════════════════════════════════

def gather_admerasia_inputs(pdf_path: str) -> Optional[dict]:
    """
    Gather ALL user inputs BEFORE browser automation starts.

    This function:
    1. Parses the PDF to extract order details
    2. Auto-detects customer (always McDonald's for Admerasia)
    3. Prompts for any missing/ambiguous information (garbled times)
    4. Prepares all data needed for unattended automation

    Args:
        pdf_path: Path to Admerasia PDF

    Returns:
        Dictionary with all inputs needed for automation, or None on failure
    """
    print("\n" + "=" * 70)
    print("ADMERASIA ORDER - UPFRONT INPUT COLLECTION")
    print("=" * 70)

    # Parse PDF (this handles ambiguous time prompts internally)
    print("\n[PARSE] Reading PDF...")
    try:
        order = parse_admerasia_pdf(pdf_path)
    except Exception as e:
        print(f"[PARSE] ✗ Failed: {e}")
        return None

    print(f"[PARSE] ✓ Order: {order.order_number}")
    print(f"[PARSE] ✓ Language: {order.language}")
    print(f"[PARSE] ✓ Lines: {len(order.lines)}")

    flight_start, flight_end = order.get_flight_dates()
    print(f"[PARSE] ✓ Flight: {flight_start} - {flight_end}")

    # Verification step — show human-readable summary before proceeding
    order = _verify_parsed_order(order, pdf_path)
    if order is None:
        return None

    # Detect market
    market = order.get_market_code()
    if market == "Unknown" and order.markets:
        market = map_market_to_code(order.markets[0])
    print(f"[MARKET] ✓ Detected: {market}")

    if market == "UNKNOWN":
        print(f"\n[MARKET] ✗ Could not determine market from DMA: {order.markets}")
        market = input("  Enter market code (HOU, LAX, SEA, NYC, etc.): ").strip().upper()

    # Lookup customer (always McDonald's)
    customer = lookup_customer(order.header_text, order.order_number)

    if customer:
        print(f"\n[CUSTOMER] ✓ Auto-detected: McDonald's (ID: {customer['customer_id']})")
        customer_id = customer['customer_id']
        separation = customer['separation']
    else:
        print(f"\n[CUSTOMER] ✗ Could not auto-detect customer")
        print("Please enter customer details:")
        customer_id = input("  Customer ID: ").strip()

        print("\nSeparation intervals (minutes):")
        cust_sep = input("  Customer separation [3]: ").strip() or "3"
        event_sep = input("  Event separation [0]: ").strip() or "0"
        order_sep = input("  Order separation [5]: ").strip() or "5"
        separation = (int(cust_sep), int(event_sep), int(order_sep))

    # Self-learning: Save customer to database for future lookups
    _save_customer_to_db(
        customer_name="McDonald's",
        customer_id=str(customer_id),
        db_path=CUSTOMER_DB_PATH
    )

    # Get Etere line specifications (parsed and analyzed)
    etere_lines = order.get_etere_lines()
    print(f"\n[LINES] ✓ {len(etere_lines)} Etere line(s) after analysis")

    if not etere_lines:
        print("[LINES] ✗ No valid lines found in PDF")
        return None

    # Show line summary
    for i, line_spec in enumerate(etere_lines, 1):
        print(f"  Line {i}: {line_spec['days']} {line_spec['time']} "
              f"| {line_spec['start_date']} - {line_spec['end_date']} "
              f"| {line_spec['total_spots']}x @ ${line_spec['rate']}")

    # Contract code and description
    suggested_code = get_default_order_code(order)
    suggested_desc = get_default_order_description(order)

    print(f"\n[CONTRACT]")
    contract_code = input(f"  Code [{suggested_code}]: ").strip() or suggested_code
    description = input(f"  Description [{suggested_desc}]: ").strip() or suggested_desc

    # Customer Order Ref: just the order number
    customer_order_ref = get_default_customer_order_ref(order)

    # Notes: header text from PDF (campaign info, DMA, restrictions)
    notes = get_default_notes(order)
    print(f"\n  Notes:")
    for line in notes.split('\n'):
        print(f"    {line}")

    # Billing (UNIVERSAL for ALL agency orders)
    billing = BillingType.CUSTOMER_SHARE_AGENCY
    print(f"\n[BILLING] ✓ Customer share indicating agency % / Agency")

    # Spot duration (from parsed order)
    spot_duration = order.lines[0].spot_length if order.lines else 15
    print(f"[SPOT] ✓ Duration: :{spot_duration}s")

    print("\n" + "=" * 70)
    print("INPUT COLLECTION COMPLETE - Ready for automation")
    print("=" * 70)

    return {
        'order': order,
        'customer_id': customer_id,
        'market': market,
        'contract_code': contract_code,
        'contract_description': description,
        'customer_order_ref': customer_order_ref,
        'notes': notes,
        'billing': billing,
        'separation': separation,
        'etere_lines': etere_lines,
        'spot_duration': spot_duration,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# BROWSER AUTOMATION
# ═══════════════════════════════════════════════════════════════════════════════

def process_admerasia_order(
    driver,
    pdf_path: str,
    user_input: dict = None
) -> bool:
    """
    Process Admerasia order with completely unattended automation.

    Matches Daviselen/TCAA pattern for consistency across all automations.

    Workflow:
    1. Use pre-collected inputs (from orchestrator) OR gather them now
    2. Start browser automation (no interruptions)
    3. Create contract header
    4. Add all contract lines (pre-analyzed from daily spot grid)
    5. Return success status

    Args:
        driver: Selenium WebDriver (raw driver, not session)
        pdf_path: Path to Admerasia PDF
        user_input: Pre-collected inputs from orchestrator (optional)

    Returns:
        True if successful, False otherwise
    """
    # ═══════════════════════════════════════════════════════════════
    # GET INPUTS (pre-collected OR gather now)
    # ═══════════════════════════════════════════════════════════════

    if user_input is None:
        # Not called from orchestrator - gather inputs now
        user_input = gather_admerasia_inputs(pdf_path)
        if not user_input:
            return False

    order = user_input['order']
    etere_lines = user_input['etere_lines']

    # ═══════════════════════════════════════════════════════════════
    # BROWSER AUTOMATION (COMPLETELY UNATTENDED)
    # ═══════════════════════════════════════════════════════════════

    print("\n" + "=" * 70)
    print("STARTING BROWSER AUTOMATION")
    print("=" * 70)

    all_success = True

    # Create Etere client (just like Daviselen/TCAA does)
    etere = EtereClient(driver)

    try:
        # Master market already set by session (NYC for Crossings TV)
        # Individual lines will use their specific market

        # ═══════════════════════════════════════════════════════════
        # CREATE CONTRACT HEADER
        # ═══════════════════════════════════════════════════════════

        billing = user_input['billing']
        flight_start, flight_end = order.get_flight_dates()

        contract_number = etere.create_contract_header(
            customer_id=int(user_input['customer_id']),
            code=user_input['contract_code'],
            description=user_input['contract_description'],
            contract_start=flight_start.strftime('%m/%d/%Y'),
            contract_end=flight_end.strftime('%m/%d/%Y'),
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
        # ADD CONTRACT LINES
        # ═══════════════════════════════════════════════════════════

        separation = user_input['separation']
        market = user_input['market']
        spot_duration = user_input['spot_duration']

        for line_idx, line_spec in enumerate(etere_lines, 1):
            # Build description: DAYS TIME
            # No program names for Admerasia (business rule #4)
            # No line numbers (IO doesn't have them)
            description = f"{line_spec['days']} {line_spec['time']}"

            # Format dates for Etere
            start_date_str = line_spec['start_date'].strftime('%m/%d/%Y')
            end_date_str = line_spec['end_date'].strftime('%m/%d/%Y')

            # Apply Sunday 6-7a rule
            days, _ = EtereClient.check_sunday_6_7a_rule(
                line_spec['days'], line_spec['time']
            )

            # Parse time range using EtereClient universal parser
            time_from, time_to = EtereClient.parse_time_range(line_spec['time'])

            # Determine spot code (Admerasia never has bonus spots)
            spot_code = 2  # Paid Commercial

            # Calculate spots_per_week: Admerasia orders specify exact spots on exact days,
            # so max per week = 0 (tells Etere to not apply any weekly cap)
            spots_per_week = 0

            print(f"\n[LINE {line_idx}] {days} {line_spec['time']}")
            print(f"  {start_date_str} - {end_date_str}")
            print(f"  {line_spec['total_spots']}x total, "
                  f"{line_spec['per_day_max']}x/day max "
                  f"@ ${line_spec['rate']}")

            success = etere.add_contract_line(
                contract_number=contract_number,
                market=market,
                start_date=start_date_str,
                end_date=end_date_str,
                days=days,
                time_from=time_from,
                time_to=time_to,
                description=description,
                spot_code=spot_code,
                duration_seconds=spot_duration,
                total_spots=line_spec['total_spots'],
                spots_per_week=spots_per_week,
                max_daily_run=line_spec['per_day_max'],
                rate=float(line_spec['rate']),                separation_intervals=separation,
            )

            if not success:
                print(f"  [LINE {line_idx}] ✗ Failed")
                all_success = False

        print(f"\n[COMPLETE] Contract {contract_number} — "
              f"{len(etere_lines)} lines processed")

    except Exception as e:
        print(f"\n[ERROR] Browser automation failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    return all_success



# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("ADMERASIA AUTOMATION - STANDALONE MODE NOT SUPPORTED")
    print("=" * 70)
    print()
    print("This automation must be run through the orchestrator (main.py)")
    print("which provides the browser session.")
    print()
    print("To process Admerasia orders:")
    print("  1. Place PDF in incoming\\ folder")
    print("  2. Run: python main.py")
    print("  3. Select the Admerasia order from the menu")
    print()
    print("For testing/development, you can call process_admerasia_order()")
    print("directly with a browser driver session.")
    print("=" * 70)
    sys.exit(1)
