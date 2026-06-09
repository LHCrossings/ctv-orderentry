"""
Sierra Donor Services Automation

Handles browser automation for Sierra Donor Services Crossings TV media plan orders.

Structure:
- One Etere contract per order
- One line per daypart segment per language block
- COM lines: spot_code=2 (Paid Commercial), rate from PDF
- BON lines: spot_code=10 (Bonus), rate=0
- Direct client billing (no agency)
- Market: CVC (Central Valley) — from parsed PDF

Multi-daypart lines (e.g. Chinese M-F + Sat-Sun):
  Parser produces placeholder SierraLines with total_spots=0.
  gather_sierra_inputs() asks user for spot count on first segment;
  remainder auto-assigns to last segment.
"""

import math
import os
import sys
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from browser_automation.etere_client import EtereClient
from browser_automation.parsers.sierra_parser import SierraOrder, SierraLine, parse_sierra
from src.domain.enums import BillingType, OrderType

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

SIERRA_SEPARATION = (15, 0, 0)
from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH

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


def _lookup_customer(advertiser: str) -> Optional[int]:
    if not os.path.exists(CUSTOMER_DB_PATH):
        return None
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        customer = (
            repo.find_by_name(advertiser, OrderType.SIERRADONOR) or
            repo.find_by_name_fuzzy(advertiser, OrderType.SIERRADONOR)
        )
        return int(customer.customer_id) if customer else None
    except Exception as exc:
        print(f"[CUSTOMER DB] ⚠ Lookup failed: {exc}")
        return None


