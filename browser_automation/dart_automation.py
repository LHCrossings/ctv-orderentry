"""
DART (Dallas Area Rapid Transit) Automation.

Gathers user inputs and enters DART insertion orders into Etere for
The Asian Channel (KLEG 44.3, Dallas).

Master market: DAL — this is the explicit exception alongside WorldLink TAC.
Market on every line: "DAL"
Billing: client (charge_to="Customer", invoice_header="Customer")
"""

import math
import os
import sqlite3
from decimal import Decimal
from typing import Optional

from browser_automation.etere_client import EtereClient
from browser_automation.parsers.dart_parser import DartOrder, parse_dart_xlsx, parse_dart_schedule

CUSTOMER_DB_PATH = os.path.join("data", "customers.db")


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

def process_dart_order(driver, xlsx_path: str, user_input: dict) -> Optional[str]:
    """
    Enter a DART order into Etere.

    Args:
        driver: Selenium WebDriver from the shared EtereSession.
        xlsx_path: Path to the .xlsx file (re-parsed from disk for safety).
        user_input: Dict returned by gather_dart_inputs.

    Returns:
        Contract number string on success, None on failure.
    """
    etere = EtereClient(driver)

    # DART is The Asian Channel Dallas — explicit DAL master market exception.
    # (Same rationale as WorldLink TAC — see lessons.md.)
    etere.set_master_market("DAL")

    # Re-parse from disk (same safety pattern as HL automation)
    order: DartOrder = parse_dart_xlsx(xlsx_path)

    customer_id = user_input["customer_id"]
    code = user_input["code"]
    description = user_input["description"]
    separation = user_input.get("separation", (15, 0, 0))

    flight_start_str = order.flight_start.strftime("%m/%d/%Y")
    flight_end_str = order.flight_end.strftime("%m/%d/%Y")

    print(f"\n[DART] Creating contract: {code}")
    print(f"[DART] Customer ID: {customer_id}")
    print(f"[DART] Flight: {flight_start_str} – {flight_end_str}")

    contract_number = etere.create_contract_header(
        customer_id=int(customer_id),
        code=code,
        description=description,
        contract_start=flight_start_str,
        contract_end=flight_end_str,
        charge_to="Customer",
        invoice_header="Customer",
    )

    if not contract_number:
        print("[DART] ✗ Failed to create contract header")
        return None

    print(f"[DART] ✓ Contract created: {contract_number}")

    # ── Add contract lines ─────────────────────────────────────────────────
    week_dates_str = [d.strftime("%m/%d/%Y") for d in order.week_start_dates]
    flight_end_str_fmt = order.flight_end.strftime("%m/%d/%Y")

    line_num = 0
    for ln in order.lines:
        line_num += 1
        days, time_from, time_to = parse_dart_schedule(ln.schedule)
        spot_code = 10 if ln.is_bonus else 2  # 10=BNS, 2=Paid Commercial
        rate = float(ln.rate)
        label = "BNS" if ln.is_bonus else "PAY"

        if ln.is_bonus:
            line_desc = f"BNS {ln.programming}"
        else:
            line_desc = f"{ln.programming} {ln.schedule}"

        # Consolidate consecutive weeks with identical spot counts
        segments = etere.consolidate_weeks(
            ln.spot_counts,
            week_dates_str,
            flight_end_str_fmt,
        )

        print(f"\n[DART] Line {line_num} [{label}] {ln.programming} — "
              f"{len(segments)} segment(s)")

        for seg in segments:
            spots_per_week = seg["spots_per_week"]
            total_spots = seg["spots_per_week"] * seg["weeks"]

            # Max daily run: ceil(spots_per_week / active_days_in_pattern)
            active_days = _count_active_days(days)
            max_daily = math.ceil(spots_per_week / active_days) if active_days > 0 else 1

            ok = etere.add_contract_line(
                contract_number=contract_number,
                market="DAL",
                start_date=seg["start_date"],
                end_date=seg["end_date"],
                days=days,
                time_from=time_from,
                time_to=time_to,
                description=line_desc,
                spot_code=spot_code,
                duration_seconds=order.duration_seconds,
                total_spots=total_spots,
                spots_per_week=spots_per_week,
                max_daily_run=max_daily,
                rate=rate,
                separation_intervals=separation,
            )

            status = "✓" if ok else "✗"
            print(f"  [{status}] {seg['start_date']} – {seg['end_date']}  "
                  f"{spots_per_week}/wk  {seg['weeks']} wk(s)  total={total_spots}")

            if not ok:
                print(f"[DART] ✗ Line {line_num} segment failed — aborting")
                return None

    print(f"\n[DART] ✓ All lines entered. Contract: {contract_number}")
    return contract_number


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_DAY_COUNTS = {
    "M-Su": 7,
    "M-F":  5,
    "M-Sa": 6,
    "Sa-Su": 2,
    "Sa":   1,
    "Su":   1,
}


def _count_active_days(days: str) -> int:
    """Return the number of active days in an Etere day pattern string."""
    return _DAY_COUNTS.get(days, 7)
