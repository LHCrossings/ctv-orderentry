"""
H/L Buy Detail Report (BDR) Automation

Processes H/L Agency Buy Detail Report PDFs.
One page = one estimate = one Etere contract.

Entered into Etere exactly like standard H&L orders (hl_automation.py):
- Same agency DB records (order_type = 'hl')
- Same billing: Customer share indicating agency % / Agency
- Same separation: (25, 0, 0)
- Same contract code/description prompts with smart defaults
- Same customer template resolution
"""
from __future__ import annotations

import time

from selenium import webdriver

from browser_automation.etere_client import EtereClient
from browser_automation.parsers.hl_bdr_parser import BDROrder, BDRLine, parse_bdr_pdf

# ── Same constants as HL — BDR is the same agency ────────────────────────────
AGENCY_NAME = "hl"
SEPARATION_INTERVALS = (25, 0, 0)
CHARGE_TO = "Customer share indicating agency %"
INVOICE_HEADER = "Agency"
CUSTOMERS_DB_PATH = __import__("pathlib").Path("data") / "customers.db"


# ── Default code/description resolution for BDR ───────────────────────────────

def _get_bdr_defaults(orders: list[BDROrder]) -> tuple[str, str]:
    """
    Build smart contract code/description defaults for a BDR PDF.

    Uses the same customer-template system as HL orders.
    Falls back to generic defaults if no template is found.
    """
    from browser_automation.customer_defaults import ensure_template_columns, resolve_defaults

    if not orders:
        return ("HL Order", "H&L BDR Order")

    order = orders[0]
    try:
        ensure_template_columns(CUSTOMERS_DB_PATH)
        code, desc = resolve_defaults(
            customer_name=order.client,
            order_type=AGENCY_NAME,
            estimate_number=order.estimate_number,
            market=order.market,
            db_path=CUSTOMERS_DB_PATH,
        )
        if code and desc:
            return (code, desc)
    except Exception:
        pass

    return (
        f"HL {order.estimate_number}",
        f"H&L {order.market} Est {order.estimate_number}",
    )


def _save_customer_to_db(client_name: str, customer_id: int) -> None:
    """Save customer to DB (INSERT OR IGNORE). Non-fatal."""
    try:
        import sqlite3
        with sqlite3.connect(str(CUSTOMERS_DB_PATH)) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO customers (customer_name, customer_id, order_type) VALUES (?, ?, ?)",
                (client_name, customer_id, AGENCY_NAME),
            )
            if conn.total_changes > 0:
                print(f"[CUSTOMER DB] ✓ Saved: {client_name} (ID: {customer_id})")
            else:
                print(f"[CUSTOMER DB] ℹ Already known: {client_name}")
    except Exception as e:
        print(f"[CUSTOMER DB] ⚠ Could not save (non-fatal): {e}")


def _resolve_bdr_customer_id(client_name: str) -> int | None:
    """Look up customer ID from DB using order_type='hl'."""
    try:
        import sqlite3
        with sqlite3.connect(str(CUSTOMERS_DB_PATH)) as conn:
            row = conn.execute(
                "SELECT customer_id FROM customers WHERE customer_name = ? AND order_type = ?",
                (client_name, AGENCY_NAME),
            ).fetchone()
            if row:
                cust_id = int(row[0])
                print(f"[CUSTOMER DB] ✓ Found: {client_name} → ID {cust_id}")
                return cust_id

            # Fuzzy match
            rows = conn.execute(
                "SELECT customer_name, customer_id FROM customers WHERE order_type = ?",
                (AGENCY_NAME,),
            ).fetchall()
            for db_name, db_id in rows:
                if (db_name.lower() in client_name.lower()
                        or client_name.lower() in db_name.lower()):
                    cust_id = int(db_id)
                    print(f"[CUSTOMER DB] ✓ Fuzzy match: {client_name} ≈ {db_name} → ID {cust_id}")
                    return cust_id
    except Exception as e:
        print(f"[CUSTOMER DB] ⚠ Lookup failed: {e}")
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def gather_hl_bdr_inputs(pdf_path: str) -> dict | None:
    """
    Gather all user inputs before browser automation starts.
    Works like gather_hl_inputs — same prompts, same DB, same defaults.
    """
    print("\n" + "=" * 70)
    print("H&L BUY DETAIL REPORT - UPFRONT INPUT COLLECTION")
    print("=" * 70)

    print("\n[PARSE] Reading BDR PDF...")
    try:
        orders = parse_bdr_pdf(pdf_path)
    except Exception as e:
        print(f"[PARSE] ✗ Failed: {e}")
        return None

    if not orders:
        print("[PARSE] ✗ No estimates found in PDF")
        return None

    for o in orders:
        total_spots = sum(ln.total_spots for ln in o.lines)
        print(f"[PARSE] ✓ Est {o.estimate_number}: {o.description}")
        print(f"         Client: {o.client}  |  {o.market}  |  {o.flight_start}–{o.flight_end}  |  {total_spots} spots")

    if len(orders) > 1:
        print(f"[PARSE] ℹ {len(orders)} estimates — each becomes a separate contract")

    # ── Customer resolution ──
    client_name = orders[0].client
    customer_id = _resolve_bdr_customer_id(client_name)
    if customer_id:
        print(f"[CUSTOMER] ✓ {client_name} → ID {customer_id}")
    else:
        print(f"[CUSTOMER] Not in DB: {client_name}")
        cid_input = input("  Enter customer ID (or press Enter to search in Etere): ").strip()
        if cid_input:
            try:
                customer_id = int(cid_input)
            except ValueError:
                customer_id = None

    # Persist customer immediately so template lookup works
    if customer_id and client_name:
        _save_customer_to_db(client_name, customer_id)

    # ── Smart code/description defaults ──
    suggested_code, suggested_desc = _get_bdr_defaults(orders)

    print(f"\n[CONTRACT]")
    contract_code = input(f"  Code [{suggested_code}]: ").strip() or suggested_code
    description = input(f"  Description [{suggested_desc}]: ").strip() or suggested_desc

    # ── Separation (HL default, no prompt needed) ──
    separation = SEPARATION_INTERVALS
    print(f"\n[BILLING] ✓ Customer share indicating agency % / Agency")
    print(f"[INTERVALS] ✓ Customer={separation[0]}, Event={separation[1]}, Order={separation[2]}")

    print("\n" + "=" * 70)
    print("INPUT COLLECTION COMPLETE - Ready for automation")
    print("=" * 70)

    return {
        "order_code": contract_code,
        "description": description,
        "customer_id": customer_id,
        "separation": separation,
        "orders": orders,
    }


