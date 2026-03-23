"""
H/L Buy Detail Report (BDR) Automation

Processes H/L Agency Buy Detail Report PDFs.
One page = one estimate = one Etere contract.
"""
from __future__ import annotations

import sqlite3

from selenium import webdriver

from browser_automation.etere_client import EtereClient
from browser_automation.parsers.hl_bdr_parser import BDROrder, BDRLine, parse_bdr_pdf
from browser_automation.parsers.hl_parser import analyze_weekly_distribution

AGENCY_NAME = "hl_bdr"
CHARGE_TO = "Customer share indicating agency %"
INVOICE_HEADER = "Agency"


# ── Customer helpers ──────────────────────────────────────────────────────────

def _get_customer_id(client_name: str) -> int | None:
    """Return Etere customer_id for the given client name, or None if unknown."""
    try:
        with sqlite3.connect("data/customers.db") as conn:
            row = conn.execute(
                "SELECT customer_id FROM customers WHERE customer_name = ? AND order_type = ?",
                (client_name, AGENCY_NAME),
            ).fetchone()
            if row:
                return int(row[0])
    except Exception:
        pass
    return None


def _save_customer(client_name: str, customer_id: int) -> None:
    try:
        with sqlite3.connect("data/customers.db") as conn:
            conn.execute(
                "INSERT OR IGNORE INTO customers (customer_name, customer_id, order_type) VALUES (?, ?, ?)",
                (client_name, str(customer_id), AGENCY_NAME),
            )
    except Exception as e:
        print(f"[BDR] Warning: could not save customer to DB: {e}")


# ── Public API ────────────────────────────────────────────────────────────────

def gather_hl_bdr_inputs(pdf_path: str) -> dict | None:
    """
    Gather all user inputs before browser automation starts.
    Called by orchestrator during upfront input phase.
    Returns OrderInput-compatible dict, or None if user cancels.
    """
    orders = parse_bdr_pdf(pdf_path)
    if not orders:
        print("[BDR] No valid orders found in PDF.")
        return None

    print(f"\n[BDR] Found {len(orders)} estimate(s):")
    for o in orders:
        total_spots = sum(ln.total_spots for ln in o.lines)
        print(
            f"  Est {o.estimate_number}: {o.description}"
            f" | {o.market} | {o.flight_start}–{o.flight_end}"
            f" | {total_spots} spots | sep={o.separation_minutes}min"
        )

    # Customer lookup
    client_name = orders[0].client
    customer_id = _get_customer_id(client_name)
    if customer_id:
        print(f"[BDR] Customer '{client_name}' → ID {customer_id}")
    else:
        print(f"[BDR] Customer '{client_name}' not in DB — manual search required.")

    return {
        "orders": orders,
        "customer_id": customer_id,
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

    if not orders:
        print("[BDR] No orders to process.")
        return []

    etere.set_master_market("NYC")
    created: list[str] = []

    for order in orders:
        print(f"\n[BDR] Processing Est {order.estimate_number}: {order.description}")

        contract_number = etere.create_contract_header(
            customer_id=customer_id,
            code=order.estimate_number,
            description=order.description,
            contract_start=order.flight_start,
            contract_end=order.flight_end,
            customer_order_ref=order.estimate_number,
            notes=f"Buyer: {order.buyer}" if order.buyer else "",
            charge_to=CHARGE_TO,
            invoice_header=INVOICE_HEADER,
        )

        if not contract_number:
            print(f"[BDR] ✗ Failed to create contract for Est {order.estimate_number}")
            continue

        print(f"[BDR] ✓ Contract {contract_number} created")
        created.append(str(contract_number))

        # Save customer now that we have a live Etere session
        if customer_id:
            _save_customer(order.client, customer_id)

        separation = (order.separation_minutes, 0, 0)

        for line in order.lines:
            _add_bdr_line(etere, order, line, contract_number, separation)

    return created


def _add_bdr_line(
    etere: EtereClient,
    order: BDROrder,
    line: BDRLine,
    contract_number: int,
    separation: tuple[int, int, int],
) -> None:
    """Add all Etere contract lines for one BDR schedule row."""
    # Use the first week_date as the flight start for week distribution
    week_start = order.week_dates[0] if order.week_dates else order.flight_start

    date_ranges = analyze_weekly_distribution(
        weekly_spots=line.weekly_spots,
        flight_start=week_start,
        flight_end=order.flight_end,
    )

    if not date_ranges:
        print(f"  [BDR] ⚠ {line.days} {line.time} — no active weeks, skipping")
        return

    # Apply Sunday 6–7a rule
    days, _ = etere.check_sunday_6_7a_rule(line.days, line.time)
    time_from, time_to = EtereClient.parse_time_range(line.time)

    description = f"{days} {line.language}"
    if line.category:
        description += f" {line.category}"

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
            f"  [BDR] {status} {days} {time_from}-{time_to}"
            f" {dr['start_date']}-{dr['end_date']}"
            f" {dr['spots_per_week']}/wk={dr['spots']}total"
        )
