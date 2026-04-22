"""
Wallrich Order Automation
Browser automation for Wallrich agency insertion orders.

Format: Strata IO (same family as H&L Partners / opAD).
Market: Sacramento (CVC) — KBTV station.
Separation: PDF value → (PDF_val - 5, 0, 0) per lessons (30 min → 25 min Etere).
"""

import json
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from selenium import webdriver

from browser_automation.etere_client import EtereClient
from browser_automation.language_utils import extract_language_from_program
from parsers.wallrich_parser import (
    WallrichEstimate,
    WallrichLine,
    consolidate_wallrich_weeks,
    parse_wallrich_pdf,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AGENCY_NAME = "wallrich"

CHARGE_TO      = "Customer share indicating agency %"
INVOICE_HEADER = "Agency"

SPOT_CODE_PAID  = 2
SPOT_CODE_BONUS = 10

CUSTOMERS_DB_PATH = Path("data") / "customers.db"

_MARKET_MAP = {
    "SACRAMENTO":     "CVC",
    "CENTRAL VALLEY": "CVC",
    "CVC":            "CVC",
    "SAN FRANCISCO":  "SFO",
    "SFO":            "SFO",
    "SEATTLE":        "SEA",
    "SEA":            "SEA",
    "LOS ANGELES":    "LAX",
    "LAX":            "LAX",
    "HOUSTON":        "HOU",
    "HOU":            "HOU",
    "NEW YORK":       "NYC",
    "NYC":            "NYC",
}

_VALID_MARKETS = list(_MARKET_MAP.values())


def _normalize_market(market_text: str) -> str:
    return _MARKET_MAP.get(market_text.upper().strip(), "CVC")


def _etere_separation(pdf_minutes: int) -> tuple:
    """
    Convert PDF separation value to Etere tuple.
    Rule: 30 min → (25, 0, 0).  General: subtract 5, floor at 0.
    """
    etere_customer = max(0, pdf_minutes - 5)
    return (etere_customer, 0, 0)


# ---------------------------------------------------------------------------
# Overrides sidecar (written by web UI, consumed here)
# ---------------------------------------------------------------------------

def _read_overrides(pdf_path: str) -> dict:
    sidecar = Path(pdf_path).with_suffix(".overrides.json")
    if sidecar.exists():
        try:
            data = json.loads(sidecar.read_text())
            sidecar.unlink()
            return data
        except Exception:
            pass
    return {}


# ---------------------------------------------------------------------------
# Customer DB helpers
# ---------------------------------------------------------------------------

def _lookup_customer(client_name: str, customer_id_hint: Optional[int] = None) -> Optional[int]:
    """Look up customer ID from customers.db by customer_id hint, then by name."""
    try:
        if not CUSTOMERS_DB_PATH.exists():
            return None
        with sqlite3.connect(str(CUSTOMERS_DB_PATH)) as conn:
            # 1. Try exact customer_id hint
            if customer_id_hint is not None:
                row = conn.execute(
                    "SELECT customer_id, customer_name FROM customers WHERE customer_id = ?",
                    (str(customer_id_hint),),
                ).fetchone()
                if row:
                    print(f"[CUSTOMER DB] ✓ Found by ID {customer_id_hint}: {row[1]}")
                    return int(row[0])

            # 2. Exact name + order_type
            row = conn.execute(
                "SELECT customer_id FROM customers WHERE customer_name = ? AND order_type = ?",
                (client_name, AGENCY_NAME),
            ).fetchone()
            if row:
                cid = int(row[0])
                print(f"[CUSTOMER DB] ✓ Exact match: {client_name} → ID {cid}")
                return cid

            # 3. Case-insensitive substring
            rows = conn.execute(
                "SELECT customer_id, customer_name FROM customers WHERE order_type = ?",
                (AGENCY_NAME,),
            ).fetchall()
            for db_id, db_name in rows:
                if (client_name.lower() in db_name.lower() or
                        db_name.lower() in client_name.lower()):
                    cid = int(db_id)
                    print(f"[CUSTOMER DB] ✓ Fuzzy match: {client_name} ≈ {db_name} → ID {cid}")
                    return cid
    except Exception as e:
        print(f"[CUSTOMER DB] ⚠ Lookup failed: {e}")

    print(f"[CUSTOMER] Not found in database: {client_name}")
    cid_str = input("Enter customer ID (or press Enter to search in Etere): ").strip()
    if cid_str:
        try:
            return int(cid_str)
        except ValueError:
            print("[CUSTOMER] Invalid ID — will search in Etere")
    return None


def _upsert_customer(client_name: str, customer_id: int, market_code: str) -> None:
    """Upsert customer into customers.db so future orders find them automatically."""
    try:
        if not CUSTOMERS_DB_PATH.exists():
            return
        with sqlite3.connect(str(CUSTOMERS_DB_PATH)) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO customers
                    (customer_name, customer_id, order_type, default_market)
                VALUES (?, ?, ?, ?)
                """,
                (client_name, str(customer_id), AGENCY_NAME, market_code),
            )
            if conn.total_changes > 0:
                print(f"[CUSTOMER DB] ✓ Saved: {client_name} (ID {customer_id})")
            else:
                print(f"[CUSTOMER DB] ℹ Already known: {client_name}")
    except Exception as e:
        print(f"[CUSTOMER DB] ⚠ Could not save customer (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Upfront input collection (called by orchestrator before browser opens)
# ---------------------------------------------------------------------------

def gather_wallrich_inputs(pdf_path: str) -> Optional[dict]:
    """
    Gather all user inputs before the browser session opens.

    Args:
        pdf_path: Path to Wallrich PDF

    Returns:
        Dict of inputs for process_wallrich_order(), or None to cancel.
    """
    print("\n" + "=" * 70)
    print("WALLRICH ORDER — UPFRONT INPUT COLLECTION")
    print("=" * 70)

    print("\n[PARSE] Reading PDF...")
    try:
        estimates = parse_wallrich_pdf(pdf_path)
    except Exception as e:
        print(f"[PARSE] ✗ Failed: {e}")
        return None

    if not estimates:
        print("[PARSE] ✗ No estimates found in PDF")
        return None

    est = estimates[0]
    market_code = _normalize_market(est.market)
    etere_sep   = _etere_separation(est.separation)

    print(f"\n[PARSE] ✓ Estimate #: {est.estimate_number}")
    print(f"[PARSE] ✓ Client:     {est.client}")
    print(f"[PARSE] ✓ Market:     {est.market} → {market_code}")
    print(f"[PARSE] ✓ Flight:     {est.flight_start} – {est.flight_end}")
    print(f"[PARSE] ✓ Buyer:      {est.buyer}")
    print(f"[PARSE] ✓ Separation: {est.separation} min PDF → {etere_sep} Etere")
    print(f"[PARSE] ✓ Weeks:      {len(est.week_starts)} ({est.week_starts[0]}–{est.week_starts[-1]})")
    print(f"[PARSE] ✓ Lines:      {len(est.lines)}")

    for i, ln in enumerate(est.lines, 1):
        bonus = " [BONUS]" if ln.is_bonus else ""
        print(f"         {i}. {ln.days} {ln.time} {ln.program} "
              f"→ {ln.total_spots} spots @ ${ln.rate:.2f}{bonus}")

    # ── Market override from web sidecar ──
    overrides = _read_overrides(pdf_path)
    if "market" in overrides:
        market_code = overrides["market"]
        print(f"\n[MARKET] Using web override: {market_code}")
    elif market_code not in _VALID_MARKETS or market_code == "UNKNOWN":
        print(f"\n[MARKET] Market '{est.market}' could not be resolved.")
        while True:
            entered = input(f"Enter market code {_VALID_MARKETS}: ").strip().upper()
            if entered in _VALID_MARKETS:
                market_code = entered
                break
            print(f"  Invalid. Choose from: {_VALID_MARKETS}")

    # ── Customer lookup ──
    customer_id = _lookup_customer(est.client)

    # ── Contract code and description defaults ──
    default_code = f"W{est.estimate_number}"
    default_desc = est.description or f"Wallrich Est {est.estimate_number} {market_code}"

    print("\n[CONTRACT]")
    code_input = input(f"  Contract code [{default_code}]: ").strip()
    order_code = code_input or default_code

    desc_input = input(f"  Description   [{default_desc}]: ").strip()
    description = desc_input or default_desc

    print(f"\n[SEPARATION] PDF: {est.separation} min → Etere: {etere_sep}")

    print("\n[CONFIRM] Ready to process.")
    confirm = input("  Start automation? [Y/n]: ").strip().lower()
    if confirm == "n":
        print("[CANCEL] Aborted by user.")
        return None

    return {
        "order_code":    order_code,
        "description":   description,
        "customer_id":   customer_id,
        "market_code":   market_code,
        "separation":    etere_sep,
        "pdf_path":      pdf_path,
    }


# ---------------------------------------------------------------------------
# Main entry point (called by order_processing_service with driver)
# ---------------------------------------------------------------------------

def process_wallrich_order(
    driver: webdriver.Chrome,
    pdf_path: str,
    user_input: dict,
) -> bool:
    """
    Process a Wallrich order end-to-end.

    Args:
        driver:     Selenium WebDriver (already logged in)
        pdf_path:   Path to the Wallrich PDF
        user_input: Dict from gather_wallrich_inputs()

    Returns:
        True if successful, False otherwise.
    """
    etere = EtereClient(driver)
    try:
        return _execute_order(etere, pdf_path, user_input)
    except Exception as e:
        print(f"[WALLRICH] ✗ Order failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def _execute_order(
    etere: EtereClient,
    pdf_path: str,
    user_input: dict,
) -> bool:
    order_code   = user_input["order_code"]
    description  = user_input["description"]
    customer_id  = user_input.get("customer_id")
    market_code  = user_input.get("market_code", "CVC")
    separation   = user_input.get("separation", (25, 0, 0))

    print(f"\n{'='*60}")
    print(f"Processing Wallrich Order: {pdf_path}")
    print(f"{'='*60}\n")

    estimates = parse_wallrich_pdf(pdf_path)
    if not estimates:
        print("[WALLRICH] ✗ No estimates found")
        return False

    est = estimates[0]
    print(f"[PARSE] ✓ {est.estimate_number} / {est.client} / {len(est.lines)} lines")

    # True contract start = first week that has at least one spot on any line.
    # The PDF flight_start is the order effective date, which often precedes airing.
    flight_year = datetime.strptime(est.flight_start, "%m/%d/%Y").year
    contract_start = est.flight_start
    for i, ws in enumerate(est.week_starts):
        if any(i < len(ln.weekly_spots) and ln.weekly_spots[i] > 0 for ln in est.lines):
            contract_start = f"{ws}/{flight_year}"
            break
    if contract_start != est.flight_start:
        print(f"[PARSE] ✓ Contract start: {contract_start} (first airing week; PDF says {est.flight_start})")

    # ── Contract header ──
    contract_number = etere.create_contract_header(
        customer_id=customer_id,
        code=order_code,
        description=description,
        contract_start=contract_start,
        contract_end=est.flight_end,
        customer_order_ref=est.estimate_number,
        notes=est.description,
        charge_to=CHARGE_TO,
        invoice_header=INVOICE_HEADER,
    )
    if not contract_number:
        print("[WALLRICH] ✗ Failed to create contract header")
        return False
    print(f"[WALLRICH] ✓ Contract created: {contract_number}")

    # ── Persist customer ──
    if customer_id:
        _upsert_customer(est.client, customer_id, market_code)

    # ── Contract lines ──
    all_success = True
    line_count  = 0

    for ln in est.lines:
        etere_lines = _build_etere_lines(ln, est, market_code, flight_year)

        for el in etere_lines:
            line_count += 1
            print(f"\n  [LINE {line_count}] {el['description']}")
            print(f"    {el['start_date']} – {el['end_date']}")
            print(f"    {el['spots_per_week']}/wk × {el['num_weeks']} wks "
                  f"= {el['total_spots']} spots  ${el['rate']:.2f}")

            ok = etere.add_contract_line(
                contract_number=contract_number,
                market=market_code,
                start_date=el["start_date"],
                end_date=el["end_date"],
                days=el["days"],
                time_from=el["time_from"],
                time_to=el["time_to"],
                description=el["description"],
                spot_code=el["spot_code"],
                duration_seconds=ln.duration,
                total_spots=el["total_spots"],
                spots_per_week=el["spots_per_week"],
                rate=el["rate"],
                separation_intervals=separation,
            )

            if not ok:
                print(f"    ✗ Failed to add line {line_count}")
                all_success = False
                break

            time.sleep(2)

        if not all_success:
            break

    print(f"\n{'='*60}")
    status = "✓ COMPLETE" if all_success else "✗ FAILED"
    print(f"{status}  Contract: {contract_number}  Lines: {line_count}")
    print(f"{'='*60}")
    return all_success


# ---------------------------------------------------------------------------
# Line building
# ---------------------------------------------------------------------------

def _build_etere_lines(
    ln: WallrichLine,
    est: WallrichEstimate,
    market_code: str,
    flight_year: int,
) -> list:
    """
    Convert a single WallrichLine into one or more Etere line dicts,
    split on gaps and differing weekly spot counts.
    """
    from browser_automation.day_utils import to_etere as days_to_etere

    ranges = consolidate_wallrich_weeks(
        ln.weekly_spots,
        est.week_starts,
        est.flight_end,
        flight_year,
    )

    etere_days = days_to_etere(ln.days)

    # Parse time to 24-hour format: "4:00p-7:00p" → ("16:00", "19:00")
    time_from, time_to = EtereClient.parse_time_range(ln.time)

    language  = extract_language_from_program(ln.program) or ln.program
    spot_code = SPOT_CODE_BONUS if ln.is_bonus else SPOT_CODE_PAID

    result = []
    for rng in ranges:
        description = f"{etere_days} {_fmt_time(ln.time)} {language}"
        if ln.is_bonus:
            description += " BONUS"

        rate = 0.0 if ln.is_bonus else ln.rate

        result.append({
            "start_date":    rng["start_date"],
            "end_date":      rng["end_date"],
            "spots_per_week": rng["spots_per_week"],
            "num_weeks":     rng["num_weeks"],
            "total_spots":   rng["total_spots"],
            "days":          etere_days,
            "time_from":     time_from,
            "time_to":       time_to,
            "description":   description,
            "spot_code":     spot_code,
            "rate":          rate,
        })

    return result


def _fmt_time(time_str: str) -> str:
    """Format time for line description: '7:00p-8:00p' → '7-8p'."""
    m = re.match(r'(\d+):\d+([ap])-(\d+):\d+([ap])', time_str)
    if m:
        sh, sp, eh, ep = m.groups()
        if sp == ep:
            return f"{sh}-{eh}{sp}"
        return f"{sh}{sp}-{eh}{ep}"
    return time_str
