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


def _format_time_range_short(time_range: str) -> str:
    """Convert '7:00 AM - 4:00 PM' to '7a-4p' format."""
    parts = time_range.split(' - ')
    if len(parts) != 2:
        return time_range
    start, end = parts
    start_short = start.replace(':00', '').replace(' AM', 'a').replace(' PM', 'p')
    end_short = end.replace(':00', '').replace(' AM', 'a').replace(' PM', 'p')
    return f"{start_short}-{end_short}"


def _parse_date(date_str: str):
    """Convert 'MM/DD/YYYY' string to date object for create_contract_header."""
    return datetime.strptime(date_str.strip(), '%m/%d/%Y').date()


def _build_notes(order_data: dict) -> str:
    """Build contract notes — just the Order Comment from the PDF."""
    import re
    comment = order_data.get('order_comment', '') or ''
    # Strip Unicode private-use area characters (PDF font artifacts like \ue010)
    comment = re.sub(r'[\ue000-\uf8ff]', '', comment).strip()
    return comment


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
                    or repo.find_by_fuzzy_match(client_name, OrderType.WORLDLINK))
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

    # Revision orders: skip customer details — contract already exists in Etere.
    # Only the contract number is needed to add lines.
    contract_number = None
    if order_type_str != 'new':
        print(f"\n[REVISION] Order type: {order_type_str.upper()}")
        contract_number = input("  Existing contract number: ").strip()
        customer_id = None
        separation = (5, 0, 15)  # WorldLink default — contract already has its own settings
        print(f"[CUSTOMER] ✓ Revision — using existing contract {contract_number}")
    else:
        # New order: look up or prompt for customer details
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
        desc = f"(Line {line['line_number']}) {days} {_format_time_range_short(line['time_range'])}"
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

        # CMP line: $0 — replicated to other markets via Options tab selection
        ok = etere.add_contract_line(
            contract_number=contract_number, market="CMP",
            start_date=line['start_date'], end_date=line['end_date'],
            days=days, time_from=time_from, time_to=time_to,
            description=desc, spot_code=2, duration_seconds=duration,
            total_spots=line['total_spots'], spots_per_week=line['spots'],
            rate=0.0, separation_intervals=separation,
            other_markets=["CVC", "SFO", "LAX", "SEA", "HOU", "WDC", "MMT"],
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
            description=f"(Line {line['line_number']}) {days} {_format_time_range_short(line['time_range'])}",
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
            flight_start = min(_parse_date(l['start_date']) for l in lines).strftime('%m/%d/%Y')
            flight_end = max(_parse_date(l['end_date']) for l in lines).strftime('%m/%d/%Y')

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
            highest_existing_etere_num = None  # New contract — refresh all lines
        else:
            contract_number = user_input.get('contract_number', '')
            if not contract_number:
                print("[CONTRACT] ✗ No contract number provided for revision")
                return None
            print(f"[CONTRACT] ✓ Using existing: {contract_number}")
            # Extend contract end date if revision lines go beyond it.
            # Also warms up the Etere sales context (new orders get this via
            # create_contract_header; revision orders need it explicitly).
            if not etere.extend_contract_end_date(contract_number, lines):
                return None
            # Scan existing lines to get the highest Etere internal line number.
            # This is the onscreen Etere-assigned ID (unique per line, SQL-assigned),
            # NOT the PDF line number. Block refresh filters by this to skip pre-existing lines.
            existing_data = etere.get_all_line_ids_with_numbers(contract_number)
            etere_line_nums = [lnum for _, lnum in existing_data if lnum is not None]
            highest_existing_etere_num = max(etere_line_nums) if etere_line_nums else None
            print(f"[LINES] {len(existing_data)} existing lines — "
                  f"highest Etere line number: {highest_existing_etere_num}")

        if network == 'ASIAN':
            success = _add_asian_lines(etere, contract_number, lines, separation)
        else:
            success = _add_crossings_lines(etere, contract_number, lines, separation)

        status = "✓" if success else "⚠ (some lines failed)"
        print(f"\n[COMPLETE] {status} Contract {contract_number} — {len(lines)} PDF lines")

        # Block refresh: Crossings TV only (CMP lines replicated to other markets)
        if network == 'CROSSINGS' and success:
            etere.perform_block_refresh(contract_number, only_lines_above=highest_existing_etere_num)

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
