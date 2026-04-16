"""
Polaris Media Group Automation.

Gathers user inputs and enters Polaris insertion orders into Etere for
Crossings TV (SFO, CVC, SEA, LAX, or other markets).

Master market: NYC (standard default — Polaris is not WorldLink/DAL).
Billing: agency (charge_to="Customer share indicating agency %", invoice_header="Agency").
"""

import math
import os
import sqlite3
from decimal import Decimal
from typing import Optional

from browser_automation.etere_client import EtereClient
from browser_automation.parsers.polaris_parser import PolarisOrder, PolarisLine, parse_polaris_xlsx

CUSTOMER_DB_PATH = os.path.join("data", "customers.db")

_MARKET_SHORT = {
    "CVC": "CV",
    "SFO": "SF",
    "SEA": "SEA",
    "LAX": "LA",
    "HOU": "HOU",
    "CMP": "CMP",
    "WDC": "WDC",
    "NYC": "NYC",
}


# ─────────────────────────────────────────────────────────────────────────────
# Customer DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_customer(name: str, db_path: str = CUSTOMER_DB_PATH) -> Optional[dict]:
    """Case-insensitive lookup in customers.db; falls back to partial match."""
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
    code_name: str,
    description_name: str,
    include_market: bool,
    db_path: str = CUSTOMER_DB_PATH,
) -> None:
    """Insert or replace Polaris customer record."""
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            """INSERT OR REPLACE INTO customers
               (customer_id, customer_name, order_type,
                code_name, description_name, include_market_in_code,
                billing_type,
                separation_customer, separation_event, separation_order)
               VALUES (?, ?, 'polaris', ?, ?, ?, 'agency', 15, 0, 0)""",
            (customer_id, customer_name, code_name, description_name, int(include_market)),
        )
        conn.commit()
        conn.close()
        print(f"[CUSTOMER DB] ✓ Saved: {customer_name} → ID {customer_id}")
    except Exception as exc:
        print(f"[CUSTOMER DB] ✗ Save failed: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Input gathering
# ─────────────────────────────────────────────────────────────────────────────

def gather_polaris_inputs(xlsx_path: str) -> Optional[dict]:
    """
    Parse the Polaris xlsx and collect all user inputs before the browser opens.

    Args:
        xlsx_path: Path to the .xlsx file.

    Returns:
        Dict with keys: order, customer_id, contracts (per-market code/description),
        separation.  Returns None if the user cancels.
    """
    order = parse_polaris_xlsx(xlsx_path)

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("POLARIS MEDIA GROUP")
    print(f"  Advertiser : {order.advertiser}")
    print(f"  Prepared by: {order.prepared_by}")
    print(f"  Flight     : {order.flight_start} – {order.flight_end}")
    print(f"  Budget     : ${order.gross_budget:,}")
    print(f"  Markets    : {', '.join(order.markets)}")
    print(f"  Lines      : {len(order.lines)} ({order.total_spots} spots)")
    print()
    for ln in order.lines:
        tf, tt = ln.get_time_from_to()
        rate_label = "BONUS" if ln.is_bonus else f"${ln.rate}"
        print(f"    [{ln.market}]  {ln.days:<8s}  {ln.time_str:<15s}  "
              f"{ln.program:<28s}  {rate_label}  ×{ln.total_spots}")
    print(f"{'─'*60}")

    # ── Flight date confirmation (political orders often arrive day-of) ────────
    print(f"\n  Sheet start date: {order.flight_start}")
    start_input = input(
        f"  Use this start date? [Enter=yes / type override, e.g. 4/17/2026]: "
    ).strip()
    actual_start = start_input if start_input else order.flight_start

    print(f"\n  Sheet end date: {order.flight_end}")
    end_input = input(
        f"  Use this end date? [Enter=yes / type override, e.g. 4/20/2026]: "
    ).strip()
    actual_end = end_input if end_input else order.flight_end

    if actual_start != order.flight_start or actual_end != order.flight_end:
        print(f"  ✓ Using adjusted flight: {actual_start} – {actual_end}")

    # ── Customer lookup ───────────────────────────────────────────────────────
    existing = _lookup_customer(order.advertiser)
    customer_id: Optional[str] = None
    code_name: str = ""
    description_name: str = ""
    include_market: bool = len(order.markets) > 1

    if existing:
        stored_id   = existing.get("customer_id", "")
        stored_code = existing.get("code_name", "") or ""
        stored_desc = existing.get("description_name", "") or ""
        stored_mkt  = bool(existing.get("include_market_in_code", 0))
        print(f"\n[CUSTOMER DB] Found: {existing['customer_name']}")
        print(f"  Customer ID : {stored_id}")
        print(f"  Code prefix : {stored_code}")
        resp = input(
            f"  Use stored customer ID '{stored_id}'? [Enter=yes / type new ID]: "
        ).strip()
        customer_id      = resp if resp else stored_id
        code_name        = stored_code
        description_name = stored_desc
        include_market   = stored_mkt
    else:
        print(f"\n[CUSTOMER DB] '{order.advertiser}' not found in database.")
        customer_id = input("  Enter Etere customer ID: ").strip()
        if not customer_id:
            print("[CANCELLED] No customer ID entered.")
            return None
        code_name = input(
            "  Enter contract code prefix (e.g. 'POLARIS'): "
        ).strip()
        description_name = input(
            "  Enter description prefix (e.g. 'Polaris'): "
        ).strip()
        inc = input(
            "  Append market to contract code? (y/N): "
        ).strip().lower()
        include_market = inc in ("y", "yes")
        _upsert_customer(
            customer_id, order.advertiser,
            code_name, description_name, include_market,
        )

    # ── Per-market contract code / description ────────────────────────────────
    contracts: dict[str, dict] = {}
    for market in order.markets:
        suffix = _MARKET_SHORT.get(market, market) if include_market else ""
        default_code = f"{code_name}{suffix}" if code_name else market
        default_desc = (
            f"{description_name} {order.advertiser}"
            if description_name else order.advertiser
        )

        print(f"\n  Market: {market}")
        code_in = input(f"  Contract code [{default_code}]: ").strip()
        desc_in = input(f"  Contract description [{default_desc}]: ").strip()
        contracts[market] = {
            "code":        code_in  if code_in  else default_code,
            "description": desc_in  if desc_in  else default_desc,
        }

    # ── Separation ────────────────────────────────────────────────────────────
    if existing:
        sep_c = int(existing.get("separation_customer", 15) or 15)
        sep_e = int(existing.get("separation_event",    0)  or 0)
        sep_o = int(existing.get("separation_order",    0)  or 0)
        separation = (sep_c, sep_e, sep_o)
    else:
        separation = (15, 0, 0)

    return {
        "order":        order,
        "customer_id":  customer_id,
        "contracts":    contracts,
        "separation":   separation,
        "actual_start": actual_start,
        "actual_end":   actual_end,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Order processing
# ─────────────────────────────────────────────────────────────────────────────

def process_polaris_order(driver, xlsx_path: str, user_input: dict) -> Optional[str]:
    """
    Enter a Polaris order into Etere.  Creates one contract per market.

    Args:
        driver:     Selenium WebDriver from the shared EtereSession.
        xlsx_path:  Path to the .xlsx file (re-parsed from disk for safety).
        user_input: Dict returned by gather_polaris_inputs.

    Returns:
        Contract number of the last created contract, or None on failure.
    """
    etere = EtereClient(driver)

    # Re-parse from disk for safety (same pattern as HL / DART)
    order: PolarisOrder = parse_polaris_xlsx(xlsx_path)

    customer_id  = user_input["customer_id"]
    contracts    = user_input["contracts"]
    separation   = user_input.get("separation", (15, 0, 0))
    flight_start = user_input.get("actual_start", order.flight_start)
    flight_end   = user_input.get("actual_end",   order.flight_end)

    last_contract: Optional[str] = None

    for market in order.markets:
        market_lines = order.lines_for_market(market)
        contract_info = contracts[market]
        code        = contract_info["code"]
        description = contract_info["description"]

        print(f"\n[POLARIS] Creating contract for {market}: {code}")

        contract_number = etere.create_contract_header(
            customer_id=int(customer_id),
            code=code,
            description=description,
            contract_start=flight_start,
            contract_end=flight_end,
            charge_to="Customer share indicating agency %",
            invoice_header="Agency",
        )

        if not contract_number:
            print(f"[POLARIS] ✗ Failed to create contract header for {market}")
            return None

        print(f"[POLARIS] ✓ Contract created: {contract_number}")

        # ── Add contract lines ─────────────────────────────────────────────
        for idx, ln in enumerate(market_lines, start=1):
            time_from, time_to = ln.get_time_from_to()
            spot_code = EtereClient.SPOT_CODES["BNS"] if ln.is_bonus else EtereClient.SPOT_CODES["Paid Commercial"]
            rate = float(ln.rate)
            label = "BNS" if ln.is_bonus else "PAY"

            # Apply Sunday 6-7am rule
            days, _ = etere.check_sunday_6_7a_rule(ln.days, ln.time_str)

            active_days = EtereClient._count_active_days(days)
            max_daily = math.ceil(ln.total_spots / active_days) if active_days > 0 else 1

            ok = etere.add_contract_line(
                contract_number=contract_number,
                market=market,
                start_date=flight_start,
                end_date=flight_end,
                days=days,
                time_from=time_from,
                time_to=time_to,
                description=ln.get_description(),
                spot_code=spot_code,
                duration_seconds=30,
                total_spots=ln.total_spots,
                spots_per_week=ln.total_spots,  # single-flight: spots_per_week == total_spots
                max_daily_run=max_daily,
                rate=rate,
                separation_intervals=separation,
            )

            status = "✓" if ok else "✗"
            tf_label = "BONUS" if ln.is_bonus else f"${ln.rate}"
            print(f"  [{status}] Line {idx} [{label}]  {days}  {ln.time_str}  "
                  f"{ln.program}  {tf_label}  ×{ln.total_spots}  "
                  f"max/day={max_daily}")

            if not ok:
                print(f"[POLARIS] ✗ Line {idx} failed — aborting")
                return None

        print(f"\n[POLARIS] ✓ {market}: all lines entered. Contract: {contract_number}")
        last_contract = contract_number

    return last_contract
