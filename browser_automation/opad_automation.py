"""
opAD Automation - Browser Automation for opAD Agency Orders

Uses EtereClient for ALL Etere interactions.
Parser handles PDF extraction; this file handles business logic + orchestration.

opAD Business Rules:
- NYC market ONLY (always)
- Multiple clients possible under opAD → customer DB lookup with manual fallback
- Separation intervals: Customer=15, Event=0, Order=15
- Billing: Charge To = "Customer share indicating agency %", Invoice Header = "Agency"
- Description from IO → contract header Notes field
- Estimate number → Customer Order Ref
- Bonus lines (rate=0) → spot code BNS (10)
- Weekly distribution splitting on gaps and differing spot counts
- Sunday 6-7a paid programming rule applies
"""

import sqlite3
import time
from pathlib import Path
from typing import Optional

from selenium import webdriver

from etere_client import EtereClient
from parsers.opad_parser import (
    OpADOrder,
    OpADLine,
    analyze_weekly_distribution,
    format_time_for_description,
    parse_opad_pdf,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

AGENCY_NAME = "opAD"
DEFAULT_MARKET = "NYC"

# Separation intervals: (customer, event, order) in minutes
SEPARATION_INTERVALS = (15, 0, 15)

# Billing
CHARGE_TO = "Customer share indicating agency %"
INVOICE_HEADER = "Agency"

# Spot codes
SPOT_CODE_PAID = 2       # Paid Commercial
SPOT_CODE_BONUS = 10     # BNS / Bonus Spot

# Customer database path (relative to project root)
CUSTOMERS_DB_PATH = Path("data") / "customers.db"


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def process_opad_order(
    driver: webdriver.Chrome,
    pdf_path: str,
    user_input: dict,
) -> bool:
    """
    Process an opAD order end-to-end.

    Called by order_processing_service.py with the same signature as
    Daviselen/Admerasia: (driver, pdf_path, user_input).

    Args:
        driver: Selenium WebDriver (already logged in via shared session)
        pdf_path: Path to the opAD PDF
        user_input: Dict with keys:
            - order_code: Contract code (e.g., "opAD NYSDOH 2824")
            - description: Contract description (goes to Notes)
            - customer_id: Customer ID (int or None for manual search)

    Returns:
        True if successful, False otherwise
    """
    etere = EtereClient(driver)

    try:
        return _execute_order(etere, pdf_path, user_input)
    except Exception as e:
        print(f"[opAD] ✗ Order failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def _execute_order(
    etere: EtereClient,
    pdf_path: str,
    user_input: dict,
) -> bool:
    """Execute the full order workflow."""

    order_code = user_input.order_code
    description = user_input.description

    # Customer ID resolved from database (not from OrderInput)
    customer_id, _ = _resolve_customer_id(pdf_path)
    
    # Separation intervals from user_input (confirmed by user in input_collectors)
    separation = user_input.separation_intervals or SEPARATION_INTERVALS

    # ── Parse PDF ──
    print(f"\n{'='*60}")
    print(f"Processing opAD Order: {pdf_path}")
    print(f"{'='*60}\n")

    order = parse_opad_pdf(pdf_path)
    print(f"[PARSE] ✓ {len(order.lines)} lines found")
    print(f"[PARSE] Client: {order.client}")
    print(f"[PARSE] Estimate: {order.estimate_number}")
    print(f"[PARSE] Flight: {order.flight_start} - {order.flight_end}")

    # ── Master market already set by orchestrator (shared session) ──

    # ── Create contract header ──
    contract_number = etere.create_contract_header(
        customer_id=customer_id,
        code=order_code,
        description=description,
        contract_start=order.flight_start,
        contract_end=order.flight_end,
        customer_order_ref=order.estimate_number,
        notes=description,
        charge_to=CHARGE_TO,
        invoice_header=INVOICE_HEADER,
    )

    if not contract_number:
        print("[opAD] ✗ Failed to create contract header")
        return False

    print(f"[opAD] ✓ Contract created: {contract_number}")

    # ── Save customer to DB on first discovery ──
    if customer_id:
        _save_customer_to_db(order.client, customer_id)

    # ── Add lines ──
    line_count = 0
    for line_idx, line in enumerate(order.lines, 1):
        etere_lines = _build_etere_lines(line, order)

        for etere_line in etere_lines:
            line_count += 1
            print(f"\n  [LINE {line_count}] {etere_line['description']}")
            print(f"    {etere_line['start_date']} - {etere_line['end_date']}")
            print(f"    {etere_line['spots_per_week']}/wk, rate=${etere_line['rate']}")

            success = etere.add_contract_line(
                contract_number=contract_number,
                market=DEFAULT_MARKET,
                start_date=etere_line["start_date"],
                end_date=etere_line["end_date"],
                days=etere_line["days"],
                time_from=etere_line["time_from"],
                time_to=etere_line["time_to"],
                description=etere_line["description"],
                spot_code=etere_line["spot_code"],
                duration_seconds=line.duration,
                total_spots=etere_line["total_spots"],
                spots_per_week=etere_line["spots_per_week"],
                rate=etere_line["rate"],                separation_intervals=separation,
            )

            if not success:
                print(f"    ✗ Failed to add line {line_count}")
                return False

            time.sleep(2)

    # ── Summary ──
    print(f"\n{'='*60}")
    print(f"✓ opAD ORDER COMPLETE")
    print(f"  Contract: {contract_number}")
    print(f"  Lines Added: {line_count}")
    print(f"{'='*60}")

    return True


# ═══════════════════════════════════════════════════════════════════════════════
# LINE BUILDING
# ═══════════════════════════════════════════════════════════════════════════════

def _build_etere_lines(line: OpADLine, order: OpADOrder) -> list[dict]:
    """
    Convert a single parsed opAD line into one or more Etere line dicts.

    Splits on:
    - Gaps (weeks with 0 spots)
    - Differing weekly spot counts

    Returns:
        List of dicts ready for etere.add_contract_line()
    """
    # Analyze weekly distribution (splits on gaps + differing counts)
    ranges = analyze_weekly_distribution(
        line.weekly_spots,
        order.week_start_dates,
        contract_end_date=order.flight_end,
    )

    # Build description
    time_fmt = format_time_for_description(line.time)
    lang_suffix = f" {line.language}" if line.language else ""
    description = f"{line.days} {time_fmt}{lang_suffix}"

    # Parse time range using EtereClient universal parser
    time_from, time_to = EtereClient.parse_time_range(line.time)

    # Apply Sunday 6-7a rule
    adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(line.days, line.time)

    # Spot code: bonus if rate is 0
    spot_code = SPOT_CODE_BONUS if line.is_bonus() else SPOT_CODE_PAID

    etere_lines = []
    for range_data in ranges:
        etere_lines.append({
            "start_date": range_data["start_date"],
            "end_date": range_data["end_date"],
            "days": adjusted_days,
            "time_from": time_from,
            "time_to": time_to,
            "description": description,
            "spot_code": spot_code,
            "total_spots": range_data["spots"],
            "spots_per_week": range_data["spots_per_week"],
            "rate": line.rate,
        })

    return etere_lines


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOMER RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_customer_id(pdf_path: str) -> tuple[Optional[int], None]:
    """
    Resolve customer ID from database based on parsed client name.
    Falls back to manual entry if not found.

    Args:
        pdf_path: Path to the opAD PDF (parsed to get client name)

    Returns:
        Tuple of (customer_id, None) - second element for backward compat
    """
    try:
        order = parse_opad_pdf(pdf_path)
        client_name = order.client
    except Exception:
        client_name = None

    if not client_name:
        return _manual_customer_entry(), None

    # Try database lookup
    try:
        db_path = CUSTOMERS_DB_PATH
        if db_path.exists():
            import sqlite3
            with sqlite3.connect(str(db_path)) as conn:
                # Exact match
                cursor = conn.execute(
                    "SELECT customer_id FROM customers WHERE customer_name = ? AND order_type = ?",
                    (client_name, "opad"),
                )
                row = cursor.fetchone()
                if row:
                    cust_id = int(row[0])
                    print(f"[CUSTOMER DB] ✓ Found: {client_name} → ID {cust_id}")
                    return cust_id, None

                # Fuzzy: check containment
                cursor = conn.execute(
                    "SELECT customer_name, customer_id FROM customers WHERE order_type = ?",
                    ("opad",),
                )
                for db_name, db_id in cursor.fetchall():
                    if (db_name.lower() in client_name.lower()
                            or client_name.lower() in db_name.lower()):
                        cust_id = int(db_id)
                        print(f"[CUSTOMER DB] ✓ Fuzzy match: {client_name} ≈ {db_name} → ID {cust_id}")
                        return cust_id, None
    except Exception as e:
        print(f"[CUSTOMER DB] ⚠ Lookup failed: {e}")

    print(f"[CUSTOMER] Not found in database: {client_name}")
    return _manual_customer_entry(), None


def _manual_customer_entry() -> Optional[int]:
    """Prompt user for customer ID manually."""
    customer_input = input("Enter customer ID (or press Enter to search in Etere): ").strip()
    if customer_input:
        try:
            return int(customer_input)
        except ValueError:
            print("[CUSTOMER] Invalid ID - will search in Etere")
            return None
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOMER DATABASE (Self-Learning)
# ═══════════════════════════════════════════════════════════════════════════════

def _save_customer_to_db(client_name: str, customer_id: int) -> None:
    """
    Save customer to database on first discovery.
    Uses INSERT OR IGNORE so it's safe to call every time.
    Non-fatal: if DB write fails, order processing continues.
    """
    try:
        db_path = CUSTOMERS_DB_PATH
        if not db_path.exists():
            print(f"[CUSTOMER DB] ⚠ Database not found at {db_path}")
            return

        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO customers (customer_name, customer_id, order_type)
                VALUES (?, ?, ?)
                """,
                (client_name, customer_id, AGENCY_NAME),
            )
            if conn.total_changes > 0:
                print(f"[CUSTOMER DB] ✓ Saved new customer: {client_name} (ID: {customer_id})")
            else:
                print(f"[CUSTOMER DB] ℹ Customer already known: {client_name}")
    except Exception as e:
        print(f"[CUSTOMER DB] ⚠ Could not save customer (non-fatal): {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# DEFAULT VALUES (called by order_processing_service.get_default_order_values)
# ═══════════════════════════════════════════════════════════════════════════════

def get_opad_defaults(pdf_path: str) -> tuple[str, str]:
    """
    Get smart default contract code and description for an opAD order.

    Code format: "opAD {client_first_word} {estimate_number}"
    Description: PDF's Description field (or Product as fallback)

    Args:
        pdf_path: Path to the opAD PDF

    Returns:
        Tuple of (default_code, default_description)
    """
    try:
        order = parse_opad_pdf(pdf_path)
        client_first = order.client.split()[0] if order.client else "CLIENT"
        code = f"opAD {client_first} {order.estimate_number}"
        description = order.description if order.description else order.product
        return (code, description)
    except Exception as e:
        print(f"[opAD] ⚠ Could not parse defaults: {e}")
        return ("opAD Order", "opAD Order")
