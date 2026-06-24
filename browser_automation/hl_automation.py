"""
H&L Partners Automation - Direct DB Entry for H&L Partners Agency Orders

Uses EtereDirectClient for ALL Etere interactions — no browser required.
Parser handles PDF extraction; this file handles business logic + orchestration.

H&L Partners Business Rules:
- Markets: SFO (San Francisco) or CVC (Sacramento) ONLY
- Multiple clients possible → customer DB lookup with manual fallback
- Separation intervals: Customer=25, Order=0, Event=0
- Billing: agency (Customer share indicating agency %)
- Estimate number → Customer Order Ref; estimate description → contract Notes
- Language block filtering (Mandarin, Korean, Hindi, etc.)
- Bonus lines (rate=0) → booking code BNS (10)
- Weekly distribution splitting on gaps and differing spot counts
- Sunday 6-7a paid programming rule applies
- Strata IO format (same family as opAD/TCAA)
"""

import sqlite3
from datetime import datetime
from typing import Optional

from parsers.hl_parser import (
    HLEstimate,
    HLLine,
    analyze_weekly_distribution,
    convert_hl_days_to_etere,
    extract_language_from_program,
    format_time_for_description,
    parse_hl_pdf,
)

from browser_automation.etere_direct_client import (
    AGENCY_IDS,
    MEDIA_CENTER_IDS,
    EtereDirectClient,
    connect,
)

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

AGENCY_NAME = "hl"

# Separation intervals: (customer, order, event) in minutes
SEPARATION_INTERVALS = (25, 0, 0)

# Spot codes
SPOT_CODE_PAID = 2       # Paid Commercial
SPOT_CODE_BONUS = 10     # BNS / Bonus Spot

