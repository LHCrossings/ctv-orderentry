"""
WorldLink Order Automation
Browser automation for entering WorldLink/Tatari agency orders into Etere.

═══════════════════════════════════════════════════════════════════════════════
WORLDLINK BUSINESS RULES
═══════════════════════════════════════════════════════════════════════════════

Networks:
    1. Crossings TV (CROSSINGS): NYC line at real rate + CMP line at $0
       - Two Etere lines per PDF line (NYC + CMP)
       - CMP line ($0) gets replicated to other markets via block refresh
    2. Asian Channel (ASIAN): Single DAL market line per PDF line

Billing (Universal for ALL WorldLink):
    - Charge To: "Customer share indicating agency %"
    - Invoice Header: "Agency"

Contract Format:
    - Code: "WL {Agency} {Tracking#}" (built by parser)
    - Description: "WL {Agency} {Advertiser} {Spot} {Tracking#}" (built by parser)

Order Types:
    - new: Create new contract header + add all lines
    - revision_add / revision_change: Skip header, prompt for existing contract#

Block Refresh:
    - Required for all WorldLink contracts (requires_block_refresh() = True)
    - User must refresh blocks in Etere after CMP lines are added
    - highest_line tracked for revision orders (partial refresh)

Separation:
    - Customer=5, Event=0, Order=15 (SeparationInterval.WORLDLINK)

═══════════════════════════════════════════════════════════════════════════════
IMPORTS
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from etere_client import EtereClient
from src.domain.enums import BillingType, OrderType, SeparationInterval

from browser_automation.parsers.worldlink_parser import parse_worldlink_pdf


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

CUSTOMER_DB_PATH = os.path.join("data", "customers.db")
WL_DEFAULT_SEPARATION = SeparationInterval.WORLDLINK.value  # (5, 0, 15)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _duration_to_seconds(duration_str: str) -> int:
    """WorldLink parser stores duration as integer string (already in seconds)."""
    try:
        return int(duration_str)
    except (ValueError, TypeError):
        return 30


def _parse_date(date_str: str):
    """Convert 'MM/DD/YYYY' string to date object for create_contract_header."""
    return datetime.strptime(date_str.strip(), '%m/%d/%Y').date()


def _build_notes(order_data: dict) -> str:
    """Build contract notes from parsed WorldLink order data."""
    parts = [
        f"ADVERTISER {order_data.get('advertiser', '')}",
        f"PRODUCT {order_data.get('product', '')}",
        f"TRACKING {order_data.get('tracking_number', '')}",
    ]
    if order_data.get('order_comment'):
        parts.append(order_data['order_comment'])
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOMER DATABASE ACCESS
# ═══════════════════════════════════════════════════════════════════════════════

def lookup_customer(
    client_name: str,
    db_path: str = CUSTOMER_DB_PATH
) -> Optional[dict]:
    """
    Look up WorldLink customer in the database.

    No hardcoded fallbacks — WorldLink advertisers are diverse. Returns None if
    not found so gather_worldlink_inputs() can prompt the user and save for next time.
    """
    if not os.path.exists(db_path):
        return None
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        repo = CustomerRepository(db_path)
        customer = (repo.find_by_name(client_name, OrderType.WORLDLINK)
                    or repo.find_by_name_fuzzy(client_name, OrderType.WORLDLINK))
        if customer:
            return {
                'customer_id': customer.customer_id,
                'abbreviation': customer.abbreviation,
                'separation': (
                    customer.separation_customer,
                    customer.separation_event,
                    customer.separation_order,
                ),
            }
    except Exception as e:
        print(f"[CUSTOMER DB] ⚠ Database lookup failed: {e}")
    return None


def save_new_customer(
    customer_id: str,
    customer_name: str,
    abbreviation: str,
    separation: tuple,
    db_path: str = CUSTOMER_DB_PATH,
) -> None:
    """Save a new WorldLink customer to the database for future orders."""
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        repo = CustomerRepository(db_path)
        repo.save(Customer(
            customer_id=customer_id,
            customer_name=customer_name,
            order_type=OrderType.WORLDLINK,
            abbreviation=abbreviation,
            default_market=None,
            billing_type='agency',
            separation_customer=separation[0],
            separation_event=separation[1],
            separation_order=separation[2],
        ))
        print(f"[CUSTOMER DB] ✓ Saved: {customer_name} → ID {customer_id}")
    except Exception as e:
        print(f"[CUSTOMER DB] ✗ Save failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# UPFRONT INPUT COLLECTION
# ═══════════════════════════════════════════════════════════════════════════════

def gather_worldlink_inputs(pdf_path: str) -> Optional[dict]:
    """
    Gather ALL user inputs BEFORE browser automation starts.

    Parses PDF, detects network type, auto-detects customer from database,
    handles revision contract lookup, and returns everything needed for
    unattended automation.
    """
    print("\n" + "="*70)
    print("WORLDLINK ORDER - UPFRONT INPUT COLLECTION")
    print("="*70)

    print("\n[PARSE] Reading PDF...")
    try:
        order_data = parse_worldlink_pdf(pdf_path)
    except Exception as e:
        print(f"[PARSE] ✗ Failed: {e}")
        return None

    if not order_data or not order_data.get('lines'):
        print("[PARSE] ✗ Could not parse order or no lines found")
        return None

    lines = order_data['lines']
    network = order_data.get('network', 'CROSSINGS')
    order_type_str = order_data.get('order_type', 'new')
    advertiser = order_data.get('advertiser', '')

    print(f"[PARSE] ✓ Advertiser: {advertiser}")
    print(f"[PARSE] ✓ Tracking: {order_data.get('tracking_number', '')}")
    print(f"[PARSE] ✓ Network: {network} | Type: {order_type_str.upper()}")
    print(f"[PARSE] ✓ Lines: {len(lines)}")

    # Customer lookup
    customer = lookup_customer(advertiser)
    if customer:
        print(f"\n[CUSTOMER] ✓ Found: ID={customer['customer_id']}, "
              f"Abbrev={customer['abbreviation']}")
        customer_id = customer['customer_id']
        abbreviation = customer['abbreviation']
        separation = customer['separation']
    else:
        print(f"\n[CUSTOMER] ✗ Not found: {advertiser}")
        print("Please enter customer details:")
        customer_id = input("  Customer ID: ").strip()
        abbreviation = input("  Abbreviation (e.g., Muck, Cross): ").strip()
        cust_sep = input("  Customer separation [5]: ").strip() or "5"
        event_sep = input("  Event separation [0]: ").strip() or "0"
        order_sep = input("  Order separation [15]: ").strip() or "15"
        separation = (int(cust_sep), int(event_sep), int(order_sep))
        save_new_customer(customer_id, advertiser, abbreviation, separation)

    # Revision handling: prompt for existing contract number
    contract_number = None
    highest_line = None
    if order_type_str != 'new':
        print(f"\n[REVISION] Order type: {order_type_str.upper()}")
        contract_number = input("  Existing contract number: ").strip()
        if lines:
            highest_line = lines[0]['line_number'] - 1  # block refresh starts here

    billing = BillingType.CUSTOMER_SHARE_AGENCY
    print(f"\n[BILLING] ✓ Customer share indicating agency % / Agency")

    print("\n" + "="*70)
    print("INPUT COLLECTION COMPLETE - Ready for automation")
    print("="*70)

    return {
        'order_data': order_data,
        'customer_id': customer_id,
        'separation': separation,
        'billing': billing,
        'network': network,
        'contract_number': contract_number,
        'highest_line': highest_line,
        'notes': _build_notes(order_data),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# LINE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _add_crossings_lines(
    etere: EtereClient,
    contract_number: str,
    lines: list,
    separation: tuple
) -> bool:
    """
    Add Crossings TV lines: NYC line at real rate + CMP line at $0 per PDF line.

    The CMP $0 line is replicated to other markets via block refresh after processing.
    """
    all_success = True
    for line in lines:
        days = line['days_of_week']
        days, _ = EtereClient.check_sunday_6_7a_rule(days, line['time_range'])
        time_from = line['from_time']
        time_to = line['to_time']
        desc = f"({line['line_number']}) {line['time_range']}"
        duration = _duration_to_seconds(line['duration'])
        rate = float(line['rate'])

        print(f"\n[LINE {line['line_number']}] {days} {line['time_range']} | "
              f"{duration}s | {line['spots']}/wk | ${rate}")

        # NYC line: actual rate
        ok = etere.add_contract_line(
            contract_number=contract_number, market="NYC",
            start_date=line['start_date'], end_date=line['end_date'],
            days=days, time_from=time_from, time_to=time_to,
            description=desc, spot_code=2, duration_seconds=duration,
            total_spots=line['total_spots'], spots_per_week=line['spots'],
            rate=rate, separation_intervals=separation,
        )
        if not ok:
            print(f"  ✗ NYC line failed")
            all_success = False

        # CMP line: $0 (multi-market replication via block refresh)
        ok = etere.add_contract_line(
            contract_number=contract_number, market="CMP",
            start_date=line['start_date'], end_date=line['end_date'],
            days=days, time_from=time_from, time_to=time_to,
            description=desc, spot_code=2, duration_seconds=duration,
            total_spots=line['total_spots'], spots_per_week=line['spots'],
            rate=0.0, separation_intervals=separation,
        )
        if not ok:
            print(f"  ✗ CMP line failed")
            all_success = False

    return all_success


def _add_asian_lines(
    etere: EtereClient,
    contract_number: str,
    lines: list,
    separation: tuple
) -> bool:
    """Add Asian Channel (TAC) lines: single DAL market per PDF line."""
    all_success = True
    for line in lines:
        days = line['days_of_week']
        days, _ = EtereClient.check_sunday_6_7a_rule(days, line['time_range'])
        ok = etere.add_contract_line(
            contract_number=contract_number, market="DAL",
            start_date=line['start_date'], end_date=line['end_date'],
            days=days, time_from=line['from_time'], time_to=line['to_time'],
            description=f"({line['line_number']}) {line['time_range']}",
            spot_code=2,
            duration_seconds=_duration_to_seconds(line['duration']),
            total_spots=line['total_spots'], spots_per_week=line['spots'],
            rate=float(line['rate']), separation_intervals=separation,
        )
        if not ok:
            print(f"  ✗ DAL line {line['line_number']} failed")
            all_success = False
    return all_success


# ═══════════════════════════════════════════════════════════════════════════════
# BROWSER AUTOMATION
# ═══════════════════════════════════════════════════════════════════════════════

def process_worldlink_order(
    driver,
    pdf_path: str,
    user_input: dict = None
) -> Optional[str]:
    """
    Process WorldLink order with completely unattended automation.

    Returns contract_number (str) on success, None on failure.
    The contract_number is needed by the service to build a Contract entity
    so requires_block_refresh() works and the result formatter shows the reminder.

    Workflow:
    1. Use pre-collected inputs (from orchestrator) OR gather them now
    2. Create contract header (new) or use existing contract (revision)
    3. Add all lines (NYC+CMP for Crossings, DAL for Asian Channel)
    4. Return contract_number
    """
    if user_input is None:
        user_input = gather_worldlink_inputs(pdf_path)
        if not user_input:
            return None

    order_data = user_input['order_data']
    network = user_input['network']
    lines = order_data['lines']
    order_type_str = order_data.get('order_type', 'new')
    billing = user_input['billing']
    separation = user_input['separation']

    print("\n" + "="*70)
    print(f"STARTING WORLDLINK BROWSER AUTOMATION — {network} / {order_type_str.upper()}")
    print("="*70)

    etere = EtereClient(driver)

    try:
        if order_type_str == 'new':
            flight_start = min(_parse_date(l['start_date']) for l in lines)
            flight_end = max(_parse_date(l['end_date']) for l in lines)

            contract_number = etere.create_contract_header(
                customer_id=int(user_input['customer_id']),
                code=order_data['order_code'],
                description=order_data['description'],
                contract_start=flight_start,
                contract_end=flight_end,
                customer_order_ref=order_data.get('tracking_number', ''),
                notes=user_input['notes'],
                charge_to=billing.get_charge_to(),
                invoice_header=billing.get_invoice_header(),
            )
            if not contract_number:
                print("[CONTRACT] ✗ Failed to create contract")
                return None
            print(f"[CONTRACT] ✓ Created: {contract_number}")
        else:
            contract_number = user_input.get('contract_number', '')
            if not contract_number:
                print("[CONTRACT] ✗ No contract number provided for revision")
                return None
            print(f"[CONTRACT] ✓ Using existing: {contract_number}")
            # Extend contract end date if revision lines go beyond it.
            # This navigation also warms up the Etere sales context so the first
            # add_contract_line call doesn't time out (new orders get this via
            # create_contract_header; revision orders need it explicitly).
            if not etere.extend_contract_end_date(contract_number, lines):
                return None

        if network == 'ASIAN':
            success = _add_asian_lines(etere, contract_number, lines, separation)
        else:
            success = _add_crossings_lines(etere, contract_number, lines, separation)

        status = "✓" if success else "⚠ (some lines failed)"
        print(f"\n[COMPLETE] {status} Contract {contract_number} — {len(lines)} PDF lines")

        return contract_number if success else None

    except Exception as e:
        print(f"\n[ERROR] Browser automation failed: {e}")
        import traceback
        traceback.print_exc()
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("="*70)
    print("WORLDLINK AUTOMATION - STANDALONE MODE NOT SUPPORTED")
    print("="*70)
    print()
    print("This automation must be run through the orchestrator (main.py)")
    print("which provides the browser session.")
    print()
    print("To process WorldLink orders:")
    print("  1. Place PDF in incoming\\ folder")
    print("  2. Run: python main.py")
    print("  3. Select the WorldLink order from the menu")
    print("="*70)
    import sys
    sys.exit(1)