def _upsert_customer(customer_id: int, advertiser: str, market: str) -> None:
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        repo.save(Customer(
            customer_id=str(customer_id),
            customer_name=advertiser,
            order_type=OrderType.SIERRADONOR,
            billing_type="client",
            default_market=market,
            separation_customer=SIERRA_SEPARATION[0],
            separation_event=SIERRA_SEPARATION[1],
            separation_order=SIERRA_SEPARATION[2],
        ))
        print(f"[CUSTOMER DB] ✓ Saved: {advertiser} → ID {customer_id}")
    except Exception as exc:
        print(f"[CUSTOMER DB] ✗ Save failed: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# DIRECT DB ENTRY
# ─────────────────────────────────────────────────────────────────────────────

def _create_sierra_contract_direct(order: SierraOrder, inputs: dict) -> Optional[int]:
    """Enter Sierra Donor Services order directly via DB stored procedures (no browser).

    Returns contract_id on success, None on failure (rolls back fully).
    """
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = inputs.get('customer_id')
    if customer_id is None:
        print("[SIERRA DIRECT] ✗ No customer_id — cannot enter without a known ID")
        return None

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        separation = inputs.get('separation', SIERRA_SEPARATION)

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
            print("[SIERRA DIRECT] ✗ Failed to create contract header")
            return None
        print(f"[SIERRA DIRECT] ✓ Contract header ID={contract_id}")

        line_count = 0
        for line in order.lines:
            total_spots = line.total_spots
            if total_spots == 0 and not line.is_bonus:
                print(f"  [SKIP] {line.language_block} {line.days}: 0 spots")
                continue

            adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(line.days, line.time_str)
            time_from, time_to = EtereClient.parse_time_range(line.time_str)
            time_range = f"{time_from}-{time_to}"

            eligible_days = _eligible_days_in_flight(adjusted_days, line.start_date, line.end_date)
            max_daily_run = math.ceil(total_spots / eligible_days) if total_spots > 0 else 1

            is_bonus     = line.is_bonus
            booking_code = 10 if is_bonus else 2
            rate         = 0.0 if is_bonus else float(line.rate)

            lang_short   = line.language_block.split('/')[0].strip()
            description  = f"{adjusted_days} {lang_short} {line.time_str}"
            duration_str = _secs_to_duration(line.duration_seconds)

            line_count += 1
            print(f"  [LINE {line_count}] {line.language_block}: {line.start_date}–{line.end_date} "
                  f"{total_spots} spots  {'BNS' if is_bonus else 'COM'}")

            client.add_contract_line(
                market=order.market,
                days=adjusted_days,
                time_range=time_range,
                description=description,
                rate=rate,
                total_spots=total_spots,
                spots_per_week=0,
                max_daily_run=max_daily_run,
                date_from=_parse_date(line.start_date),
                date_to=_parse_date(line.end_date),
                duration=duration_str,
                is_bonus=is_bonus,
                booking_code=booking_code,
                separation_intervals=separation,
            )

        conn.commit()
        conn.close()
        print(f"[SIERRA DIRECT] ✓ {line_count} lines committed.")
        return contract_id

    except Exception as exc:
        print(f"[SIERRA DIRECT] ✗ {exc}")
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

def gather_sierra_inputs(pdf_path: str) -> Optional[dict]:
    """
    Parse PDF and gather all user inputs before the browser session opens.

    For multi-daypart lines (total_spots==0 placeholders), prompts for the
    spot count on each sub-line and assigns the remainder to the last one.

    Returns dict with: contract_code, description, notes, customer_id,
    separation, spot_overrides {(language_block, days): total_spots}.
    Returns None if user cancels.
    """
    print("\n" + "=" * 70)
    print("SIERRA DONOR SERVICES — INPUT COLLECTION")
    print("=" * 70)

    print("\n[PARSE] Reading PDF…")
    try:
        order = parse_sierra(pdf_path)
    except Exception as exc:
        print(f"[ERROR] Failed to parse PDF: {exc}")
        return None

    total_paid  = sum(l.total_spots for l in order.lines if not l.is_bonus)
    total_bonus = sum(l.total_spots for l in order.lines if l.is_bonus)
    total_cost  = sum(l.total_spots * l.rate for l in order.lines)
    print(f"\nAdvertiser: {order.advertiser}")
    print(f"Market:     {order.market}")
    print(f"Acct Rep:   {order.acct_rep}")
    print(f"Lines:      {len(order.lines)}"
          f"  ({total_paid} paid spots + {total_bonus} bonus = ${total_cost:,.2f})")
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
                _upsert_customer(customer_id, order.advertiser, order.market)
    print()

    # Derive yymm from earliest flight start
    sorted_starts = sorted(
        order.lines, key=lambda l: datetime.strptime(l.start_date, '%m/%d/%Y')
    )
    yymm = (
        datetime.strptime(sorted_starts[0].start_date, '%m/%d/%Y').strftime('%m%y')
        if sorted_starts else ""
    )
    flight_label = (
        datetime.strptime(sorted_starts[0].start_date, '%m/%d/%Y').strftime('%B %Y')
        if sorted_starts else ""
    )

    # ── Contract code / description / notes ───────────────────────────────────
    default_code = f"SDS{yymm}"
    default_desc = f"Sierra Donor Services {flight_label}".strip()

    raw = input(f"  Contract code [{default_code}]: ").strip()
    code = raw or default_code

    raw = input(f"  Description   [{default_desc}]: ").strip()
    description = raw or default_desc

    raw = input("  Notes         [Enter to skip]: ").strip()
    notes = raw

    sep = SIERRA_SEPARATION

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

def process_sierra_order(
    driver,
    pdf_path: str,
    shared_session=None,
    pre_gathered_inputs: Optional[dict] = None,
) -> bool:
    """
    Process a Sierra Donor Services media plan PDF and create one Etere contract.

    Returns True on success, False on failure.
    """
    try:
        order = parse_sierra(pdf_path)

        print(f"\n{'=' * 70}")
        print("SIERRA DONOR SERVICES PROCESSING")
        print(f"{'=' * 70}")
        print(f"Advertiser: {order.advertiser}")
        print(f"Market:     {order.market}")
        print(f"Lines:      {len(order.lines)}")
        print(f"{'=' * 70}\n")

        if pre_gathered_inputs:
            inputs = pre_gathered_inputs
            print("[INFO] Using pre-gathered inputs\n")
        else:
            inputs = gather_sierra_inputs(pdf_path)

        if not inputs:
            print("\n✗ Input gathering cancelled")
            return False

        if driver is None:
            contract_id = _create_sierra_contract_direct(order, inputs)
            return contract_id is not None

        etere       = EtereClient(driver)
        customer_id = inputs.get('customer_id')
        notes       = inputs.get('notes', '')
        separation  = inputs.get('separation', SIERRA_SEPARATION)
        code        = inputs.get('contract_code', 'SDS')
        description = inputs.get('description', order.advertiser)

        # ── Flight date range ──────────────────────────────────────────────
        flight_start = min(
            order.lines, key=lambda l: datetime.strptime(l.start_date, '%m/%d/%Y')
        ).start_date
        flight_end = max(
            order.lines, key=lambda l: datetime.strptime(l.end_date, '%m/%d/%Y')
        ).end_date

        # ── Create contract header ─────────────────────────────────────────
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
            print("[SIERRA] ✗ Failed to create contract header")
            return False

        print(f"[SIERRA] ✓ Contract created: {contract_number}")

        # ── Add one Etere line per SierraLine ─────────────────────────────
        line_count = 0

        for line in order.lines:
            total_spots = line.total_spots

            if total_spots == 0 and not line.is_bonus:
                print(f"  [SKIP] {line.language_block} {line.days}: 0 spots")
                continue

            adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(line.days, line.time_str)
            time_from, time_to = EtereClient.parse_time_range(line.time_str)

            eligible_days = _eligible_days_in_flight(adjusted_days, line.start_date, line.end_date)
            max_daily_run = math.ceil(total_spots / eligible_days) if total_spots > 0 else 1

            spot_code = 10 if line.is_bonus else 2
            rate      = 0.0 if line.is_bonus else line.rate

            lang_short = line.language_block.split('/')[0].strip()
            line_desc  = f"{adjusted_days} {lang_short} {line.time_str}"

            line_count += 1
            print(f"\n  [{line_count}] {line.language_block}")
            print(f"    Days: {adjusted_days}  Time: {time_from}–{time_to}")
            print(f"    Spots: {total_spots}  Eligible days: {eligible_days}  "
                  f"Max/day: {max_daily_run}  Rate: ${rate:.2f}  "
                  f"{'BONUS' if line.is_bonus else 'COM'}")

            ok = etere.add_contract_line(
                contract_number=contract_number,
                market=order.market,
                start_date=line.start_date,
                end_date=line.end_date,
                days=adjusted_days,
                time_from=time_from,
                time_to=time_to,
                description=line_desc,
                spot_code=spot_code,
                duration_seconds=line.duration_seconds,
                total_spots=total_spots,
                spots_per_week=0,
                max_daily_run=max_daily_run,
                rate=rate,
                separation_intervals=separation,
                is_bookend=False,
                is_billboard=False,
            )

            if not ok:
                print(f"    ✗ Failed to add line {line_count}")
                return False

        print(f"\n[SIERRA] ✓ All {line_count} Etere lines added")
        print(f"\n{'=' * 70}")
        print("✓ SIERRA DONOR SERVICES PROCESSING COMPLETE")
        print(f"{'=' * 70}")
        return True

    except Exception as exc:
        import traceback
        print(f"\n[SIERRA] ✗ Error: {exc}")
        traceback.print_exc()
        return False