from browser_automation.added_value import add_av_line, prompt_add_av, widest_window
from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMERS_DB_PATH

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
    market_upper = market_text.upper().strip()
    return HL_MARKET_MAPPING.get(market_upper, "SFO")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def process_hl_order(
    driver=None,                          # unused — kept for interface compatibility
    pdf_path: str = "",
    user_input: dict | None = None,       # legacy kwarg name
    pre_gathered_inputs: dict | None = None,
) -> bool:
    """
    Process an H&L Partners order end-to-end via direct DB entry.

    Called by order_processing_service.py. Browser session not required.

    Args:
        driver:               Unused — kept for interface compatibility
        pdf_path:             Path to the H&L Partners PDF
        user_input:           Legacy kwarg — treated as pre_gathered_inputs
        pre_gathered_inputs:  Dict from gather_hl_inputs()

    Returns:
        True if ALL estimates processed successfully, False otherwise
    """
    inputs = pre_gathered_inputs or user_input
    if not inputs:
        print("[H&L] No inputs provided")
        return False

    try:
        return _execute_order(pdf_path, inputs)
    except Exception as e:
        print(f"[H&L] ✗ Order failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def _execute_order(pdf_path: str, user_input: dict) -> bool:
    """Execute the full order workflow via direct DB."""

    order_code              = user_input['order_code']
    description             = user_input['description']
    customer_id             = user_input.get('customer_id')
    separation              = user_input.get('separation') or SEPARATION_INTERVALS
    existing_contract_id    = user_input.get('existing_contract_number')
    gross_up_factor         = user_input.get('gross_up_factor', 1.0)
    add_av                  = user_input.get('add_av', False)

    if customer_id is None:
        customer_id, _ = _resolve_customer_id(pdf_path)

    print(f"\n{'='*60}")
    print(f"Processing H&L Partners Order: {pdf_path}")
    print(f"{'='*60}\n")

    estimates = parse_hl_pdf(pdf_path)
    if not estimates:
        print("[H&L] ✗ No estimates found in PDF")
        return False

    print(f"[PARSE] ✓ Found {len(estimates)} estimate(s)")

    # Re-apply gross-up from factor (PDF re-parsed from disk, in-memory edits gone)
    if gross_up_factor != 1.0:
        for est in estimates:
            for line in est.lines:
                if not line.is_bonus():
                    line.rate = round(line.rate * gross_up_factor, 2)
        print(f"[PARSE] ✓ Gross-up applied (factor {gross_up_factor:.6f})")

    conn = connect()
    try:
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=True)
        client.set_master_market("NYC")

        all_success = True

        for est_idx, estimate in enumerate(estimates, 1):
            print(f"\n[ESTIMATE {est_idx}/{len(estimates)}]")
            print(f"  Estimate #: {estimate.estimate_number}")
            print(f"  Description: {estimate.description}")
            print(f"  Client:      {estimate.client}")
            print(f"  Flight:      {estimate.flight_start} - {estimate.flight_end}")
            print(f"  Market:      {estimate.market}")
            print(f"  Lines:       {len(estimate.lines)}")

            est_order_code = (
                f"{order_code} Est {estimate.estimate_number}"
                if len(estimates) > 1 else order_code
            )
            est_notes = estimate.description if estimate.description else description
            market_code = _normalize_hl_market(estimate.market)

            flight_start = datetime.strptime(estimate.flight_start, "%m/%d/%Y").date()
            flight_end   = datetime.strptime(estimate.flight_end,   "%m/%d/%Y").date()

            # ── Contract header ──────────────────────────────────────────────
            if existing_contract_id:
                contract_id = int(existing_contract_id)
                client._contract_id = contract_id
                print(f"\n{'!'*60}")
                print(f"  REVISION MODE — reusing contract ID {contract_id}")
                print(f"  Please delete ALL existing lines from contract {contract_id} in Etere now.")
                input("  Press Enter when lines are cleared and you are ready to continue...")
                print(f"{'!'*60}")
            else:
                contract_id = client.create_contract_header(
                    customer_id=int(customer_id),
                    code=est_order_code,
                    description=description,
                    contract_date=flight_start,
                    contract_end_date=flight_end,
                    customer_order_ref=estimate.estimate_number,
                    note=est_notes,
                    billing_type="agency",
                    agency_id=AGENCY_IDS["HL"],
                    media_center_id=MEDIA_CENTER_IDS["HL"],
                    allow_rename=True,
                )

                if not contract_id:
                    print(f"[H&L] ✗ Failed to create contract header for estimate {estimate.estimate_number}")
                    all_success = False
                    continue

                print(f"[H&L] ✓ Contract created: #{contract_id}")

            if customer_id:
                _save_customer_to_db(estimate.client, customer_id)

            # ── Contract lines ───────────────────────────────────────────────
            line_count = 0
            for line in estimate.lines:
                etere_lines = _build_etere_lines(line, estimate, market_code)
                duration_str = f"00:00:{line.duration:02d}:00"

                for etere_line in etere_lines:
                    line_count += 1
                    print(f"\n  [LINE {line_count}] {etere_line['description']}")
                    print(f"    {etere_line['start_date']} - {etere_line['end_date']}")
                    print(f"    {etere_line['spots_per_week']}/wk, rate=${etere_line['rate']}")

                    date_from  = datetime.strptime(etere_line["start_date"], "%m/%d/%Y").date()
                    date_to    = datetime.strptime(etere_line["end_date"],   "%m/%d/%Y").date()
                    time_range = f"{etere_line['time_from']}-{etere_line['time_to']}"
                    is_bonus   = etere_line["spot_code"] == SPOT_CODE_BONUS

                    line_id = client.add_contract_line(
                        contract_id=contract_id,
                        market=market_code,
                        days=etere_line["days"],
                        time_range=time_range,
                        description=etere_line["description"],
                        rate=float(etere_line["rate"]),
                        total_spots=etere_line["total_spots"],
                        spots_per_week=etere_line["spots_per_week"],
                        date_from=date_from,
                        date_to=date_to,
                        duration=duration_str,
                        is_bonus=is_bonus,
                        booking_code=etere_line["spot_code"],
                        separation_intervals=separation,
                    )

                    if line_id <= 0:
                        print(f"    ✗ Failed to add line {line_count}")
                        all_success = False
                        break

                    print(f"    → line_id = {line_id}")

                if not all_success:
                    break

            # Optional Added Value line (one spot/day across the flight)
            if all_success and add_av and estimate.lines:
                window = widest_window([ln.time for ln in estimate.lines])
                duration_str = f"00:00:{estimate.lines[0].duration:02d}:00"
                av_id = add_av_line(
                    client,
                    contract_id=contract_id,
                    market=market_code,
                    date_from=flight_start,
                    date_to=flight_end,
                    duration=duration_str,
                    separation=separation,
                    languages=[extract_language_from_program(ln.program) for ln in estimate.lines],
                    fallback_time=window,
                )
                if av_id > 0:
                    line_count += 1
                    print(
                        f"\n  [ADDED VALUE] M-Su {window}"
                        f"  {estimate.flight_start}–{estimate.flight_end}"
                        f"  1/day={(flight_end - flight_start).days + 1} total → line_id={av_id}"
                    )

            print(f"\n{'='*60}")
            print(f"✓ H&L ESTIMATE {estimate.estimate_number} COMPLETE")
            print(f"  Contract: #{contract_id}")
            print(f"  Lines Added: {line_count}")
            print(f"{'='*60}")

        return all_success

    finally:
        conn.close()


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
        List of dicts ready for client.add_contract_line()
    """
    from browser_automation.etere_client import EtereClient

    ranges = analyze_weekly_distribution(
        line.weekly_spots,
        estimate.flight_start,
        estimate.flight_end,
    )

    etere_days = convert_hl_days_to_etere(line.days)

    time_fmt = format_time_for_description(line.time)
    language = extract_language_from_program(line.program)
    lang_suffix = f" {language}" if language and language != "Unknown" else ""

    is_asian_rotator = "asian rotat" in line.program.lower()

    line_prefix = f"(Line {line.line_number}) " if line.line_number is not None else ""

    if is_asian_rotator:
        spot_code = SPOT_CODE_BONUS
        description = f"{line_prefix}{etere_days} {time_fmt} BNS ROS"
    else:
        spot_code = SPOT_CODE_BONUS if line.is_bonus() else SPOT_CODE_PAID
        description = f"{line_prefix}{etere_days} {time_fmt}{lang_suffix}"

    time_from, time_to = EtereClient.parse_time_range(line.time)
    adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(etere_days, line.time)

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
    """
    try:
        estimates = parse_hl_pdf(pdf_path)
        client_name = estimates[0].client if estimates else None
    except Exception:
        client_name = None

    if not client_name:
        return _manual_customer_entry(), None

    try:
        db_path = CUSTOMERS_DB_PATH
        if db_path.exists():
            with sqlite3.connect(str(db_path)) as conn:
                cursor = conn.execute(
                    "SELECT customer_id FROM customers WHERE customer_name = ? AND order_type = ?",
                    (client_name, AGENCY_NAME),
                )
                row = cursor.fetchone()
                if row:
                    cust_id = int(row[0])
                    print(f"[CUSTOMER DB] ✓ Found: {client_name} → ID {cust_id}")
                    return cust_id, None

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
    customer_input = input("Enter customer ID (or press Enter to skip): ").strip()
    if customer_input:
        try:
            return int(customer_input)
        except ValueError:
            print("[CUSTOMER] Invalid ID - skipping")
            return None
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOMER DATABASE (Self-Learning)
# ═══════════════════════════════════════════════════════════════════════════════

