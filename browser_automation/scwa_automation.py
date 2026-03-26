"""
Sacramento County Water Agency (SCWA) Automation

Handles browser automation for SCWA Crossings TV media proposal orders.

Structure:
- One Etere contract per order
- One line per language block
- All lines are ROS paid spots (COM, spot_code=2) — NOT bonus
- Rate = Promo Unit Cost from the PDF
- Total spots covers the full flight; no weekly breakdown
- Max daily run = ceil(total_spots / eligible_days_in_flight)
- No agency: direct client billing

ROS schedule (as of Apr 1, 2026):
  Chinese (M/C):   M-Su  6a–11:59p
  Filipino:        M-Su  4p–7p
  Hmong:           Sa-Su 6p–8p
  South Asian:     M-Su  1p–4p
  Vietnamese:      M-Su  10a–1p   ← updated Apr 1, 2026 (was 11a–1p)
"""

import math
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from browser_automation.etere_client import EtereClient
from browser_automation.parsers.scwa_parser import SCWAOrder, SCWALine, parse_scwa_pdf
from src.domain.enums import BillingType, OrderType, SeparationInterval


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

SCWA_MARKET     = "CVC"
SCWA_SEPARATION = SeparationInterval.SCWA.value   # (15, 0, 0)
CUSTOMER_DB_PATH = os.path.join("data", "customers.db")

# ROS schedule per language key → (days, time_str)
# Vietnamese updated Apr 1, 2026: was 11a-1p, now 10a-1p
_ROS_MAP: dict[str, tuple[str, str]] = {
    'chinese':     ('M-Su',  '6a-11:59p'),
    'filipino':    ('M-Su',  '4p-7p'),
    'hmong':       ('Sa-Su', '6p-8p'),
    'south asian': ('M-Su',  '1p-4p'),
    'vietnamese':  ('M-Su',  '10a-1p'),
}

# Weekday sets for eligible-day counting (Monday=0)
_DAY_SETS: dict[str, set] = {
    'M-Su':  {0, 1, 2, 3, 4, 5, 6},
    'M-F':   {0, 1, 2, 3, 4},
    'M-Sa':  {0, 1, 2, 3, 4, 5},
    'Sa-Su': {5, 6},
    'Sa':    {5},
    'Su':    {6},
}


# ─────────────────────────────────────────────────────────────────────────────
# LANGUAGE MAPPING
# ─────────────────────────────────────────────────────────────────────────────

def _language_key(language_block: str) -> str:
    """
    Map a language block label to a _ROS_MAP key.

    "Chinese (Mandarin /Cantonese, excluding Children)" → "chinese"
    "South Asian (Hindi/Punjabi)"                       → "south asian"
    "Filipino"                                          → "filipino"
    "Hmong"                                             → "hmong"
    "Vietnamese"                                        → "vietnamese"
    """
    lb = language_block.lower()
    if any(k in lb for k in ('chinese', 'mandarin', 'cantonese')):
        return 'chinese'
    if 'south asian' in lb or 'hindi' in lb or 'punjabi' in lb:
        return 'south asian'
    if 'filipino' in lb or 'tagalog' in lb:
        return 'filipino'
    if 'hmong' in lb:
        return 'hmong'
    if 'vietnamese' in lb:
        return 'vietnamese'
    return ''


# ─────────────────────────────────────────────────────────────────────────────
# DAY / SPOT CALCULATIONS
# ─────────────────────────────────────────────────────────────────────────────

def _eligible_days_in_flight(days_pattern: str, start_str: str, end_str: str) -> int:
    """Count eligible broadcast days within start_str–end_str (MM/DD/YYYY)."""
    eligible = _DAY_SETS.get(days_pattern, _DAY_SETS['M-Su'])
    start = datetime.strptime(start_str, '%m/%d/%Y')
    end   = datetime.strptime(end_str,   '%m/%d/%Y')
    count = 0
    current = start
    while current <= end:
        if current.weekday() in eligible:
            count += 1
        current += timedelta(days=1)
    return max(count, 1)


def _active_days_per_week(days_pattern: str) -> int:
    return len(_DAY_SETS.get(days_pattern, _DAY_SETS['M-Su']))


# ─────────────────────────────────────────────────────────────────────────────
# CUSTOMER DB
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_customer(advertiser: str) -> Optional[int]:
    if not os.path.exists(CUSTOMER_DB_PATH):
        return None
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        customer = (
            repo.find_by_name(advertiser, OrderType.SCWA) or
            repo.find_by_name_fuzzy(advertiser, OrderType.SCWA)
        )
        return int(customer.customer_id) if customer else None
    except Exception as exc:
        print(f"[CUSTOMER DB] ⚠ Lookup failed: {exc}")
        return None


