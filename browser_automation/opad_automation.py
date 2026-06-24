"""
opAD Automation - Browser Automation for opAD Agency Orders

Uses EtereClient for ALL Etere interactions.
Parser handles PDF extraction; this file handles business logic + orchestration.

opAD Business Rules:
- NYC market ONLY (always)
- Multiple clients possible under opAD → customer DB lookup with manual fallback
- Separation intervals: Customer=15, Order=15, Event=0
- Billing: Charge To = "Customer share indicating agency %", Invoice Header = "Agency"
- Description from IO → contract header Notes field
- Estimate number → Customer Order Ref
- Bonus lines (rate=0) → spot code BNS (10)
- Weekly distribution splitting on gaps and differing spot counts
- Sunday 6-7a paid programming rule applies
"""

import sqlite3
from typing import Optional

from etere_client import EtereClient
from parsers.opad_parser import (
    OpADLine,
    OpADOrder,
    analyze_weekly_distribution,
    format_time_for_description,
    parse_opad_pdf,
)

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

AGENCY_NAME = "opAD"
DEFAULT_MARKET = "NYC"

# Separation intervals: (customer, order, event) in minutes
SEPARATION_INTERVALS = (15, 15, 0)

# Billing
CHARGE_TO = "Customer share indicating agency %"
INVOICE_HEADER = "Agency"

# Spot codes
SPOT_CODE_PAID = 2       # Paid Commercial
SPOT_CODE_BONUS = 10     # BNS / Bonus Spot

# Customer database path (relative to project root)
from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMERS_DB_PATH

# ─────────────────────────────────────────────────────────────────────────────
# DIRECT DB HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date(s):
    from datetime import date, datetime
    if isinstance(s, date):
        return s
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


def _secs_to_duration(secs: int) -> str:
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:00"


def _create_opad_contract_direct(pdf_path: str, user_input) -> bool:
    """Enter opAD order directly via DB stored procedures (no browser)."""
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    if isinstance(user_input, dict):
        customer_id = user_input.get('customer_id')
        order_code  = user_input.get('order_code', '')
        description = user_input.get('description', '')
        separation  = user_input.get('separation', SEPARATION_INTERVALS)
    else:
        customer_id, _ = _resolve_customer_id(pdf_path)
        order_code  = user_input.order_code
        description = user_input.description
        separation  = user_input.separation_intervals or SEPARATION_INTERVALS

    if customer_id is None:
        print("[OPAD DIRECT] ✗ No customer_id — cannot enter without a known ID")
        return False

    conn = None
    try:
        order = parse_opad_pdf(pdf_path)
        conn  = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=order_code,
            description=description,
            customer_id=int(customer_id),
            contract_date=_parse_date(order.flight_start),
            contract_end_date=_parse_date(order.flight_end),
            contract_type=1,
            billing_type="agency",
            note=description,
            customer_order_ref=order.estimate_number,
            allow_rename=True,
        )
        if not contract_id:
            print("[OPAD DIRECT] ✗ Failed to create contract header")
            return False
        print(f"[OPAD DIRECT] ✓ Contract ID={contract_id}")

        if customer_id:
            _save_customer_to_db(order.client, customer_id)

        line_count = 0
        for line in order.lines:
            etere_lines = _build_etere_lines(line, order)
            for el in etere_lines:
                line_count += 1
                print(f"  [LINE {line_count}] {el['description']}: "
                      f"{el['start_date']}–{el['end_date']} "
                      f"({el['spots_per_week']}/wk={el['total_spots']})")
                client.add_contract_line(
                    market=DEFAULT_MARKET,
                    days=el['days'],
                    time_range=f"{el['time_from']}-{el['time_to']}",
                    description=el['description'],
                    rate=float(el['rate']),
                    total_spots=el['total_spots'],
                    spots_per_week=el['spots_per_week'],
                    date_from=_parse_date(el['start_date']),
                    date_to=_parse_date(el['end_date']),
                    duration=_secs_to_duration(line.duration),
                    is_bonus=(el['spot_code'] == SPOT_CODE_BONUS),
                    booking_code=el['spot_code'],
                    separation_intervals=separation,
                )

        conn.commit()
        conn.close()
        print(f"[OPAD DIRECT] ✓ {line_count} line(s) entered")
        return True

    except Exception as exc:
        print(f"[OPAD DIRECT] ✗ {exc}")
        import traceback; traceback.print_exc()
        if conn:
            try: conn.rollback(); conn.close()
            except: pass
        return False
# ═══════════════════════════════════════════════════════════════════════════════
# PRE-GATHER (called before processing begins)
# ═══════════════════════════════════════════════════════════════════════════════

def gather_opad_inputs(pdf_path: str) -> Optional[dict]:
    """Collect all opAD inputs upfront before processing begins."""
    try:
        order = parse_opad_pdf(pdf_path)
    except Exception as e:
        print(f"[OPAD] ✗ Failed to parse PDF: {e}")
        return None

    # Build readable defaults
    client_short = (order.client or "").replace("NYS ", "NY ").strip()[:20]
    default_code = f"opAD {client_short} {order.estimate_number}".strip()
    default_desc = order.description or f"{order.client} Est {order.estimate_number}"

    # Customer ID (DB lookup → prompt if not found)
    customer_id, _ = _resolve_customer_id(pdf_path)

    order_code  = input(f"  Enter order code (default: {default_code}): ").strip() or default_code
    description = input(f"  Enter description (default: {default_desc}): ").strip() or default_desc

    return {
        'customer_id': customer_id,
        'order_code':  order_code,
        'description': description,
        'separation':  SEPARATION_INTERVALS,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def process_opad_order(
    pdf_path: str,
    shared_session=None,
    pre_gathered_inputs=None,
) -> bool:
    """
    Process an opAD order end-to-end via direct DB entry.

    Args:
        pdf_path: Path to the opAD PDF
        shared_session: Unused (direct DB — no browser session needed)
        pre_gathered_inputs: Dict with keys gathered upfront:
            - order_code: Contract code (e.g., "opAD NYSDOH 2824")
            - description: Contract description (goes to Notes)
            - customer_id: Customer ID (int or None for manual search)

    Returns:
        True if successful, False otherwise
    """
    return _create_opad_contract_direct(pdf_path, pre_gathered_inputs)


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