def _save_customer_to_db(client_name: str, customer_id: int) -> None:
    """Save customer to database. Uses INSERT OR IGNORE so safe to call every time."""
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


def _seed_known_template(client_name: str) -> None:
    """If client_name matches a known H&L template, seed it into the DB."""
    try:
        import sqlite3 as _sqlite3

        from browser_automation.customer_defaults import ensure_template_columns
        from seed_customer_templates import KNOWN_TEMPLATES

        ensure_template_columns(CUSTOMERS_DB_PATH)
        name_lower = client_name.lower()

        with _sqlite3.connect(str(CUSTOMERS_DB_PATH)) as conn:
            for tmpl_name, tmpl_type, code_tmpl, desc_tmpl in KNOWN_TEMPLATES:
                if tmpl_type != AGENCY_NAME:
                    continue
                if tmpl_name.lower() not in name_lower and name_lower not in tmpl_name.lower():
                    continue
                conn.execute(
                    """UPDATE customers
                       SET default_code_template = ?, default_desc_template = ?
                       WHERE customer_name = ? AND order_type = ?
                         AND default_code_template IS NULL""",
                    (code_tmpl, desc_tmpl, client_name, AGENCY_NAME),
                )
                if conn.total_changes > 0:
                    print(f"[CUSTOMER DB] ✓ Templates set: code='{code_tmpl}' desc='{desc_tmpl}'")
                break
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# DEFAULT VALUES (called by order_processing_service.get_default_order_values)
# ═══════════════════════════════════════════════════════════════════════════════

