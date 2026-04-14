"""
Prosio Communications Order Automation

Handles browser automation for Prosio "Media Contract" Excel orders
(e.g. AQMD / Sacramento Metro Air Quality Management District).

Business Rules:
  - Source file: .xlsm/.xlsx Excel (not a PDF)
  - Single market per file (CVC for Sacramento orders)
  - Paid lines:  spot_code=2, gross rate from Excel
  - Bonus lines: spot_code=10, rate=0
  - Weekly spot counts consolidated via EtereClient.consolidate_weeks
  - Block prefixes derived from language (e.g. "M" for Mandarin)
  - Sunday 6-7a rule applied per line
  - Master market: NYC (standard Crossings TV)
  - Billing: Agency (Prosio is an agency)
"""

import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Optional

_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from browser_automation.etere_client import EtereClient
from browser_automation.parsers.prosio_parser import ProsioOrder, ProsioLine, parse_prosio_excel
from src.domain.enums import BillingType, OrderType, SeparationInterval

PROSIO_SEPARATION = SeparationInterval.DEFAULT.value   # (15, 0, 0)
CUSTOMER_DB_PATH  = os.path.join("data", "customers.db")


# ─────────────────────────────────────────────────────────────────────────────
# CUSTOMER DATABASE
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_customer(advertiser: str) -> Optional[dict]:
    """Look up advertiser in customer DB by name. Returns row dict or None."""
    try:
        import sqlite3
        conn = sqlite3.connect(CUSTOMER_DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM customers WHERE LOWER(customer_name) = LOWER(?)",
            (advertiser,)
        )
        row = cur.fetchone()
        conn.close()
        if row:
            sep = (
                row['separation_customer'] or 15,
                row['separation_event']    or 0,
                row['separation_order']    or 0,
            )
            return {
                'customer_id':   row['customer_id'],
                'code_name':     row['code_name'] or "",
                'description_name': row['description_name'] or "",
                'include_market_in_code': bool(row['include_market_in_code']),
                'separation':    sep,
            }
    except Exception as e:
        print(f"[CUSTOMER] DB lookup error: {e}")
    return None