def _upsert_customer(customer_id: int, advertiser: str) -> None:
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        repo.save(Customer(
            customer_id=str(customer_id),
            customer_name=advertiser,
            order_type=OrderType.SCWA,
            billing_type="client",
            default_market=SCWA_MARKET,
            separation_customer=SCWA_SEPARATION[0],
            separation_event=SCWA_SEPARATION[1],
            separation_order=SCWA_SEPARATION[2],
        ))
        print(f"[CUSTOMER DB] ✓ Saved: {advertiser} → ID {customer_id}")
    except Exception as exc:
        print(f"[CUSTOMER DB] ✗ Save failed: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# UPFRONT INPUT GATHERING
# ─────────────────────────────────────────────────────────────────────────────

def gather_scwa_inputs(pdf_path: str) -> Optional[dict]:
    """
    Parse PDF and gather all user inputs before the browser session opens.

    Returns dict with contract_code, description, notes, customer_id, separation.
    Returns None if user cancels.
    """
    print("\n" + "=" * 70)
    print("SACRAMENTO COUNTY WATER AGENCY — INPUT COLLECTION")
    print("=" * 70)

    print("\n[PARSE] Reading PDF…")
    try:
        order = parse_scwa_pdf(pdf_path)
    except Exception as exc:
        print(f"[PARSE] ✗ Failed: {exc}")
        return None

    print(f"\nAdvertiser: {order.advertiser}")
    print(f"Contact:    {order.contact}  {order.email}")
    print(f"Campaign:   {order.campaign}")
    print(f"Market:     {order.market}")
    total_spots = sum(l.total_spots for l in order.lines)
    total_cost  = sum(l.total_spots * l.rate for l in order.lines)
    print(f"Lines:      {len(order.lines)}  ({total_spots} spots, ${total_cost:,.2f})")
    print()

    # ── Customer lookup ────────────────────────────────────────────────────
    customer_id = _lookup_customer(order.advertiser)
    if customer_id:
        print(f"[CUSTOMER] ✓ Found in DB: {order.advertiser} → ID {customer_id}")
    else:
        print(f"[CUSTOMER] Not found in DB for '{order.advertiser}'")
        raw_id = input("  Enter Etere customer ID (or blank to select in browser): ").strip()
        if raw_id.isdigit():
            customer_id = int(raw_id)
            save_yn = input(
                f"  Save '{order.advertiser}' (ID {customer_id}) to DB? (y/n): "
            ).strip().lower()
            if save_yn == 'y':
                _upsert_customer(customer_id, order.advertiser)
    print()

    # Build yymm from flight start of first line
    fs = order.lines[0].start_date   # MM/DD/YYYY
    yymm = fs[8:10] + fs[0:2]        # "26" + "04" = "2604"

    # ── Contract code ──────────────────────────────────────────────────────
    print("[1/3] Contract Code")
    print("-" * 70)
    default_code = f"SCWA {yymm}"
    print(f"Default: {default_code}")
    code = default_code if input("Use default? (y/n): ").strip().lower() == 'y' \
           else input("Enter contract code: ").strip()
    print(f"✓ {code}\n")

    # ── Contract description ───────────────────────────────────────────────
    print("[2/3] Contract Description")
    print("-" * 70)
    default_desc = f"Sacramento County Water Agency {yymm}"
    print(f"Default: {default_desc}")
    description = default_desc if input("Use default? (y/n): ").strip().lower() == 'y' \
                  else input("Enter description: ").strip()
    print(f"✓ {description}\n")

    # ── Notes ──────────────────────────────────────────────────────────────
    print("[3/3] Contract Notes")
    print("-" * 70)
    default_notes = f"Contact: {order.contact}\nEmail: {order.email}"
    print(f"Default:\n{default_notes}")
    notes = default_notes if input("Use default? (y/n): ").strip().lower() == 'y' \
            else input("Enter notes: ").strip()
    print(f"✓ {notes}\n")

    # ── Separation ─────────────────────────────────────────────────────────
    sep = SCWA_SEPARATION
    print(f"Separation: Customer={sep[0]}, Event={sep[1]}, Order={sep[2]}")
    if input("Keep default separation? (y/n): ").strip().lower() != 'y':
        c = input(f"  Customer [{sep[0]}]: ").strip()
        e = input(f"  Event [{sep[1]}]: ").strip()
        o = input(f"  Order [{sep[2]}]: ").strip()
        sep = (
            int(c) if c.isdigit() else sep[0],
            int(e) if e.isdigit() else sep[1],
            int(o) if o.isdigit() else sep[2],
        )
    print(f"✓ Separation: {sep}\n")

    print("=" * 70)
    print("✓ All inputs gathered — ready for automation")
    print("=" * 70 + "\n")

    return {
        'contract_code': code,
        'description':   description,
        'notes':         notes,
        'customer_id':   customer_id,
        'separation':    sep,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PROCESSING FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def process_scwa_order(
    driver,
    pdf_path: str,
    shared_session=None,
    pre_gathered_inputs: Optional[dict] = None,
) -> bool:
    """
    Process an SCWA media proposal PDF and create one Etere contract.

    Returns True on success, False on failure.
    """
    try:
        order = parse_scwa_pdf(pdf_path)

        print(f"\n{'=' * 70}")
        print("SACRAMENTO COUNTY WATER AGENCY PROCESSING")
        print(f"{'=' * 70}")
        print(f"Advertiser: {order.advertiser}")
        print(f"Campaign:   {order.campaign}")
        print(f"Market:     {order.market}")
        print(f"Lines:      {len(order.lines)}")
        print(f"{'=' * 70}\n")

        if pre_gathered_inputs:
            inputs = pre_gathered_inputs
            print("[INFO] Using pre-gathered inputs\n")
        else:
            inputs = gather_scwa_inputs(pdf_path)

        if not inputs:
            print("\n✗ Input gathering cancelled")
            return False

        etere = EtereClient(driver)
        customer_id = inputs.get('customer_id')
        notes       = inputs.get('notes', '')
        separation  = inputs.get('separation', SCWA_SEPARATION)
        code        = inputs.get('contract_code', f"SCWA")
        description = inputs.get('description', order.advertiser)

        # ── Create contract header ─────────────────────────────────────────
        # Flight spans the full range across all lines (they're all the same dates for SCWA)
        flight_start = order.lines[0].start_date
        flight_end   = order.lines[0].end_date

        # NOTE: master market is ALWAYS NYC — set once by EtereSession before the
        # browser automation runs. Never override it here.

        contract_number = etere.create_contract_header(
            customer_id=customer_id,
            code=code,
            description=description,
            contract_start=flight_start,
            contract_end=flight_end,
            customer_order_ref=None,
            notes=notes,
            charge_to=BillingType.CUSTOMER_DIRECT.get_charge_to(),
            invoice_header=BillingType.CUSTOMER_DIRECT.get_invoice_header(),
        )

        if not contract_number:
            print("[SCWA] ✗ Failed to create contract header")
            return False

        print(f"[SCWA] ✓ Contract created: {contract_number}")

        # ── Add one line per language block ───────────────────────────────
        line_count = 0

        for line in order.lines:
            lang_key = _language_key(line.language_block)
            if not lang_key or lang_key not in _ROS_MAP:
                print(f"  ✗ Unknown language block: {line.language_block!r} — skipped")
                continue

            days, time_str = _ROS_MAP[lang_key]
            time_from, time_to = EtereClient.parse_time_range(time_str)

            # Apply Sunday 6–7a rule
            adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(days, time_str)

            # Calculate max_daily_run from total spots / eligible days in flight
            eligible_days   = _eligible_days_in_flight(adjusted_days, line.start_date, line.end_date)
            max_daily_run   = math.ceil(line.total_spots / eligible_days)
            active_per_week = _active_days_per_week(adjusted_days)
            spots_per_week  = max_daily_run * active_per_week

            line_count += 1
            print(f"\n  [{line.language_block}]")
            print(f"    Days: {adjusted_days}  Time: {time_from}–{time_to}")
            print(f"    Total spots: {line.total_spots}  Eligible days: {eligible_days}")
            print(f"    spots/wk: {spots_per_week}  max/day: {max_daily_run}  rate: ${line.rate}")

            ok = etere.add_contract_line(
                contract_number=contract_number,
                market=SCWA_MARKET,
                start_date=line.start_date,
                end_date=line.end_date,
                days=adjusted_days,
                time_from=time_from,
                time_to=time_to,
                description=f"{adjusted_days} {line.language_block.split('(')[0].strip()} ROS",
                spot_code=2,                      # Paid Commercial
                duration_seconds=line.duration_seconds,
                total_spots=line.total_spots,
                spots_per_week=spots_per_week,
                max_daily_run=max_daily_run,
                rate=line.rate,
                separation_intervals=separation,
                is_bookend=False,
                is_billboard=False,
            )

            if not ok:
                print(f"    ✗ Failed to add line {line_count}")
                return False

        print(f"\n[SCWA] ✓ All {line_count} Etere lines added")
        print(f"\n{'=' * 70}")
        print("✓ SCWA PROCESSING COMPLETE")
        print(f"{'=' * 70}")
        return True

    except Exception as exc:
        import traceback
        print(f"\n[SCWA] ✗ Error: {exc}")
        traceback.print_exc()
        return False
