"""
Time Advertising Broadcast Order Automation

Handles input gathering and Etere entry for Time Advertising, Inc. orders
(e.g. Graton Casino on Crossings TV SF and Sacramento).

Business Rules:
- Agency: Time Advertising, Inc. (billed as agency)
- One PDF per market (SFO or CVC)
- Two paid dayparts: Cantonese News/Talk + Mandarin News/Drama
- One thematic (free/bonus) daypart: M-Sun ROS
- Rate: gross rate from PDF (no grossing needed — already gross)
- Separation: (15, 0, 0)
- Customer DB lookup by advertiser name
"""

import os
import sys
from pathlib import Path
from typing import Optional

_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from browser_automation.parsers.timeadvertising_parser import (
    TimeAdvertisingOrder,
    parse_timeadvertising_pdf,
)
from src.domain.enums import OrderType, SeparationInterval


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

TIMEADVERTISING_SEPARATION = SeparationInterval.TIMEADVERTISING.value  # (15, 0, 0)
CUSTOMER_DB_PATH = os.path.join("data", "customers.db")


# ─────────────────────────────────────────────────────────────────────────────
# CUSTOMER DATABASE LOOKUP
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_customer(client_name: str) -> Optional[dict]:
    """Look up customer in the database by advertiser name."""
    if not os.path.exists(CUSTOMER_DB_PATH):
        return None
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        customer = (
            repo.find_by_name(client_name, OrderType.TIMEADVERTISING) or
            repo.find_by_name_fuzzy(client_name, OrderType.TIMEADVERTISING)
        )
        if customer:
            return {
                'customer_id': customer.customer_id,
                'abbreviation': customer.abbreviation,
            }
    except Exception as exc:
        print(f"[CUSTOMER DB] ⚠ Lookup failed: {exc}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# UPFRONT INPUT GATHERING
# ─────────────────────────────────────────────────────────────────────────────

def gather_timeadvertising_inputs(pdf_path: str) -> Optional[dict]:
    """
    Parse PDF and gather user inputs before browser session.

    Args:
        pdf_path: Path to Time Advertising broadcast order PDF

    Returns:
        Dict with order data and user inputs, or None if cancelled
    """
    order = parse_timeadvertising_pdf(pdf_path)
    if not order:
        print("[TIME ADVERTISING] Failed to parse PDF")
        return None

    return _gather_inputs(order)


def _gather_inputs(order: TimeAdvertisingOrder) -> Optional[dict]:
    """Gather user inputs for the parsed order."""
    print(f"\n{'='*70}")
    print("TIME ADVERTISING - INPUT GATHERING")
    print(f"{'='*70}\n")

    print(f"  Advertiser:  {order.advertiser}")
    print(f"  Station:     {order.station}")
    print(f"  Market:      {order.market}")
    print(f"  Agency:      {order.agency}")
    print(f"  Duration:    :{order.duration_seconds}s")
    print(f"  Flight:      {order.flight_start} → {order.flight_end}")
    print(f"  Lines:       {len(order.paid_lines)} paid, {len(order.thematic_lines)} thematic")
    print()
    for ln in order.lines:
        kind = "THEMATIC" if ln.is_thematic else "PAID"
        print(f"  [{kind}] {ln.program}  →  {ln.total_spots} spots @ ${ln.rate:.2f}")
    print()

    # ── Customer lookup ───────────────────────────────────────────────────
    customer = _lookup_customer(order.advertiser)
    if customer:
        print(f"[DB] Customer found: ID={customer['customer_id']}")
        customer_id = customer['customer_id']
    else:
        print(f"[DB] No customer record found for '{order.advertiser}'")
        customer_id = input("  Enter Etere customer ID: ").strip()
        if not customer_id:
            print("[CANCELLED]")
            return None

    # ── Contract code ─────────────────────────────────────────────────────
    default_code = _default_contract_code(order)
    print(f"\n[1/2] Contract Code")
    print(f"  Default: {default_code}")
    resp = input("  Use default? (y/n): ").strip().lower()
    contract_code = default_code if resp == 'y' else input("  Enter code: ").strip()
    if not contract_code:
        print("[CANCELLED]")
        return None

    # ── Description ───────────────────────────────────────────────────────
    default_desc = _default_description(order)
    print(f"\n[2/2] Contract Description")
    print(f"  Default: {default_desc}")
    resp = input("  Use default? (y/n): ").strip().lower()
    description = default_desc if resp == 'y' else input("  Enter description: ").strip()

    return {
        'order': order,
        'customer_id': customer_id,
        'contract_code': contract_code,
        'description': description,
        'separation': TIMEADVERTISING_SEPARATION,
    }


def _default_contract_code(order: TimeAdvertisingOrder) -> str:
    """Generate default contract code from advertiser + market + date."""
    # e.g. "GratonCasino-SFO-Mar26"
    name = order.advertiser.replace(' ', '')
    market = order.market
    if order.order_date:
        parts = order.order_date.split('/')
        if len(parts) == 3:
            from datetime import datetime
            try:
                dt = datetime(int(parts[2]), int(parts[0]), int(parts[1]))
                date_tag = dt.strftime('%b%y')
            except ValueError:
                date_tag = order.order_date.replace('/', '')
        else:
            date_tag = order.order_date.replace('/', '')
    else:
        date_tag = ""
    return f"{name}-{market}-{date_tag}"


def _default_description(order: TimeAdvertisingOrder) -> str:
    """Generate default description from advertiser + agency."""
    return f"{order.advertiser} / {order.agency}"
