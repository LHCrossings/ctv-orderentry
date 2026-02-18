"""
H&L Partners Automation - Browser Automation for H&L Partners Agency Orders

Uses EtereClient for ALL Etere interactions.
Parser handles PDF extraction; this file handles business logic + orchestration.

H&L Partners Business Rules:
- Markets: SFO (San Francisco) or CVC (Sacramento) ONLY
- Multiple clients possible → customer DB lookup with manual fallback
- Separation intervals: Customer=25, Event=0, Order=0
- Billing: Charge To = "Customer share indicating agency %", Invoice Header = "Agency"
- Description from IO → contract header Notes field
- Estimate number → Customer Order Ref
- Language block filtering (Mandarin, Korean, Hindi, etc.)
- Bonus lines (rate=0) → spot code BNS (10)
- Weekly distribution splitting on gaps and differing spot counts
- Sunday 6-7a paid programming rule applies
- Strata IO format (same family as opAD/TCAA)
"""

import sqlite3
import time
from pathlib import Path
from typing import Optional

from selenium import webdriver

from browser_automation.etere_client import EtereClient
from parsers.hl_parser import (
    HLEstimate,
    HLLine,
    analyze_weekly_distribution,
    convert_hl_days_to_etere,
    extract_language_from_program,
    format_time_for_description,
    parse_hl_pdf,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

AGENCY_NAME = "hl"

# Separation intervals: (customer, event, order) in minutes
SEPARATION_INTERVALS = (25, 0, 0)

# Billing
CHARGE_TO = "Customer share indicating agency %"
INVOICE_HEADER = "Agency"

# Spot codes
SPOT_CODE_PAID = 2       # Paid Commercial
SPOT_CODE_BONUS = 10     # BNS / Bonus Spot

# Customer database path (relative to project root)
CUSTOMERS_DB_PATH = Path("data") / "customers.db"

# Market mapping for H&L (SFO and CVC only)
HL_MARKET_MAPPING = {
    "SAN FRANCISCO": "SFO",
    "SF": "SFO",
    "SFO": "SFO",
    "SACRAMENTO": "CVC",
    "CENTRAL VALLEY": "CVC",
    "CVC": "CVC",
    "SACRAMENTO-STOCKTON": "CVC",
}


def _normalize_hl_market(market_text: str) -> str:
    """
    Normalize H&L market name to standard code.

    H&L only operates in SFO and CVC markets.

    Args:
        market_text: Market name from PDF header

    Returns:
        Market code ("SFO" or "CVC")
    """
    market_upper = market_text.upper().strip()
    return HL_MARKET_MAPPING.get(market_upper, "SFO")  # Default to SFO


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def process_hl_order(
    driver: webdriver.Chrome,
    pdf_path: str,
    user_input: dict,
) -> bool:
    """
    Process an H&L Partners order end-to-end.

    Called by order_processing_service.py with the same signature as
    Daviselen/opAD/Admerasia: (driver, pdf_path, user_input).

    H&L PDFs can contain multiple estimates. Each estimate becomes a
    separate contract.

    Args:
        driver: Selenium WebDriver (already logged in via shared session)
        pdf_path: Path to the H&L Partners PDF
        user_input: Dict with keys:
            - order_code: Contract code (e.g., "HL Toyota 13915")
            - description: Contract description (goes to Notes)
            - customer_id: Customer ID (int or None for manual search)
            - separation_intervals: Optional override tuple

    Returns:
        True if ALL estimates processed successfully, False otherwise
    """
    etere = EtereClient(driver)

    try:
        return _execute_order(etere, pdf_path, user_input)
    except Exception as e:
        print(f"[H&L] ✗ Order failed: {e}")
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

    # Customer ID comes from user_input (resolved upfront by gather_hl_inputs).
    # Fall back to live DB lookup only if not provided (e.g. standalone mode).
    if hasattr(user_input, 'customer_id') and user_input.customer_id is not None:
        customer_id = user_input.customer_id
    else:
        customer_id, _ = _resolve_customer_id(pdf_path)

    # Separation intervals from user_input (confirmed by user in input_collectors)
    separation = user_input.separation_intervals or SEPARATION_INTERVALS

    # ── Parse PDF ──
    print(f"\n{'='*60}")
    print(f"Processing H&L Partners Order: {pdf_path}")
    print(f"{'='*60}\n")

    estimates = parse_hl_pdf(pdf_path)

    if not estimates:
        print("[H&L] ✗ No estimates found in PDF")
        return False

    print(f"[PARSE] ✓ Found {len(estimates)} estimate(s)")

    all_success = True

    for est_idx, estimate in enumerate(estimates, 1):
        print(f"\n[ESTIMATE {est_idx}/{len(estimates)}]")
        print(f"  Estimate #: {estimate.estimate_number}")
        print(f"  Description: {estimate.description}")
        print(f"  Client: {estimate.client}")
        print(f"  Flight: {estimate.flight_start} - {estimate.flight_end}")
        print(f"  Market: {estimate.market}")
        print(f"  Lines: {len(estimate.lines)}")

        # Build contract code for this estimate
        if len(estimates) > 1:
            est_order_code = f"{order_code} Est {estimate.estimate_number}"
        else:
            est_order_code = order_code

        # Use estimate description for notes
        est_notes = estimate.description if estimate.description else description

        # Normalize market
        market_code = _normalize_hl_market(estimate.market)

        # ── Create contract header ──
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
            print(f"[H&L] ✗ Failed to create contract header for estimate {estimate.estimate_number}")
            all_success = False
            continue

        print(f"[H&L] ✓ Contract created: {contract_number}")

        # ── Save customer to DB on first discovery ──
        if customer_id:
            _save_customer_to_db(estimate.client, customer_id)

        # ── Add lines ──
        line_count = 0
        for line_idx, line in enumerate(estimate.lines, 1):
            etere_lines = _build_etere_lines(line, estimate, market_code)

            for etere_line in etere_lines:
                line_count += 1
                print(f"\n  [LINE {line_count}] {etere_line['description']}")
                print(f"    {etere_line['start_date']} - {etere_line['end_date']}")
                print(f"    {etere_line['spots_per_week']}/wk, rate=${etere_line['rate']}")

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

        # ── Estimate summary ──
        print(f"\n{'='*60}")
        print(f"✓ H&L ESTIMATE {estimate.estimate_number} COMPLETE")
        print(f"  Contract: {contract_number}")
        print(f"  Lines Added: {line_count}")
        print(f"{'='*60}")

    return all_success


# ═══════════════════════════════════════════════════════════════════════════════
# LINE BUILDING
# ═══════════════════════════════════════════════════════════════════════════════

def _build_etere_lines(
    line: HLLine,
    estimate: HLEstimate,
    market_code: str,
) -> list[dict]:
    """
    Convert a single parsed H&L line into one or more Etere line dicts.

    Splits on:
    - Gaps (weeks with 0 spots)
    - Differing weekly spot counts

    Returns:
        List of dicts ready for etere.add_contract_line()
    """
    # Analyze weekly distribution (splits on gaps + differing counts)
    ranges = analyze_weekly_distribution(
        line.weekly_spots,
        estimate.flight_start,
        estimate.flight_end,
    )

    # Convert H&L day format to Etere format
    etere_days = convert_hl_days_to_etere(line.days)

    # Build description
    time_fmt = format_time_for_description(line.time)
    language = extract_language_from_program(line.program)
    lang_suffix = f" {language}" if language and language != "Unknown" else ""

    # Check for ASIAN ROTATOR/ROTATION lines:
    # These are BNS ROS lines that use the time listed on the IO.
    # The user will manually remove non-applicable shows in Etere.
    is_asian_rotator = "asian rotat" in line.program.lower()

    if is_asian_rotator:
        # Force BNS, use IO time, description = "BNS ROS"
        spot_code = SPOT_CODE_BONUS
        description = f"{etere_days} {time_fmt} BNS ROS"
    else:
        # Standard line
        spot_code = SPOT_CODE_BONUS if line.is_bonus() else SPOT_CODE_PAID
        description = f"{etere_days} {time_fmt}{lang_suffix}"

    # Parse time range using EtereClient universal parser
    time_from, time_to = EtereClient.parse_time_range(line.time)

    # Apply Sunday 6-7a rule
    adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(etere_days, line.time)
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
        pdf_path: Path to the H&L PDF (parsed to get client name)

    Returns:
        Tuple of (customer_id, None) - second element for backward compat
    """
    try:
        estimates = parse_hl_pdf(pdf_path)
        client_name = estimates[0].client if estimates else None
    except Exception:
        client_name = None

    if not client_name:
        return _manual_customer_entry(), None

    # Try database lookup
    try:
        db_path = CUSTOMERS_DB_PATH
        if db_path.exists():
            with sqlite3.connect(str(db_path)) as conn:
                # Exact match
                cursor = conn.execute(
                    "SELECT customer_id FROM customers WHERE customer_name = ? AND order_type = ?",
                    (client_name, AGENCY_NAME),
                )
                row = cursor.fetchone()
                if row:
                    cust_id = int(row[0])
                    print(f"[CUSTOMER DB] ✓ Found: {client_name} → ID {cust_id}")
                    return cust_id, None

                # Fuzzy: check containment
                cursor = conn.execute(
                    "SELECT customer_name, customer_id FROM customers WHERE order_type = ?",
                    (AGENCY_NAME,),
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

def get_hl_defaults(pdf_path: str) -> tuple[str, str]:
    """
    Get default contract code and description for an H&L order.

    Looks up customer templates in customers.db. If templates exist,
    populates them with estimate number and market. If not, returns
    generic fallbacks that the user will need to edit.

    Args:
        pdf_path: Path to the H&L PDF

    Returns:
        Tuple of (default_code, default_description)
    """
    from browser_automation.customer_defaults import (
        ensure_template_columns,
        resolve_defaults,
    )

    try:
        estimates = parse_hl_pdf(pdf_path)
        if not estimates:
            return ("HL Order", "H&L Partners Order")

        est = estimates[0]
        market_code = _normalize_hl_market(est.market)

        # Ensure DB has template columns (idempotent)
        ensure_template_columns(CUSTOMERS_DB_PATH)

        # Try to resolve from customer templates
        code, desc = resolve_defaults(
            customer_name=est.client,
            order_type=AGENCY_NAME,
            estimate_number=est.estimate_number,
            market=market_code,
            db_path=CUSTOMERS_DB_PATH,
        )

        if code and desc:
            return (code, desc)

        # No templates yet — return generic defaults
        return (f"HL {est.estimate_number}", f"H&L {market_code} Est {est.estimate_number}")

    except Exception as e:
        print(f"[H&L] ⚠ Could not parse defaults: {e}")
    return ("HL Order", "H&L Partners Order")


# ═══════════════════════════════════════════════════════════════════════════════
# UPFRONT INPUT COLLECTION (called by orchestrator BEFORE browser session opens)
# ═══════════════════════════════════════════════════════════════════════════════

def gather_hl_inputs(pdf_path: str) -> dict | None:
    """
    Gather ALL user inputs BEFORE browser automation starts.

    Called by the orchestrator during the upfront input phase so that
    the browser session can run completely unattended.

    Workflow:
    1. Parse PDF to extract order details
    2. Auto-detect customer from database (with manual fallback)
    3. Prompt for contract code and description with smart defaults
    4. Return all data needed for automation

    Args:
        pdf_path: Path to H&L Partners PDF

    Returns:
        OrderInput-compatible dict, or None if user cancels
    """
    print("\n" + "=" * 70)
    print("H&L PARTNERS ORDER - UPFRONT INPUT COLLECTION")
    print("=" * 70)

    # ── Parse PDF ──
    print("\n[PARSE] Reading PDF...")
    try:
        estimates = parse_hl_pdf(pdf_path)
    except Exception as e:
        print(f"[PARSE] ✗ Failed: {e}")
        return None

    if not estimates:
        print("[PARSE] ✗ No estimates found in PDF")
        return None

    est = estimates[0]  # Use first estimate for display / defaults
    print(f"[PARSE] ✓ Estimate #: {est.estimate_number}")
    print(f"[PARSE] ✓ Client:     {est.client}")
    print(f"[PARSE] ✓ Flight:     {est.flight_start} - {est.flight_end}")
    print(f"[PARSE] ✓ Market:     {est.market}")
    print(f"[PARSE] ✓ Lines:      {len(est.lines)}")
    if len(estimates) > 1:
        print(f"[PARSE] ℹ {len(estimates)} estimates in this PDF — each becomes a separate contract")

    # ── Customer resolution ──
    customer_id, _ = _resolve_customer_id(pdf_path)

    # ── Smart code/description defaults ──
    suggested_code, suggested_desc = get_hl_defaults(pdf_path)

    print(f"\n[CONTRACT]")
    contract_code = input(f"  Code [{suggested_code}]: ").strip() or suggested_code
    description = input(f"  Description [{suggested_desc}]: ").strip() or suggested_desc

    # ── Separation intervals (use agency default, no prompt needed) ──
    separation = SEPARATION_INTERVALS
    print(f"\n[BILLING] ✓ Customer share indicating agency % / Agency")
    print(f"[INTERVALS] ✓ Customer={separation[0]}, Event={separation[1]}, Order={separation[2]}")

    print("\n" + "=" * 70)
    print("INPUT COLLECTION COMPLETE - Ready for automation")
    print("=" * 70)

    # Return as a simple namespace so _execute_order can use dot notation
    # (matches how OrderInput is accessed: user_input.order_code etc.)
    from types import SimpleNamespace
    return SimpleNamespace(
        order_code=contract_code,
        description=description,
        customer_id=customer_id,
        separation_intervals=separation,
    )