def _save_new_customer(customer_id: str, advertiser: str) -> None:
    """Upsert a minimal customer record into the customer DB."""
    try:
        import sqlite3
        conn = sqlite3.connect(CUSTOMER_DB_PATH)
        conn.execute(
            """INSERT OR REPLACE INTO customers
               (customer_id, customer_name, order_type, billing_type)
               VALUES (?, ?, 'prosio', 'agency')""",
            (customer_id, advertiser)
        )
        conn.commit()
        conn.close()
        print(f"[CUSTOMER] ✓ Saved {advertiser!r} (ID {customer_id}) to DB")
    except Exception as e:
        print(f"[CUSTOMER] Failed to save customer: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# UPFRONT INPUT GATHERING
# ─────────────────────────────────────────────────────────────────────────────

def gather_prosio_inputs(file_path: str) -> Optional[dict]:
    """
    Parse Excel and gather all user inputs before the browser session opens.

    Returns:
        Dict with keys: contract_code, description, notes, customer_id, separation.
        Returns None if the user cancels.
    """
    print("\n" + "=" * 70)
    print("PROSIO ORDER - INPUT COLLECTION")
    print("=" * 70)

    print("\n[PARSE] Reading Excel…")
    try:
        order = parse_prosio_excel(file_path)
    except Exception as exc:
        print(f"[PARSE] ✗ Failed: {exc}")
        return None

    paid_lines  = [l for l in order.lines if not l.is_bonus]
    bonus_lines = [l for l in order.lines if l.is_bonus]
    total_paid  = sum(l.total_spots for l in paid_lines)
    total_bonus = sum(l.total_spots for l in bonus_lines)

    print(f"\nAgency:      {order.agency}")
    print(f"Advertiser:  {order.advertiser}")
    print(f"Contact:     {order.contact}  ({order.email})")
    print(f"Station:     {order.station}")
    print(f"Market:      {order.market}")
    print(f"Flight:      {order.flight_start} – {order.flight_end}")
    print(f"Weeks:       {len(order.week_start_dates)}  ({order.week_start_dates[0]} – {order.week_start_dates[-1]})")
    print(f"Lines:       {len(order.lines)}  ({len(paid_lines)} paid, {len(bonus_lines)} bonus)")
    print(f"Spots:       {total_paid} paid + {total_bonus} bonus = {total_paid + total_bonus} total")
    print()

    for line in order.lines:
        d = line.get_etere_days()
        t = line.get_etere_time_str()
        tag = "BONUS" if line.is_bonus else "PAID "
        print(f"  [{tag}] {line.language} | {line.daypart} | ${line.rate} | {line.total_spots} spots")
        print(f"         → Etere: {d} {t}")
    print()

    # ── Customer lookup ───────────────────────────────────────────────────────
    customer_info = _lookup_customer(order.advertiser)
    customer_id: Optional[int] = None
    separation = PROSIO_SEPARATION

    if customer_info:
        customer_id = int(customer_info['customer_id'])
        separation  = customer_info['separation']
        print(f"[CUSTOMER] ✓ Found in DB: {order.advertiser} → ID {customer_id}")
    else:
        print(f"[CUSTOMER] Not found in DB for '{order.advertiser}'")
        raw_id = input("  Enter Etere customer ID (or blank to select in browser): ").strip()
        if raw_id.isdigit():
            customer_id = int(raw_id)
            save_yn = input(
                f"  Save '{order.advertiser}' (ID {customer_id}) to DB for next time? (y/n): "
            ).strip().lower()
            if save_yn == 'y':
                _save_new_customer(str(customer_id), order.advertiser)
    print()

    # ── Contract code ─────────────────────────────────────────────────────────
    print("[1/3] Contract Code")
    print("-" * 70)
    # Suggest: first word of advertiser + year from flight_start
    year = order.flight_start[-4:] if order.flight_start else "2026"
    default_code = f"AQMD{year}" if "quality" in order.advertiser.lower() else f"PROSIO{year}"
    if customer_info and customer_info.get('code_name'):
        mkt_suffix = order.market if customer_info.get('include_market_in_code') else ""
        default_code = customer_info['code_name'] + mkt_suffix
    print(f"Default: {default_code}")
    user_code = input(f"Contract code [{default_code}]: ").strip()
    contract_code = user_code if user_code else default_code
    print()

    # ── Description ───────────────────────────────────────────────────────────
    print("[2/3] Contract Description")
    print("-" * 70)
    default_desc = order.advertiser
    if customer_info and customer_info.get('description_name'):
        default_desc = customer_info['description_name']
    print(f"Default: {default_desc}")
    user_desc = input(f"Description [{default_desc}]: ").strip()
    description = user_desc if user_desc else default_desc
    print()

    # ── Notes ─────────────────────────────────────────────────────────────────
    print("[3/3] Notes (optional — press Enter to skip)")
    print("-" * 70)
    notes = input("Notes: ").strip()
    print()

    return {
        'contract_code': contract_code,
        'description':   description,
        'notes':         notes,
        'customer_id':   customer_id,
        'separation':    separation,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT CREATION
# ─────────────────────────────────────────────────────────────────────────────

def _create_prosio_contract(
    etere: EtereClient,
    order: ProsioOrder,
    inputs: dict,
) -> bool:
    """
    Create the Etere contract for a Prosio Media Contract order.

    Workflow:
    1. Create contract header
    2. For each line (paid then bonus):
       - Apply Sunday 6-7a rule
       - Consolidate consecutive identical weeks → Etere line(s)
    """
    try:
        customer_id = inputs.get('customer_id')
        separation  = inputs.get('separation', PROSIO_SEPARATION)

        print(f"[PROSIO] Creating contract for {order.advertiser}")

        # ── Contract header ───────────────────────────────────────────────────
        contract_number = etere.create_contract_header(
            customer_id=customer_id,
            code=inputs['contract_code'],
            description=inputs['description'],
            contract_start=order.flight_start,
            contract_end=order.flight_end,
            customer_order_ref=None,
            notes=inputs.get('notes', ''),
            charge_to=BillingType.CUSTOMER_SHARE_AGENCY.get_charge_to(),
            invoice_header=BillingType.CUSTOMER_SHARE_AGENCY.get_invoice_header(),
        )

        if not contract_number:
            print("[PROSIO] ✗ Failed to create contract header")
            return False

        print(f"[PROSIO] ✓ Contract created: {contract_number}")

        # ── Lines ─────────────────────────────────────────────────────────────
        line_count = 0

        for line in order.lines:
            spot_code  = 10 if line.is_bonus else 2
            rate       = float(line.rate)
            duration_s = line.get_duration_seconds()
            etere_days = line.get_etere_days()
            time_str   = line.get_etere_time_str()

            time_from, time_to = EtereClient.parse_time_range(time_str)
            description = line.get_description(etere_days, time_str)

            # Sunday 6-7a rule
            adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(etere_days, time_str)

            # Consolidate consecutive weeks with same spot count
            ranges = EtereClient.consolidate_weeks(
                line.weekly_spots,
                order.week_start_dates,
                flight_end=order.flight_end,
            )

            tag = "BONUS" if line.is_bonus else "PAID "
            print(f"\n  [{tag}] {description}")
            print(f"    Rate: ${line.rate}  |  {len(ranges)} Etere line(s)")

            for rng in ranges:
                line_count += 1
                total_spots = rng['spots_per_week'] * rng['weeks']

                print(f"    Line {line_count}: "
                      f"{rng['start_date']} – {rng['end_date']} "
                      f"({rng['spots_per_week']} spots/wk × {rng['weeks']} wks = {total_spots})")

                success = etere.add_contract_line(
                    contract_number=contract_number,
                    market=order.market,
                    start_date=rng['start_date'],
                    end_date=rng['end_date'],
                    days=adjusted_days,
                    time_from=time_from,
                    time_to=time_to,
                    description=description,
                    spot_code=spot_code,
                    duration_seconds=duration_s,
                    total_spots=total_spots,
                    spots_per_week=rng['spots_per_week'],
                    rate=rate,
                    separation_intervals=separation,
                    is_bookend=False,
                    is_billboard=False,
                )

                if not success:
                    print(f"    ✗ Failed to add line {line_count}")
                    return False

        print(f"\n[PROSIO] ✓ All {line_count} Etere lines added successfully")
        return True

    except Exception as exc:
        print(f"\n[PROSIO] ✗ Error creating contract: {exc}")
        import traceback
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PROCESSING FUNCTION  (called by order_processing_service)
# ─────────────────────────────────────────────────────────────────────────────

def process_prosio_order(
    driver,
    file_path: str,
    shared_session=None,
    pre_gathered_inputs: Optional[dict] = None,
) -> bool:
    """
    Process a Prosio Media Contract Excel file and create the contract in Etere.

    Args:
        driver:               Selenium WebDriver
        file_path:            Path to Prosio .xlsm/.xlsx file
        shared_session:       Optional shared Etere session
        pre_gathered_inputs:  Pre-gathered inputs dict (skips upfront prompts)

    Returns:
        True if contract created successfully, False otherwise.
    """
    try:
        order = parse_prosio_excel(file_path)

        print(f"\n{'=' * 70}")
        print("PROSIO ORDER PROCESSING")
        print(f"{'=' * 70}")
        print(f"Advertiser:  {order.advertiser}")
        print(f"Market:      {order.market}")
        print(f"Flight:      {order.flight_start} – {order.flight_end}")
        print(f"Lines:       {len(order.lines)}")
        print(f"{'=' * 70}\n")

        if pre_gathered_inputs:
            inputs = pre_gathered_inputs
            print("[INFO] Using pre-gathered inputs\n")
        else:
            inputs = gather_prosio_inputs(file_path)
            if not inputs:
                print("[PROSIO] Input gathering cancelled")
                return False

        etere = EtereClient(driver)
        return _create_prosio_contract(etere, order, inputs)

    except Exception as exc:
        print(f"\n[PROSIO] ✗ Fatal error: {exc}")
        import traceback
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE TEST ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) < 2:
        print("Usage: python prosio_automation.py <excel_path>")
        _sys.exit(1)
    inputs = gather_prosio_inputs(_sys.argv[1])
    if inputs:
        print("\n[DRY RUN] Would create contract with:")
        for k, v in inputs.items():
            print(f"  {k}: {v}")
