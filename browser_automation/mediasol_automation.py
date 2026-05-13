"""
Media Solutions / Pulsar Advertising c/o Mediasol Automation

Uses EtereClient for ALL Etere interactions.
Parser handles PDF extraction; this file handles business logic + orchestration.

Business Rules:
- Markets: LAX (Los Angeles) primary; SFO and CVC possible
- Separation intervals: Customer=25, Event=0, Order=0
  (PDF says "30 min" — house rule maps this to 25 for Etere entry)
- Billing: Charge To = "Customer share indicating agency %", Invoice Header = "Agency"
- IO shows STN Net rates (rates_are_net=True) — gross up before Etere entry
- Description from IO → contract header Notes field
- Estimate number → Customer Order Ref
- Bonus lines (rate=0) → spot code BNS (10)
- Weekly distribution splitting on gaps and differing spot counts
- Sunday 6-7a paid programming rule applies
- Strata IO format (same family as H&L Partners)
"""

import sqlite3
import time
from pathlib import Path
from typing import Optional

from selenium import webdriver

from browser_automation.etere_client import EtereClient
from browser_automation.parsers.mediasol_parser import (
    MediasolEstimate,
    MediasolLine,
    analyze_weekly_distribution,
    convert_hl_days_to_etere,
    extract_language_from_program,
    format_time_for_description,
    parse_mediasol_pdf,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

AGENCY_NAME = "mediasol"

# Separation intervals: (customer, event, order) in minutes
# PDF says "30 min between spots" — house rule: enter 25 in Etere
SEPARATION_INTERVALS = (25, 0, 0)

# Billing
CHARGE_TO = "Customer share indicating agency %"
INVOICE_HEADER = "Agency"

# Spot codes
SPOT_CODE_PAID = 2
SPOT_CODE_BONUS = 10

# Customer database path (relative to project root)
CUSTOMERS_DB_PATH = Path("data") / "customers.db"

# Known customer IDs (avoid DB round-trip for well-known clients)
KNOWN_CUSTOMER_IDS = {
    "ochca": 364,
    "oc health care agency": 364,
}

# Market mapping
MEDIASOL_MARKET_MAPPING = {
    "LOS ANGELES": "LAX",
    "LOS ANGELES-LOS ANGELES": "LAX",
    "LA": "LAX",
    "LAX": "LAX",
    "SAN FRANCISCO": "SFO",
    "SAN FRANCISCO-SAN FRANCISCO": "SFO",
    "SF": "SFO",
    "SFO": "SFO",
    "SACRAMENTO": "CVC",
    "SACRAMENTO-STOCKTON": "CVC",
    "CENTRAL VALLEY": "CVC",
    "CVC": "CVC",
}


def _normalize_mediasol_market(market_text: str) -> str:
    market_upper = market_text.upper().strip()
    return MEDIASOL_MARKET_MAPPING.get(market_upper, "LAX")  # Default to LAX


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def process_mediasol_order(
    driver: webdriver.Chrome,
    pdf_path: str,
    user_input: dict,
) -> bool:
    """
    Process a Media Solutions order end-to-end.

    Args:
        driver: Selenium WebDriver (already logged in)
        pdf_path: Path to the Mediasol PDF
        user_input: Dict from gather_mediasol_inputs

    Returns:
        True if all estimates processed successfully
    """
    etere = EtereClient(driver)
    try:
        return _execute_order(etere, pdf_path, user_input)
    except Exception as e:
        print(f"[MEDIASOL] ✗ Order failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def _execute_order(etere: EtereClient, pdf_path: str, user_input: dict) -> bool:
    if isinstance(user_input, dict):
        order_code = user_input['order_code']
        description = user_input['description']
        customer_id = user_input.get('customer_id')
        separation = user_input.get('separation') or SEPARATION_INTERVALS
        existing_contract_number = user_input.get('existing_contract_number')
        gross_up_factor = user_input.get('gross_up_factor', 1.0)
    else:
        order_code = user_input.order_code
        description = user_input.description
        customer_id = getattr(user_input, 'customer_id', None)
        separation = getattr(user_input, 'separation_intervals', None) or SEPARATION_INTERVALS
        existing_contract_number = getattr(user_input, 'existing_contract_number', None)
        gross_up_factor = getattr(user_input, 'gross_up_factor', 1.0)

    if customer_id is None:
        customer_id, _ = _resolve_customer_id(pdf_path)

    print(f"\n{'='*60}")
    print(f"Processing Media Solutions Order: {pdf_path}")
    print(f"{'='*60}\n")

    estimates = parse_mediasol_pdf(pdf_path)
    if not estimates:
        print("[MEDIASOL] ✗ No estimates found in PDF")
        return False

    print(f"[PARSE] ✓ Found {len(estimates)} estimate(s)")

    if gross_up_factor != 1.0:
        for est in estimates:
            for line in est.lines:
                if not line.is_bonus():
                    line.rate = round(line.rate * gross_up_factor, 2)
        print(f"[PARSE] ✓ Gross-up applied (factor {gross_up_factor:.6f})")

    all_success = True

    for est_idx, estimate in enumerate(estimates, 1):
        print(f"\n[ESTIMATE {est_idx}/{len(estimates)}]")
        print(f"  Estimate #: {estimate.estimate_number}")
        print(f"  Client:     {estimate.client}")
        print(f"  Flight:     {estimate.flight_start} - {estimate.flight_end}")
        print(f"  Market:     {estimate.market}")
        print(f"  Lines:      {len(estimate.lines)}")

        est_order_code = (
            f"{order_code} Est {estimate.estimate_number}" if len(estimates) > 1 else order_code
        )
        est_notes = estimate.description if estimate.description else description
        market_code = _normalize_mediasol_market(estimate.market)

        if existing_contract_number:
            contract_number = existing_contract_number
            print(f"\n{'!'*60}")
            print(f"  REVISION MODE — reusing contract {contract_number}")
            print(f"  Please delete ALL existing lines from contract {contract_number} in Etere now.")
            input("  Press Enter when lines are cleared and ready to continue...")
            print(f"{'!'*60}")
        else:
            contract_number = etere.create_contract_header(
                customer_id=customer_id,
                code=est_order_code,
                description=description,
                contract_start=estimate.flight_start,
                contract_end=estimate.flight_end,
                customer_order_ref=estimate.estimate_number,
                notes=est_notes,
                charge_to=CHARGE_TO,
                invoice_header=INVOICE_HEADER,
            )
            if not contract_number:
                print(f"[MEDIASOL] ✗ Failed to create contract for estimate {estimate.estimate_number}")
                all_success = False
                continue
            print(f"[MEDIASOL] ✓ Contract created: {contract_number}")

        if customer_id:
            _save_customer_to_db(estimate.client, customer_id)

        line_count = 0
        for line in estimate.lines:
            etere_lines = _build_etere_lines(line, estimate, market_code)
            for etere_line in etere_lines:
                line_count += 1
                print(f"\n  [LINE {line_count}] {etere_line['description']}")
                print(f"    {etere_line['start_date']} - {etere_line['end_date']}")
                print(f"    {etere_line['spots_per_week']}/wk  rate=${etere_line['rate']}")

                success = etere.add_contract_line(
                    contract_number=contract_number,
                    market=market_code,
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
                    rate=etere_line["rate"],
                    separation_intervals=separation,
                )
                if not success:
                    print(f"    ✗ Failed to add line {line_count}")
                    all_success = False
                    break
                time.sleep(2)

            if not all_success:
                break

        print(f"\n{'='*60}")
        print(f"✓ MEDIASOL ESTIMATE {estimate.estimate_number} COMPLETE")
        print(f"  Contract: {contract_number}  Lines: {line_count}")
        print(f"{'='*60}")

    return all_success


# ═══════════════════════════════════════════════════════════════════════════════
# LINE BUILDING
# ═══════════════════════════════════════════════════════════════════════════════

def _build_etere_lines(
    line: MediasolLine,
    estimate: MediasolEstimate,
    market_code: str,
) -> list[dict]:
    ranges = analyze_weekly_distribution(
        line.weekly_spots,
        estimate.flight_start,
        estimate.flight_end,
    )

    etere_days = convert_hl_days_to_etere(line.days)
    time_fmt = format_time_for_description(line.time)
    language = extract_language_from_program(line.program)
    lang_suffix = f" {language}" if language and language != "Unknown" else ""

    spot_code = SPOT_CODE_BONUS if line.is_bonus() else SPOT_CODE_PAID
    description = f"{etere_days} {time_fmt}{lang_suffix}"

    time_from, time_to = EtereClient.parse_time_range(line.time)
    adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(etere_days, line.time)

    return [
        {
            "start_date": r["start_date"],
            "end_date": r["end_date"],
            "days": adjusted_days,
            "time_from": time_from,
            "time_to": time_to,
            "description": description,
            "spot_code": spot_code,
            "total_spots": r["spots"],
            "spots_per_week": r["spots_per_week"],
            "rate": line.rate,
        }
        for r in ranges
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOMER RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_customer_id(pdf_path: str) -> tuple[Optional[int], None]:
    try:
        estimates = parse_mediasol_pdf(pdf_path)
        client_name = estimates[0].client if estimates else None
    except Exception:
        client_name = None

    # Fast path: known clients
    if client_name:
        cid = KNOWN_CUSTOMER_IDS.get(client_name.lower().strip())
        if cid:
            print(f"[CUSTOMER] ✓ Known client: {client_name} → ID {cid}")
            return cid, None

    if not client_name:
        return _manual_customer_entry(), None

    # DB lookup
    try:
        if CUSTOMERS_DB_PATH.exists():
            with sqlite3.connect(str(CUSTOMERS_DB_PATH)) as conn:
                row = conn.execute(
                    "SELECT customer_id FROM customers WHERE customer_name = ? AND order_type = ?",
                    (client_name, AGENCY_NAME),
                ).fetchone()
                if row:
                    cid = int(row[0])
                    print(f"[CUSTOMER DB] ✓ Found: {client_name} → ID {cid}")
                    return cid, None
                for db_name, db_id in conn.execute(
                    "SELECT customer_name, customer_id FROM customers WHERE order_type = ?",
                    (AGENCY_NAME,),
                ).fetchall():
                    if (db_name.lower() in client_name.lower()
                            or client_name.lower() in db_name.lower()):
                        cid = int(db_id)
                        print(f"[CUSTOMER DB] ✓ Fuzzy: {client_name} ≈ {db_name} → ID {cid}")
                        return cid, None
    except Exception as e:
        print(f"[CUSTOMER DB] ⚠ Lookup failed: {e}")

    print(f"[CUSTOMER] Not found in DB: {client_name}")
    return _manual_customer_entry(), None


def _manual_customer_entry() -> Optional[int]:
    val = input("Enter customer ID (or press Enter to search in Etere): ").strip()
    if val:
        try:
            return int(val)
        except ValueError:
            print("[CUSTOMER] Invalid ID — will search in Etere")
    return None


def _save_customer_to_db(client_name: str, customer_id: int) -> None:
    try:
        if not CUSTOMERS_DB_PATH.exists():
            return
        with sqlite3.connect(str(CUSTOMERS_DB_PATH)) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO customers (customer_name, customer_id, order_type) VALUES (?, ?, ?)",
                (client_name, customer_id, AGENCY_NAME),
            )
            if conn.total_changes > 0:
                print(f"[CUSTOMER DB] ✓ Saved: {client_name} (ID: {customer_id})")
    except Exception as e:
        print(f"[CUSTOMER DB] ⚠ Could not save (non-fatal): {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# DEFAULT VALUES
# ═══════════════════════════════════════════════════════════════════════════════

def get_mediasol_defaults(pdf_path: str) -> tuple[str, str]:
    try:
        estimates = parse_mediasol_pdf(pdf_path)
        if not estimates:
            return ("Mediasol Order", "Media Solutions Order")
        est = estimates[0]
        market_code = _normalize_mediasol_market(est.market)

        from browser_automation.customer_defaults import ensure_template_columns, resolve_defaults
        ensure_template_columns(CUSTOMERS_DB_PATH)
        code, desc = resolve_defaults(
            customer_name=est.client,
            order_type=AGENCY_NAME,
            estimate_number=est.estimate_number,
            market=market_code,
            db_path=CUSTOMERS_DB_PATH,
        )
        if code and desc:
            return (code, desc)

        return (f"MS {est.estimate_number}", f"Mediasol {market_code} Est {est.estimate_number}")
    except Exception as e:
        print(f"[MEDIASOL] ⚠ Could not parse defaults: {e}")
    return ("Mediasol Order", "Media Solutions Order")


# ═══════════════════════════════════════════════════════════════════════════════
# UPFRONT INPUT COLLECTION
# ═══════════════════════════════════════════════════════════════════════════════

def gather_mediasol_inputs(pdf_path: str) -> dict | None:
    """
    Gather ALL user inputs before browser automation starts.
    Called by the orchestrator during the upfront input phase.
    """
    print("\n" + "=" * 70)
    print("MEDIA SOLUTIONS ORDER - UPFRONT INPUT COLLECTION")
    print("=" * 70)

    print("\n[PARSE] Reading PDF...")
    try:
        estimates = parse_mediasol_pdf(pdf_path)
    except Exception as e:
        print(f"[PARSE] ✗ Failed: {e}")
        return None

    if not estimates:
        print("[PARSE] ✗ No estimates found in PDF")
        return None

    est = estimates[0]
    print(f"[PARSE] ✓ Estimate #: {est.estimate_number}")
    print(f"[PARSE] ✓ Client:     {est.client}")
    print(f"[PARSE] ✓ Flight:     {est.flight_start} - {est.flight_end}")
    print(f"[PARSE] ✓ Market:     {est.market}")
    print(f"[PARSE] ✓ Lines:      {len(est.lines)}")
    if len(estimates) > 1:
        print(f"[PARSE] ℹ {len(estimates)} estimates — each becomes a separate contract")

    customer_id, _ = _resolve_customer_id(pdf_path)
    if customer_id and est.client:
        _save_customer_to_db(est.client, customer_id)

    suggested_code, suggested_desc = get_mediasol_defaults(pdf_path)
    print(f"\n[CONTRACT]")
    contract_code = input(f"  Code [{suggested_code}]: ").strip() or suggested_code
    description = input(f"  Description [{suggested_desc}]: ").strip() or suggested_desc

    # Revision detection — check PDF title line or ask
    is_revision = any(
        'revis' in e.description.lower()
        for e in estimates
    )
    existing_contract_number = None
    if is_revision:
        print(f"\n[REVISION] ⚠ Detected 'revised' in order description.")
    revision_input = input(
        f"  Is this a revision of an existing Etere contract? [{'Y/n' if is_revision else 'y/N'}]: "
    ).strip().lower()
    is_revision = (revision_input != 'n') if is_revision else (revision_input == 'y')
    if is_revision:
        existing_contract_number = input("  Enter existing Etere contract number: ").strip() or None
        if not existing_contract_number:
            print("  No contract number entered — will create a new contract.")

    # Gross-up (rates are always net for Mediasol)
    gross_up_factor = 1.0
    paid_rates = [ln.rate for e in estimates for ln in e.lines if not ln.is_bonus()]
    print(f"\n{'!'*70}")
    print("  NOTE: Mediasol IOs show STN Net rates. Gross up for agency commission.")
    sample = ", ".join(f"${r:.2f}" for r in paid_rates[:5])
    print(f"  Net rates: {sample}")
    gross_up = input("  Gross up these rates? [Y/n]: ").strip().lower()
    if gross_up != 'n':
        pct_str = input("  Agency commission % (e.g. 15): ").strip()
        try:
            pct = float(pct_str)
            gross_up_factor = 1.0 / (1.0 - pct / 100.0)
            print(f"  Gross-up factor: {gross_up_factor:.6f}")
        except ValueError:
            print("  Invalid % — rates left as-is.")
    print('!'*70)

    separation = SEPARATION_INTERVALS
    print(f"\n[BILLING]    ✓ {CHARGE_TO} / {INVOICE_HEADER}")
    print(f"[INTERVALS]  ✓ Customer={separation[0]}, Event={separation[1]}, Order={separation[2]}")
    print("\n" + "=" * 70)
    print("INPUT COLLECTION COMPLETE - Ready for automation")
    print("=" * 70)

    return {
        'order_code': contract_code,
        'description': description,
        'customer_id': customer_id,
        'separation': separation,
        'existing_contract_number': existing_contract_number,
        'gross_up_factor': gross_up_factor,
    }