def process_hl_bdr_order(
    driver: webdriver.Chrome,
    pdf_path: str,
    user_input: dict,
) -> list[str]:
    """
    Process all estimates in a BDR PDF.
    Returns list of created contract numbers (empty on failure).
    """
    etere = EtereClient(driver)
    try:
        return _execute_order(etere, pdf_path, user_input)
    except Exception as e:
        print(f"[BDR] Fatal error: {e}")
        import traceback
        traceback.print_exc()
        return []


# ── Internal execution ────────────────────────────────────────────────────────

def _execute_order(
    etere: EtereClient,
    pdf_path: str,
    user_input: dict,
) -> list[str]:
    orders: list[BDROrder] = user_input.get("orders") or parse_bdr_pdf(pdf_path)
    customer_id: int | None = user_input.get("customer_id")
    order_code: str = user_input.get("order_code", "HL Order")
    description: str = user_input.get("description", "H&L BDR Order")
    separation: tuple = user_input.get("separation") or SEPARATION_INTERVALS

    if not orders:
        print("[BDR] No orders to process.")
        return []

    etere.set_master_market("NYC")
    created: list[str] = []

    for order in orders:
        print(f"\n[BDR] Processing Est {order.estimate_number}: {order.description}")

        # Build per-estimate code: substitute this estimate's number for the first one
        # e.g. "HL Toyota 13931 SF" → "HL Toyota 13932 SF" for estimate 13932
        if len(orders) > 1:
            est_order_code = order_code.replace(orders[0].estimate_number, order.estimate_number)
        else:
            est_order_code = order_code

        # Use estimate description for notes (like HL)
        est_notes = order.description if order.description else description
        est_description = description.replace(orders[0].estimate_number, order.estimate_number)

        contract_number = etere.create_contract_header(
            customer_id=customer_id,
            code=est_order_code,
            description=est_description,
            contract_start=order.flight_start,
            contract_end=order.flight_end,
            customer_order_ref=order.estimate_number,
            notes=est_notes,
            charge_to=CHARGE_TO,
            invoice_header=INVOICE_HEADER,
        )

        if not contract_number:
            print(f"[BDR] ✗ Failed to create contract for Est {order.estimate_number}")
            continue

        print(f"[BDR] ✓ Contract {contract_number} created")
        created.append(str(contract_number))

        if customer_id:
            _save_customer_to_db(order.client, customer_id)

        line_count = 0
        for line in order.lines:
            line_count += _add_bdr_line(etere, order, line, contract_number, separation)
            time.sleep(2)

        print(f"\n{'='*60}")
        print(f"✓ H&L BDR ESTIMATE {order.estimate_number} COMPLETE")
        print(f"  Contract: {contract_number}  |  Lines Added: {line_count}")
        print(f"{'='*60}")

    return created


def _add_bdr_line(
    etere: EtereClient,
    order: BDROrder,
    line: BDRLine,
    contract_number: int,
    separation: tuple[int, int, int],
) -> int:
    """
    Add all Etere contract lines for one BDR schedule row.
    Returns the number of lines added.
    """
    from browser_automation.parsers.hl_parser import analyze_weekly_distribution

    week_start = order.week_dates[0] if order.week_dates else order.flight_start

    date_ranges = analyze_weekly_distribution(
        weekly_spots=line.weekly_spots,
        flight_start=week_start,
        flight_end=order.flight_end,
    )

    if not date_ranges:
        print(f"  [BDR] ⚠ {line.days} {line.time} — no active weeks, skipping")
        return 0

    # Apply Sunday 6–7a rule
    days, _ = etere.check_sunday_6_7a_rule(line.days, line.time)
    time_from, time_to = EtereClient.parse_time_range(line.time)

    # Description: same format as HL — "{days} {time} {language}"
    description = f"{days} {line.time} {line.language.title()}"
    if line.category:
        description += f" {line.category.title()}"

    count = 0
    for dr in date_ranges:
        success = etere.add_contract_line(
            contract_number=contract_number,
            market=order.market,
            start_date=dr["start_date"],
            end_date=dr["end_date"],
            days=days,
            time_from=time_from,
            time_to=time_to,
            description=description,
            spot_code=EtereClient.SPOT_CODES["Paid Commercial"],
            duration_seconds=line.duration,
            total_spots=dr["spots"],
            spots_per_week=dr["spots_per_week"],
            rate=float(line.rate),
            separation_intervals=separation,
        )

        status = "✓" if success else "✗"
        print(
            f"  [BDR] {status} {description}"
            f"  {dr['start_date']}–{dr['end_date']}"
            f"  {dr['spots_per_week']}/wk={dr['spots']} total"
        )
        if success:
            count += 1

    return count
