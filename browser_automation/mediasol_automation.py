"""
Media Solutions / Pulsar Advertising c/o Mediasol Automation

Uses EtereClient for ALL Etere interactions.
Parser handles PDF extraction; this file handles business logic + orchestration.

Business Rules:
- Markets: LAX (Los Angeles) primary; SFO and CVC possible
- Separation intervals: Customer=25, Order=0, Event=0
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
from pathlib import Path
from typing import Optional

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

# Separation intervals: (customer, order, event) in minutes
# PDF says "30 min between spots" — house rule: enter 25 in Etere
SEPARATION_INTERVALS = (25, 0, 0)

# Billing
CHARGE_TO = "Customer share indicating agency %"
INVOICE_HEADER = "Agency"

# Spot codes
SPOT_CODE_PAID = 2
SPOT_CODE_BONUS = 10

# Customer database path (relative to project root)
from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMERS_DB_PATH


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


def _create_mediasol_contracts_direct(pdf_path: str, user_input: dict) -> bool:
    """Enter Mediasol order(s) directly via DB stored procedures (no browser)."""
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id              = user_input.get('customer_id')
    order_code               = user_input['order_code']
    description              = user_input['description']
    separation               = user_input.get('separation') or SEPARATION_INTERVALS
    existing_contract_number = user_input.get('existing_contract_number')
    gross_up_factor          = user_input.get('gross_up_factor', 1.0)

    estimates = parse_mediasol_pdf(pdf_path)
    if not estimates:
        print("[MEDIASOL DIRECT] ✗ No estimates found")
        return False

    if gross_up_factor != 1.0:
        for est in estimates:
            for line in est.lines:
                if not line.is_bonus():
                    line.rate = round(line.rate * gross_up_factor, 2)

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")
        all_success = True

        for est_idx, estimate in enumerate(estimates, 1):
            est_order_code = (
                f"{order_code} Est {estimate.estimate_number}" if len(estimates) > 1 else order_code
            )
            est_notes   = estimate.description if estimate.description else description
            market_code = _normalize_mediasol_market(estimate.market)

            if existing_contract_number:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT ID_CONTRATTITESTATA FROM CONTRATTITESTATA WHERE NUMERO = %s",
                    (existing_contract_number,),
                )
                row = cursor.fetchone()
                if not row:
                    print(f"[MEDIASOL DIRECT] ✗ Contract '{existing_contract_number}' not found in Etere")
                    return False
                contract_id = row[0]
                row_status  = 2
                print(f"[MEDIASOL DIRECT] Revision — using contract ID={contract_id} (row_status=2)")
            else:
                if customer_id is None:
                    print("[MEDIASOL DIRECT] ✗ No customer_id")
                    return False
                contract_id = client.create_contract_header(
                    code=est_order_code,
                    description=description,
                    customer_id=int(customer_id),
                    contract_date=_parse_date(estimate.flight_start),
                    contract_end_date=_parse_date(estimate.flight_end),
                    contract_type=1,
                    billing_type="agency",
                    note=est_notes,
                    customer_order_ref=estimate.estimate_number,
                    allow_rename=True,
                )
                if not contract_id:
                    print(f"[MEDIASOL DIRECT] ✗ Failed to create contract for estimate {estimate.estimate_number}")
                    all_success = False
                    continue
                row_status = 0
                print(f"[MEDIASOL DIRECT] ✓ Contract ID={contract_id}")

            line_count = 0
            for line in estimate.lines:
                etere_lines = _build_etere_lines(line, estimate, market_code)
                for el in etere_lines:
                    line_count += 1
                    print(f"  [LINE {line_count}] {el['description']}: "
                          f"{el['start_date']}–{el['end_date']} "
                          f"({el['spots_per_week']}/wk={el['total_spots']})")
                    client.add_contract_line(
                        contract_id=contract_id,
                        market=market_code,
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
                        row_status=row_status,
                    )

        conn.commit()
        conn.close()
        return all_success

    except Exception as exc:
        print(f"[MEDIASOL DIRECT] ✗ {exc}")
        import traceback; traceback.print_exc()
        if conn:
            try: conn.rollback(); conn.close()
            except: pass
        return False
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
    pdf_path: str,
    user_input: dict,
) -> bool:
    """
    Process a Media Solutions order end-to-end via direct DB entry.

    Args:
        pdf_path: Path to the Mediasol PDF
        user_input: Dict from gather_mediasol_inputs

    Returns:
        True if all estimates processed successfully
    """
    return _create_mediasol_contracts_direct(pdf_path, user_input)


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
    print(f"[INTERVALS]  ✓ Customer={separation[0]}, Order={separation[1]}, Event={separation[2]}")
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
