"""
RPM Order Automation
Browser automation for entering RPM agency orders into Etere.

═══════════════════════════════════════════════════════════════════════════════
RPM BUSINESS RULES
═══════════════════════════════════════════════════════════════════════════════

Known Customers:
    Various clients (e.g., Muckleshoot, Pechanga) in SEA, SFO, or CVC markets.
    Looked up by name via CustomerRepository; prompted and saved on first encounter.

Billing (Universal for ALL RPM):
    - Charge To: "Customer share indicating agency %"
    - Invoice Header: "Agency"

Contract Format:
    - Code: "RPM {estimate_number}"
    - Description: "{market} Est {estimate_number}"
    - Notes: CLIENT / PRODUCT / ESTIMATE / DEMO lines

Line Generation:
    - Weekly spot distribution pattern (one Etere line per non-zero week)
    - Bonus lines use spot code 10; paid lines use spot code 2
    - Sunday 6-7a rule applied (paid programming)
    - No block filtering (Blocks tab skipped — matches all new automations)

Separation:
    - Customer=25, Event=0, Order=15 (SeparationInterval.RPM)

═══════════════════════════════════════════════════════════════════════════════
IMPORTS
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
from pathlib import Path
from datetime import date, timedelta
from typing import Optional

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from etere_client import EtereClient
from src.domain.enums import BillingType, OrderType, SeparationInterval

from browser_automation.parsers.rpm_parser import (
    parse_rpm_pdf,
    RPMOrder,
    RPMLine,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

CUSTOMER_DB_PATH = os.path.join("data", "customers.db")
RPM_DEFAULT_SEPARATION = SeparationInterval.RPM.value  # (25, 0, 15)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_rpm_daypart(daypart: str) -> tuple[str, str, str]:
    """Split "M-F 6a-8p Chinese" → (days, time_range, language)."""
    parts = daypart.split(" ", 2)
    days = parts[0] if len(parts) > 0 else "M-F"
    time_range = parts[1] if len(parts) > 1 else "6a-12m"
    language = parts[2] if len(parts) > 2 else ""
    return days, time_range, language


def _duration_to_seconds(duration_str: str) -> int:
    """Convert "00:00:30:00" (HH:MM:SS:FF) to integer seconds."""
    parts = duration_str.split(":")
    return int(parts[2]) if len(parts) >= 3 else 30


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOMER DATABASE ACCESS
# ═══════════════════════════════════════════════════════════════════════════════

def lookup_customer(
    client_name: str,
    db_path: str = CUSTOMER_DB_PATH
) -> Optional[dict]:
    """
    Look up RPM customer in the database.

    No hardcoded fallbacks — RPM clients are diverse. Returns None if not found
    so gather_rpm_inputs() can prompt the user and save for next time.
    """
    if not os.path.exists(db_path):
        return None
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        repo = CustomerRepository(db_path)
        customer = repo.find_by_name(client_name, OrderType.RPM) or \
                   repo.find_by_name_fuzzy(client_name, OrderType.RPM)
        if customer:
            return {
                'customer_id': customer.customer_id,
                'abbreviation': customer.abbreviation,
                'market': customer.default_market,
                'separation': (
                    customer.separation_customer,
                    customer.separation_event,
                    customer.separation_order,
                ),
                'billing_type': customer.billing_type,
            }
    except Exception as e:
        print(f"[CUSTOMER DB] ⚠ Database lookup failed: {e}")
    return None


def save_new_customer(
    customer_id: str,
    customer_name: str,
    abbreviation: str,
    market: str,
    separation: tuple,
    db_path: str = CUSTOMER_DB_PATH,
) -> None:
    """Save a new RPM customer to the database for future orders."""
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        repo = CustomerRepository(db_path)
        customer = Customer(
            customer_id=customer_id,
            customer_name=customer_name,
            order_type=OrderType.RPM,
            abbreviation=abbreviation,
            default_market=market,
            billing_type='agency',
            separation_customer=separation[0],
            separation_event=separation[1],
            separation_order=separation[2],
        )
        repo.save(customer)
        print(f"[CUSTOMER DB] ✓ Saved: {customer_name} → ID {customer_id}")
    except Exception as e:
        print(f"[CUSTOMER DB] ✗ Save failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# UPFRONT INPUT COLLECTION
# ═══════════════════════════════════════════════════════════════════════════════

def gather_rpm_inputs(pdf_path: str) -> Optional[dict]:
    """
    Gather ALL user inputs BEFORE browser automation starts.

    Parses PDF, auto-detects customer from database, prompts for missing
    info, and returns everything needed for unattended automation.
    """
    print("\n" + "="*70)
    print("RPM ORDER - UPFRONT INPUT COLLECTION")
    print("="*70)

    print("\n[PARSE] Reading PDF...")
    try:
        order, lines = parse_rpm_pdf(pdf_path)
    except Exception as e:
        print(f"[PARSE] ✗ Failed: {e}")
        return None

    if not order:
        print("[PARSE] ✗ Could not parse order from PDF")
        return None

    print(f"[PARSE] ✓ Client: {order.client}")
    print(f"[PARSE] ✓ Estimate: {order.estimate_number}")
    print(f"[PARSE] ✓ Market: {order.market}")
    print(f"[PARSE] ✓ Flight: {order.flight_start} - {order.flight_end}")
    print(f"[PARSE] ✓ Lines: {len(lines)}")

    market = order.market  # Already SEA/SFO/CVC from parser

    customer = lookup_customer(order.client)
    if customer:
        print(f"\n[CUSTOMER] ✓ Found: ID={customer['customer_id']}, "
              f"Abbrev={customer['abbreviation']}")
        customer_id = customer['customer_id']
        abbreviation = customer['abbreviation']
        separation = customer['separation']
    else:
        print(f"\n[CUSTOMER] ✗ Not found: {order.client}")
        print("Please enter customer details:")
        customer_id = input("  Customer ID: ").strip()
        abbreviation = input("  Abbreviation (e.g., Muck, Pech): ").strip()
        cust_sep = input("  Customer separation [25]: ").strip() or "25"
        event_sep = input("  Event separation [0]: ").strip() or "0"
        order_sep = input("  Order separation [15]: ").strip() or "15"
        separation = (int(cust_sep), int(event_sep), int(order_sep))
        save_new_customer(customer_id, order.client, abbreviation, market, separation)

    suggested_code = f"RPM {order.estimate_number}"
    suggested_desc = f"{market} Est {order.estimate_number}"
    customer_order_ref = f"Est {order.estimate_number}"

    print(f"\n[CONTRACT]")
    contract_code = input(f"  Code [{suggested_code}]: ").strip() or suggested_code
    description = input(f"  Description [{suggested_desc}]: ").strip() or suggested_desc

    notes = (f"CLIENT {order.client}\nPRODUCT {order.product}"
             f"\nESTIMATE {order.estimate_number}\nDEMO {order.primary_demo}")
    print(f"  Notes:")
    for ln in notes.split('\n'):
        print(f"    {ln}")

    billing = BillingType.CUSTOMER_SHARE_AGENCY
    print(f"\n[BILLING] ✓ Customer share indicating agency % / Agency")

    print("\n" + "="*70)
    print("INPUT COLLECTION COMPLETE - Ready for automation")
    print("="*70)

    return {
        'order': order,
        'lines': lines,
        'customer_id': customer_id,
        'market': market,
        'contract_code': contract_code,
        'contract_description': description,
        'customer_order_ref': customer_order_ref,
        'notes': notes,
        'billing': billing,
        'separation': separation,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# BROWSER AUTOMATION
# ═══════════════════════════════════════════════════════════════════════════════

def process_rpm_order(
    driver,
    pdf_path: str,
    user_input: dict = None
) -> bool:
    """
    Process RPM order with completely unattended automation.

    Workflow:
    1. Use pre-collected inputs (from orchestrator) OR gather them now
    2. Create contract header via EtereClient
    3. Add all contract lines with weekly distribution
    4. Return success status
    """
    if user_input is None:
        user_input = gather_rpm_inputs(pdf_path)
        if not user_input:
            return False

    order = user_input['order']
    lines = user_input['lines']

    print("\n" + "="*70)
    print("STARTING RPM BROWSER AUTOMATION")
    print("="*70)

    all_success = True
    etere = EtereClient(driver)

    try:
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

        separation = user_input['separation']
        market = user_input['market']

        for line_num, line in enumerate(lines, 1):
            days, time_range, language = _parse_rpm_daypart(line.daypart)
            days, _ = EtereClient.check_sunday_6_7a_rule(days, time_range)
            time_from, time_to = EtereClient.parse_time_range(time_range)
            description = f"({line_num}) {line.daypart}"
            spot_code = 10 if line.is_bonus else 2
            duration_seconds = _duration_to_seconds(line.duration)

            print(f"\n[LINE {line_num}] {'BNS' if line.is_bonus else 'PAID'} "
                  f"{language} | {days} {time_range} | {duration_seconds}s")

            for week_idx, spots in enumerate(line.weekly_spots):
                if spots == 0:
                    continue
                week_start = order.flight_start + timedelta(weeks=week_idx)
                week_end = min(week_start + timedelta(days=6), order.flight_end)
                print(f"  Week {week_idx + 1}: {week_start} - {week_end}, {spots} spots")
                success = etere.add_contract_line(
                    contract_number=contract_number,
                    market=market,
                    start_date=week_start.strftime('%m/%d/%Y'),
                    end_date=week_end.strftime('%m/%d/%Y'),
                    days=days,
                    time_from=time_from,
                    time_to=time_to,
                    description=description,
                    spot_code=spot_code,
                    duration_seconds=duration_seconds,
                    total_spots=spots,
                    spots_per_week=spots,
                    rate=float(line.rate),
                    separation_intervals=separation,
                )
                if not success:
                    print(f"  [LINE {line_num}] ✗ Failed for week {week_idx + 1}")
                    all_success = False

        print(f"\n[COMPLETE] Contract {contract_number} — {len(lines)} lines processed")

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
    print("="*70)
    print("RPM AUTOMATION - STANDALONE MODE NOT SUPPORTED")
    print("="*70)
    print()
    print("This automation must be run through the orchestrator (main.py)")
    print("which provides the browser session.")
    print()
    print("To process RPM orders:")
    print("  1. Place PDF in incoming\\ folder")
    print("  2. Run: python main.py")
    print("  3. Select the RPM order from the menu")
    print("="*70)
    sys.exit(1)
