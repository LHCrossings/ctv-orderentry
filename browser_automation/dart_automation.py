"""
DART (Dallas Area Rapid Transit) Automation.

Gathers user inputs and enters DART insertion orders into Etere for
The Asian Channel (KLEG 44.3, Dallas).

Master market: DAL — this is the explicit exception alongside WorldLink TAC.
Market on every line: "DAL"
Billing: client (charge_to="Customer", invoice_header="Customer")
"""

import os
import sqlite3
from decimal import Decimal
from typing import Optional

from browser_automation.etere_client import EtereClient
from browser_automation.parsers.dart_parser import DartOrder, parse_dart_xlsx, parse_dart_schedule

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH


# ─────────────────────────────────────────────────────────────────────────────
# DIRECT DB HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date(s):
    from datetime import datetime, date
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


def _create_dart_contract_direct(xlsx_path: str, user_input: dict) -> Optional[str]:
    """Enter DART order directly via DB stored procedures (no browser). Master market: DAL."""
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = user_input.get('customer_id')
    if customer_id is None:
        print("[DART DIRECT] ✗ No customer_id — cannot enter without a known ID")
        return None

    order: DartOrder = parse_dart_xlsx(xlsx_path)
    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("DAL")

        week_dates_str  = [d.strftime("%m/%d/%Y") for d in order.week_start_dates]
        flight_end_str  = order.flight_end.strftime("%m/%d/%Y")

        contract_id = client.create_contract_header(
            code=user_input['code'],
            description=user_input['description'],
            customer_id=int(customer_id),
            contract_date=order.flight_start,
            contract_end_date=order.flight_end,
            contract_type=1,
            billing_type="client",
            allow_rename=True,
        )
        if not contract_id:
            print("[DART DIRECT] ✗ Failed to create contract header")
            return None
        print(f"[DART DIRECT] ✓ Contract ID={contract_id}")

        separation   = user_input.get('separation', (15, 0, 0))
        duration_str = _secs_to_duration(order.duration_seconds)
        line_num     = 0

        for ln in order.lines:
            days, time_from, time_to = parse_dart_schedule(ln.schedule)
            is_bonus     = ln.is_bonus
            booking_code = 10 if is_bonus else 2
            line_desc    = f"BNS {ln.programming}" if is_bonus else f"{ln.programming} {ln.schedule}"

            segments = EtereClient.consolidate_weeks(ln.spot_counts, week_dates_str, flight_end_str)

            for seg in segments:
                line_num       += 1
                spots_per_week  = seg['spots_per_week']
                total_spots     = spots_per_week * seg['weeks']
                print(f"  [LINE {line_num}] {line_desc}: "
                      f"{seg['start_date']}–{seg['end_date']} "
                      f"({spots_per_week}/wk×{seg['weeks']}={total_spots})")
                client.add_contract_line(
                    market="DAL",
                    days=days,
                    time_range=f"{time_from}-{time_to}",
                    description=line_desc,
                    rate=float(ln.rate),
                    total_spots=total_spots,
                    spots_per_week=spots_per_week,
                    date_from=_parse_date(seg['start_date']),
                    date_to=_parse_date(seg['end_date']),
                    duration=duration_str,
                    is_bonus=is_bonus,
                    booking_code=booking_code,
                    separation_intervals=separation,
                )

        conn.commit()
        conn.close()
        print(f"[DART DIRECT] ✓ {line_num} line(s) entered")
        return str(contract_id)

    except Exception as exc:
        print(f"[DART DIRECT] ✗ {exc}")
        import traceback; traceback.print_exc()
        if conn:
            try: conn.rollback(); conn.close()
            except: pass
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Customer DB helpers (same pattern as charmaine_automation)
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_customer(name: str, db_path: str = CUSTOMER_DB_PATH) -> Optional[dict]:
    """Case-insensitive fuzzy lookup in customers.db."""
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM customers WHERE LOWER(customer_name) = LOWER(?)", (name,)
        )
        row = cur.fetchone()
        if row:
            conn.close()
            return dict(row)
        # Partial match
        cur.execute("SELECT * FROM customers")
        name_lower = name.lower()
        for row in cur.fetchall():
            n = row["customer_name"].lower()
            if n in name_lower or name_lower in n:
                conn.close()
                return dict(row)
        conn.close()
    except Exception as exc:
        print(f"[CUSTOMER DB] ⚠ Lookup error: {exc}")
    return None