def get_hl_defaults(pdf_path: str) -> tuple[str, str]:
    """
    Get default contract code and description for an H&L order.

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

        return (f"HL {est.estimate_number}", f"H&L {market_code} Est {est.estimate_number}")

    except Exception as e:
        print(f"[H&L] ⚠ Could not parse defaults: {e}")
    return ("HL Order", "H&L Partners Order")


# ═══════════════════════════════════════════════════════════════════════════════
# UPFRONT INPUT COLLECTION (called by orchestrator BEFORE any DB session opens)
# ═══════════════════════════════════════════════════════════════════════════════

def gather_hl_inputs(pdf_path: str) -> dict | None:
    """
    Gather ALL user inputs BEFORE DB automation starts.

    Args:
        pdf_path: Path to H&L Partners PDF

    Returns:
        OrderInput-compatible dict, or None if user cancels
    """
    print("\n" + "=" * 70)
    print("H&L PARTNERS ORDER - UPFRONT INPUT COLLECTION")
    print("=" * 70)

    print("\n[PARSE] Reading PDF...")
    try:
        estimates = parse_hl_pdf(pdf_path)
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
        print(f"[PARSE] ℹ {len(estimates)} estimates in this PDF — each becomes a separate contract")

    customer_id, _ = _resolve_customer_id(pdf_path)

    if customer_id and est.client:
        _save_customer_to_db(est.client, customer_id)
        _seed_known_template(est.client)

    suggested_code, suggested_desc = get_hl_defaults(pdf_path)

    print("\n[CONTRACT]")
    contract_code = input(f"  Code [{suggested_code}]: ").strip() or suggested_code
    description = input(f"  Description [{suggested_desc}]: ").strip() or suggested_desc

    # ── Revision detection ──
    is_revision = any(
        'revis' in e.description.lower() or 'revis' in e.client.lower()
        for e in estimates
    )
    existing_contract_number = None
    if is_revision:
        print("\n[REVISION] ⚠ Detected 'revised' in order description.")
    revision_input = input(
        f"  Is this a revision of an existing Etere contract? [{'Y/n' if is_revision else 'y/N'}]: "
    ).strip().lower()
    is_revision = revision_input != 'n' if is_revision else revision_input == 'y'
    if is_revision:
        existing_contract_number = input(
            "  Enter the existing Etere contract ID (numeric ID from contract URL): "
        ).strip()
        if not existing_contract_number:
            print("  No contract ID entered — will create a new contract instead.")
            existing_contract_number = None

    # ── Net rate check ──
    gross_up_factor = 1.0
    if any(e.rates_are_net for e in estimates):
        paid_rates = [l.rate for e in estimates for l in e.lines if not l.is_bonus()]
        print(f"\n{'!'*70}")
        print("  WARNING: This order's rates are labeled NET in the PDF.")
        sample = ", ".join(f"${r:.2f}" for r in paid_rates[:5])
        print(f"  Sample rates: {sample}")
        gross_up = input("  Gross these rates up for agency commission? [y/N]: ").strip().lower()
        if gross_up == 'y':
            pct_str = input("  Agency commission % (e.g. 15): ").strip()
            try:
                pct = float(pct_str)
                gross_up_factor = 1.0 / (1.0 - pct / 100.0)
                print(f"  Gross-up factor: {gross_up_factor:.6f}  (net ÷ {1 - pct/100:.2f})")
                for e in estimates:
                    for l in e.lines:
                        if not l.is_bonus():
                            l.rate = round(l.rate * gross_up_factor, 2)
                grossed_sample = ", ".join(
                    f"${l.rate:.2f}" for e in estimates for l in e.lines if not l.is_bonus()
                )[:80]
                print(f"  Grossed rates (first several): {grossed_sample}")
            except ValueError:
                print("  Invalid percentage — rates left as-is.")
        print('!'*70)

    separation = SEPARATION_INTERVALS
    print("\n[BILLING] ✓ Customer share indicating agency % / Agency")
    print(f"[INTERVALS] ✓ Customer={separation[0]}, Order={separation[1]}, Event={separation[2]}")

    # Offer Added Value when the order carries no bonus (rate == 0) lines
    has_bonus = any(ln.is_bonus() for e in estimates for ln in e.lines)
    add_av = prompt_add_av(has_bonus)

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
        'add_av': add_av,
    }
