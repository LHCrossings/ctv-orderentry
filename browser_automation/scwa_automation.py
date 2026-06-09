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
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from browser_automation.etere_client import EtereClient
from browser_automation.parsers.scwa_parser import SCWAOrder, SCWALine, parse_scwa_pdf
from src.domain.enums import OrderType, SeparationInterval


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

SCWA_MARKET     = "CVC"
SCWA_SEPARATION = SeparationInterval.SCWA.value   # (15, 0, 0)
from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
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
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date(date_str: str) -> date:
    """Convert MM/DD/YYYY string to date object."""
    return datetime.strptime(date_str, '%m/%d/%Y').date()


def _secs_to_duration(seconds: int) -> str:
    """Convert duration in seconds to Etere duration string (e.g. 30 → ':30')."""
    return f":{seconds}"


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
# DIRECT DB ENTRY
# ─────────────────────────────────────────────────────────────────────────────

def _create_scwa_contract_direct(order: SCWAOrder, inputs: dict) -> Optional[int]:
    """Enter SCWA order directly via DB stored procedures (no browser).

    Returns contract_id on success, None on failure (rolls back fully).
    """
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = inputs.get('customer_id')
    if customer_id is None:
        print("[SCWA DIRECT] ✗ No customer_id — cannot enter without a known ID")
        return None

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        separation = inputs.get('separation', SCWA_SEPARATION)

        flight_start = min(order.lines, key=lambda l: datetime.strptime(l.start_date, '%m/%d/%Y')).start_date
        flight_end   = max(order.lines, key=lambda l: datetime.strptime(l.end_date,   '%m/%d/%Y')).end_date

        contract_id = client.create_contract_header(
            code=inputs['contract_code'],
            description=inputs['description'],
            customer_id=int(customer_id),
            contract_date=_parse_date(flight_start),
            contract_end_date=_parse_date(flight_end),
            contract_type=1,
            billing_type="client",
            note=inputs.get('notes', ''),
            customer_order_ref='',
            allow_rename=True,
        )
        if not contract_id:
            print("[SCWA DIRECT] ✗ Failed to create contract header")
            return None
        print(f"[SCWA DIRECT] ✓ Contract header ID={contract_id}")

        line_count = 0
        for line in order.lines:
            lang_key = _language_key(line.language_block)
            if not lang_key or lang_key not in _ROS_MAP:
                print(f"  ✗ Unknown language block: {line.language_block!r} — skipped")
                continue

            days, time_str = _ROS_MAP[lang_key]
            time_from, time_to = EtereClient.parse_time_range(time_str)
            adjusted_days, _  = EtereClient.check_sunday_6_7a_rule(days, time_str)
            time_range = f"{time_from}-{time_to}"

            eligible_days = _eligible_days_in_flight(adjusted_days, line.start_date, line.end_date)
            max_daily_run = math.ceil(line.total_spots / eligible_days)

            lang_label   = line.language_block.split('(')[0].strip()
            description  = f"{adjusted_days} {lang_label} ROS"
            duration_str = _secs_to_duration(line.duration_seconds)

            line_count += 1
            print(f"  [LINE {line_count}] {lang_label}: {line.start_date}–{line.end_date} "
                  f"{line.total_spots} spots @ ${line.rate}")

            client.add_contract_line(
                market=SCWA_MARKET,
                days=adjusted_days,
                time_range=time_range,
                description=description,
                rate=float(line.rate),
                total_spots=line.total_spots,
                spots_per_week=0,
                max_daily_run=max_daily_run,
                date_from=_parse_date(line.start_date),
                date_to=_parse_date(line.end_date),
                duration=duration_str,
                is_bonus=False,
                booking_code=2,   # always Paid Commercial for SCWA
                separation_intervals=separation,
            )

        conn.commit()
        conn.close()
        print(f"[SCWA DIRECT] ✓ {line_count} lines committed.")
        return contract_id

    except Exception as exc:
        print(f"[SCWA DIRECT] ✗ {exc}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


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

    # Build yymm from flight dates
    from datetime import datetime as _dt
    _starts = sorted(order.lines, key=lambda l: _dt.strptime(l.start_date, '%m/%d/%Y'))
    _ends   = sorted(order.lines, key=lambda l: _dt.strptime(l.end_date,   '%m/%d/%Y'))
    fs = _starts[0].start_date   # earliest start (MM/DD/YYYY)
    fe = _ends[-1].end_date      # latest end
    yy = fs[8:10]
    start_mm = fs[0:2]
    end_mm   = fe[0:2]
    code_yymm = f"{yy}{start_mm}"                                          # first month only
    desc_yymm = code_yymm if start_mm == end_mm else f"{yy}{start_mm}-{yy}{end_mm}"  # full range

    # ── Contract code / description ────────────────────────────────────────
    default_code = f"SCWA {code_yymm}"
    default_desc = f"Sacramento County Water Agency {desc_yymm}"

    raw = input(f"  Contract code [{default_code}]: ").strip()
    code = raw or default_code

    raw = input(f"  Description   [{default_desc}]: ").strip()
    description = raw or default_desc

    # ── Notes ──────────────────────────────────────────────────────────────
    print("[3/3] Contract Notes")
    print("-" * 70)
    default_notes = f"Contact: {order.contact}\nEmail: {order.email}"
    print(f"  Notes default:\n    {default_notes.replace(chr(10), chr(10) + '    ')}")
    raw = input("  Notes [Enter to keep]: ").strip()
    notes = raw or default_notes

    sep = SCWA_SEPARATION

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

        contract_id = _create_scwa_contract_direct(order, inputs)
        return contract_id is not None

    except Exception as exc:
        import traceback
        print(f"\n[SCWA] ✗ Error: {exc}")
        traceback.print_exc()
        return False