def _upsert_customer(
    customer_id: str,
    customer_name: str,
    abbreviation: str,
    db_path: str = CUSTOMER_DB_PATH,
) -> None:
    """Insert or replace DART customer record."""
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            """INSERT OR REPLACE INTO customers
               (customer_id, customer_name, order_type, abbreviation,
                default_market, billing_type,
                separation_customer, separation_event, separation_order)
               VALUES (?, ?, 'dart', ?, 'DAL', 'client', 15, 0, 0)""",
            (customer_id, customer_name, abbreviation),
        )
        conn.commit()
        conn.close()
        print(f"[CUSTOMER DB] ✓ Saved: {customer_name} → ID {customer_id}")
    except Exception as exc:
        print(f"[CUSTOMER DB] ✗ Save failed: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Input gathering
# ─────────────────────────────────────────────────────────────────────────────

def gather_dart_inputs(xlsx_path: str) -> Optional[dict]:
    """
    Parse the DART xlsx and collect all user inputs needed before the browser opens.

    Args:
        xlsx_path: Path to the .xlsx file.

    Returns:
        Dict with keys: order, customer_id, code, description, separation.
        Returns None if the user cancels.
    """
    order = parse_dart_xlsx(xlsx_path)

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("DART — Dallas Area Rapid Transit")
    print(f"  Station : {order.station}")
    print(f"  Contact : {order.contact}")
    print(f"  Duration: :{order.duration_seconds}s")
    print(f"  Flight  : {order.flight_start.strftime('%m/%d/%Y')} – "
          f"{order.flight_end.strftime('%m/%d/%Y')}  ({len(order.week_start_dates)} weeks)")
    print(f"  Lines   : {len(order.paid_lines)} paid, {len(order.bonus_lines)} bonus")
    print(f"  Cost    : ${order.total_cost:,.2f}")
    print("  Paid lines:")
    for ln in order.paid_lines:
        print(f"    {ln.programming:<25s}  {ln.schedule:<25s}  "
              f"${ln.rate}/spot  {ln.spot_counts}  total={ln.total_spots}")
    print("  Bonus lines:")
    for ln in order.bonus_lines:
        print(f"    {ln.programming:<25s}  {ln.schedule:<25s}  {ln.spot_counts}  total={ln.total_spots}")
    print(f"{'─'*60}")

    # ── Customer lookup ───────────────────────────────────────────────────
    existing = _lookup_customer(order.client)
    customer_id: Optional[str] = None
    abbreviation: str = ""

    if existing:
        stored_id = existing.get("customer_id", "")
        stored_abbr = existing.get("abbreviation", "") or existing.get("code_name", "")
        print(f"\n[CUSTOMER DB] Found: {existing['customer_name']}")
        print(f"  Customer ID : {stored_id}")
        print(f"  Code prefix : {stored_abbr}")
        response = input(
            f"  Use stored customer ID '{stored_id}'? [Enter=yes / type new ID]: "
        ).strip()
        if response == "":
            customer_id = stored_id
            abbreviation = stored_abbr
        else:
            customer_id = response
            abbreviation = stored_abbr
    else:
        print(f"\n[CUSTOMER DB] '{order.client}' not found in database.")
        customer_id = input("  Enter Etere customer ID: ").strip()
        if not customer_id:
            print("[CANCELLED] No customer ID entered.")
            return None
        abbreviation = input(
            "  Enter contract code prefix (e.g. 'DART'): "
        ).strip()
        desc_name = input(
            "  Enter description prefix (e.g. 'DART'): "
        ).strip()
        _upsert_customer(customer_id, order.client, abbreviation)

    # ── Contract code ─────────────────────────────────────────────────────
    default_code = abbreviation or "DART"
    code_input = input(
        f"\nContract code [{default_code}]: "
    ).strip()
    code = code_input if code_input else default_code

    # ── Contract description ──────────────────────────────────────────────
    default_desc = f"{abbreviation or 'DART'} {order.client}"
    desc_input = input(f"Contract description [{default_desc}]: ").strip()
    description = desc_input if desc_input else default_desc

    # ── Separation ────────────────────────────────────────────────────────
    separation = (15, 0, 0)
    if existing:
        sep_c = int(existing.get("separation_customer", 15) or 15)
        sep_e = int(existing.get("separation_event", 0) or 0)
        sep_o = int(existing.get("separation_order", 0) or 0)
        separation = (sep_c, sep_e, sep_o)

    return {
        "order": order,
        "customer_id": customer_id,
        "code": code,
        "description": description,
        "separation": separation,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Order processing
# ─────────────────────────────────────────────────────────────────────────────

def process_dart_order(xlsx_path: str, user_input: dict) -> Optional[str]:
    """
    Enter a DART order into Etere via direct DB.

    Args:
        xlsx_path: Path to the .xlsx file.
        user_input: Dict returned by gather_dart_inputs.

    Returns:
        Contract number string on success, None on failure.
    """
    return _create_dart_contract_direct(xlsx_path, user_input)
