"""
Resorts World New York (RWNY) Automation

Handles browser automation for RWNY Crossings TV Media Proposal orders.

Structure:
  - One Etere contract per order (covers full flight)
  - One Etere line per language block per calendar month
  - Paid lines: spot_code=2, rate from "Rate for Resorts World" column
  - Bonus (ROS) lines: spot_code=10, rate=0, daypart from ROS schedule
  - Market: NYC
  - Duration: :30s
  - Separation: (25, 0, 0)
  - Billing: direct client (Customer / Customer)
  - Customer ID: 432 (fixed)

Calendar month handling:
  First month may be partial (e.g., May 15–31).
  Each month gets its own Etere line with that month's spot count.
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
from browser_automation.parsers.rwny_parser import RWNYOrder, RWNYLine, RWNYMonthColumn, parse_rwny_pdf
from src.domain.enums import BillingType, OrderType, SeparationInterval


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

RWNY_CUSTOMER_ID  = 432
RWNY_MARKET       = 'NYC'
RWNY_SEPARATION   = SeparationInterval.RWNY.value   # (25, 0, 0)
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


# ─────────────────────────────────────────────────────────────────────────────
# CUSTOMER DB
# ─────────────────────────────────────────────────────────────────────────────

def _upsert_customer() -> None:
    """Ensure Resorts World New York (ID 432) is in the customer DB."""
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        repo.save(Customer(
            customer_id=str(RWNY_CUSTOMER_ID),
            customer_name='Resorts World New York',
            order_type=OrderType.RWNY,
            billing_type='client',
            default_market=RWNY_MARKET,
            separation_customer=RWNY_SEPARATION[0],
            separation_event=RWNY_SEPARATION[1],
            separation_order=RWNY_SEPARATION[2],
        ))
        print(f'[CUSTOMER DB] ✓ Saved: Resorts World New York → ID {RWNY_CUSTOMER_ID}')
    except Exception as exc:
        print(f'[CUSTOMER DB] ⚠ Save failed (non-fatal): {exc}')


# ─────────────────────────────────────────────────────────────────────────────
# DATE / SPOT CALCULATIONS
# ─────────────────────────────────────────────────────────────────────────────

def _eligible_days_in_range(days_pattern: str, start_str: str, end_str: str) -> int:
    """Count eligible broadcast days within a date range (MM/DD/YYYY)."""
    eligible = _DAY_SETS.get(days_pattern, _DAY_SETS['M-Su'])
    start = datetime.strptime(start_str, '%m/%d/%Y')
    end   = datetime.strptime(end_str,   '%m/%d/%Y')
    count = 0
    cur = start
    while cur <= end:
        if cur.weekday() in eligible:
            count += 1
        cur += timedelta(days=1)
    return max(count, 1)


def _weeks_in_range(start_str: str, end_str: str) -> float:
    """Return number of weeks (float) in the date range."""
    start = datetime.strptime(start_str, '%m/%d/%Y')
    end   = datetime.strptime(end_str,   '%m/%d/%Y')
    days = (end - start).days + 1
    return max(days / 7.0, 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT CODE / DESCRIPTION
# ─────────────────────────────────────────────────────────────────────────────

_MONTH_ABBR = {
    '01': 'JAN', '02': 'FEB', '03': 'MAR', '04': 'APR',
    '05': 'MAY', '06': 'JUN', '07': 'JUL', '08': 'AUG',
    '09': 'SEP', '10': 'OCT', '11': 'NOV', '12': 'DEC',
}
_MONTH_NAMES = {
    '01': 'Jan', '02': 'Feb', '03': 'Mar', '04': 'Apr',
    '05': 'May', '06': 'Jun', '07': 'Jul', '08': 'Aug',
    '09': 'Sep', '10': 'Oct', '11': 'Nov', '12': 'Dec',
}


def _build_code_and_desc(order: RWNYOrder) -> tuple[str, str]:
    """
    Build default contract code and description from flight dates.

    Single month:   code="RWNY MAY26"  desc="Resorts World NY May 2026"
    Multi-month:    code="RWNY MAY-JUN26"  desc="Resorts World NY May-Jun 2026"
    """
    if not order.month_columns:
        yy = order.flight_start[8:10] if order.flight_start else str(datetime.now().year)[2:]
        return f'RWNY {yy}', 'Resorts World NY'

    months = order.month_columns
    yy = months[0].start_date[8:10] if months[0].start_date else str(datetime.now().year)[2:]

    if len(months) == 1:
        mm = months[0].start_date[:2]
        code = f'RWNY {_MONTH_ABBR.get(mm, mm)}{yy}'
        desc = f'Resorts World NY {_MONTH_NAMES.get(mm, mm)} 20{yy}'
    else:
        mm_start = months[0].start_date[:2]
        mm_end   = months[-1].start_date[:2]
        code = f'RWNY {_MONTH_ABBR.get(mm_start, mm_start)}-{_MONTH_ABBR.get(mm_end, mm_end)}{yy}'
        desc = (f'Resorts World NY '
                f'{_MONTH_NAMES.get(mm_start, mm_start)}-'
                f'{_MONTH_NAMES.get(mm_end, mm_end)} 20{yy}')

    return code, desc


# ─────────────────────────────────────────────────────────────────────────────
# DIRECT DB ENTRY
# ─────────────────────────────────────────────────────────────────────────────

def _create_rwny_contract_direct(order: RWNYOrder, inputs: dict) -> Optional[int]:
    """Enter RWNY order directly via DB stored procedures (no browser).

    Returns contract_id on success, None on failure (rolls back fully).
    One Etere line per language block per calendar month.
    spots_per_week=0 → EtereDirectClient auto-selects Rotation (monthly order).
    """
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = inputs.get('customer_id', RWNY_CUSTOMER_ID)
    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        separation  = inputs.get('separation', RWNY_SEPARATION)
        flight_start = inputs.get('flight_start', order.flight_start)
        flight_end   = inputs.get('flight_end',   order.flight_end)

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
            print("[RWNY DIRECT] ✗ Failed to create contract header")
            return None
        print(f"[RWNY DIRECT] ✓ Contract header ID={contract_id}")

        duration_str = _secs_to_duration(order.duration_seconds)
        line_count = 0

        for line in order.lines:
            for mi, month in enumerate(order.month_columns):
                spots = line.monthly_spots[mi] if mi < len(line.monthly_spots) else 0
                if spots == 0:
                    continue

                adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(line.days, line.time_str)
                time_from, time_to = EtereClient.parse_time_range(line.time_str)
                time_range = f"{time_from}-{time_to}"

                is_bonus     = line.is_bonus
                booking_code = 10 if is_bonus else 2
                rate         = 0.0 if is_bonus else float(line.rate)

                if is_bonus:
                    desc = f"{line.language} ROS BNS {month.label}"
                else:
                    desc = f"{line.block_name} {month.label}"

                line_count += 1
                print(f"  [LINE {line_count}] {'BNS' if is_bonus else 'PAID'} "
                      f"{line.language} — {month.label}  {spots} spots")

                client.add_contract_line(
                    market=RWNY_MARKET,
                    days=adjusted_days,
                    time_range=time_range,
                    description=desc,
                    rate=rate,
                    total_spots=spots,
                    spots_per_week=0,      # monthly order → Rotation fires automatically
                    date_from=_parse_date(month.start_date),
                    date_to=_parse_date(month.end_date),
                    duration=duration_str,
                    is_bonus=is_bonus,
                    booking_code=booking_code,
                    separation_intervals=separation,
                )

        conn.commit()
        conn.close()
        print(f"[RWNY DIRECT] ✓ {line_count} lines committed.")
        return contract_id

    except Exception as exc:
        print(f"[RWNY DIRECT] ✗ {exc}")
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

def gather_rwny_inputs(pdf_path: str) -> Optional[dict]:
    """
    Parse the RWNY file and gather all user inputs before browser session opens.

    Prompts for start date confirmation if the date in the file is before today.
    Returns dict with contract_code, description, notes, customer_id, separation.
    Returns None if user cancels.
    """
    print('\n' + '=' * 70)
    print('RESORTS WORLD NEW YORK — INPUT COLLECTION')
    print('=' * 70)

    print('\n[PARSE] Reading file…')
    try:
        orders = parse_rwny_pdf(pdf_path)
    except Exception as exc:
        print(f'[PARSE] ✗ Failed: {exc}')
        return None

    if not orders:
        print('[PARSE] ✗ No order found in file')
        return None

    order = orders[0]

    paid_lines  = [l for l in order.lines if not l.is_bonus]
    bonus_lines = [l for l in order.lines if l.is_bonus]
    total_spots = sum(l.total_spots for l in order.lines)
    total_cost  = sum(l.total_spots * l.rate for l in paid_lines)

    print(f'\n  Client:   {order.client}')
    print(f'  Contact:  {order.contact}  {order.email}')
    print(f'  Market:   {order.market}  (:30s)')
    print(f'  Flight:   {order.flight_start} – {order.flight_end}')
    print(f'  Months:   {[c.label for c in order.month_columns]}')
    print(f'  Lines:    {len(paid_lines)} paid + {len(bonus_lines)} bonus  ({total_spots} total spots, ${total_cost:,.2f})')
    print()

    # ── Flight start date confirmation ─────────────────────────────────────
    flight_start = order.flight_start
    flight_end   = order.flight_end

    if flight_start:
        try:
            fs_dt = datetime.strptime(flight_start, '%m/%d/%Y')
            if fs_dt.date() < datetime.today().date():
                print(f'[DATE CHECK] Start date {flight_start} is in the past.')
                raw = input(f'  Confirm start date [{flight_start}] or enter new (MM/DD/YYYY): ').strip()
                if raw:
                    # Validate format
                    try:
                        datetime.strptime(raw, '%m/%d/%Y')
                        flight_start = raw
                        print(f'  ✓ Start date updated to {flight_start}')
                    except ValueError:
                        print(f'  ✗ Invalid format — keeping {flight_start}')
                else:
                    print(f'  ✓ Keeping {flight_start}')
                print()
        except ValueError:
            pass

    # ── Customer DB ────────────────────────────────────────────────────────
    _upsert_customer()
    print()

    # ── Contract code ──────────────────────────────────────────────────────
    default_code, default_desc = _build_code_and_desc(order)

    print('[1/3] Contract Code')
    print('-' * 70)
    print(f'Default: {default_code}')
    code = default_code if input('Use default? (y/n): ').strip().lower() == 'y' \
           else input('Enter contract code: ').strip()
    print(f'✓ {code}\n')

    # ── Contract description ───────────────────────────────────────────────
    print('[2/3] Contract Description')
    print('-' * 70)
    print(f'Default: {default_desc}')
    description = default_desc if input('Use default? (y/n): ').strip().lower() == 'y' \
                  else input('Enter description: ').strip()
    print(f'✓ {description}\n')

    # ── Notes ──────────────────────────────────────────────────────────────
    print('[3/3] Contract Notes')
    print('-' * 70)
    default_notes = f'Contact: {order.contact}\nEmail: {order.email}'
    print(f'Default:\n{default_notes}')
    notes = default_notes if input('Use default? (y/n): ').strip().lower() == 'y' \
            else input('Enter notes: ').strip()
    print(f'✓ Notes set\n')

    # ── Separation ─────────────────────────────────────────────────────────
    sep = RWNY_SEPARATION
    print(f'Separation: Customer={sep[0]}, Order={sep[1]}, Event={sep[2]}')
    if input('Keep default separation? (y/n): ').strip().lower() != 'y':
        c = input(f'  Customer [{sep[0]}]: ').strip()
        e = input(f'  Event    [{sep[1]}]: ').strip()
        o = input(f'  Order    [{sep[2]}]: ').strip()
        sep = (
            int(c) if c.isdigit() else sep[0],
            int(e) if e.isdigit() else sep[1],
            int(o) if o.isdigit() else sep[2],
        )
    print(f'✓ Separation: {sep}\n')

    print('=' * 70)
    print('✓ All inputs gathered — ready for automation')
    print('=' * 70 + '\n')

    return {
        'contract_code':  code,
        'description':    description,
        'notes':          notes,
        'customer_id':    RWNY_CUSTOMER_ID,
        'separation':     sep,
        'flight_start':   flight_start,
        'flight_end':     flight_end,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PROCESSING FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def process_rwny_order(
    driver,
    pdf_path: str,
    shared_session=None,
    pre_gathered_inputs: Optional[dict] = None,
) -> bool:
    """
    Process an RWNY media proposal and create one Etere contract.

    One Etere line is created per language block per calendar month.
    Returns True on success, False on failure.
    """
    try:
        orders = parse_rwny_pdf(pdf_path)
        if not orders:
            print('[RWNY] ✗ Failed to parse file')
            return False

        order = orders[0]

        print(f'\n{"=" * 70}')
        print('RESORTS WORLD NEW YORK PROCESSING')
        print(f'{"=" * 70}')
        print(f'  Client:  {order.client}')
        print(f'  Market:  {order.market}  (:30s)')
        print(f'  Flight:  {order.flight_start} – {order.flight_end}')
        print(f'  Months:  {[c.label for c in order.month_columns]}')
        print(f'  Lines:   {len(order.lines)}  ({sum(l.total_spots for l in order.lines)} total spots)')
        print(f'{"=" * 70}\n')

        if pre_gathered_inputs:
            inputs = pre_gathered_inputs
            print('[INFO] Using pre-gathered inputs\n')
        else:
            inputs = gather_rwny_inputs(pdf_path)

        if not inputs:
            print('\n✗ Input gathering cancelled')
            return False

        # Apply any user-corrected flight dates
        flight_start = inputs.get('flight_start', order.flight_start)
        flight_end   = inputs.get('flight_end',   order.flight_end)

        # Rebuild month columns if start date was corrected
        if flight_start != order.flight_start and order.month_columns:
            from browser_automation.parsers.rwny_parser import _build_month_columns, _detect_year
            year = _detect_year(flight_start)
            labels = [mc.label for mc in order.month_columns]
            order.month_columns = _build_month_columns(labels, flight_start, year)

        if driver is None:
            contract_id = _create_rwny_contract_direct(order, inputs)
            return contract_id is not None

        etere      = EtereClient(driver)
        separation = inputs.get('separation', RWNY_SEPARATION)
        code       = inputs.get('contract_code', 'RWNY')
        description = inputs.get('description', order.client)
        notes      = inputs.get('notes', '')
        customer_id = inputs.get('customer_id', RWNY_CUSTOMER_ID)

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
            print('[RWNY] ✗ Failed to create contract header')
            return False

        print(f'[RWNY] ✓ Contract created: {contract_number}')

        # ── Add one Etere line per language block per month ───────────────
        line_count = 0
        all_ok = True

        for line in order.lines:
            for mi, month in enumerate(order.month_columns):
                spots = line.monthly_spots[mi] if mi < len(line.monthly_spots) else 0
                if spots == 0:
                    continue

                # Apply Sunday 6–7a rule
                adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(line.days, line.time_str)

                # Parse time range for Etere
                time_from, time_to = EtereClient.parse_time_range(line.time_str)

                # Calculate scheduling parameters for the month
                eligible_days = _eligible_days_in_range(
                    adjusted_days, month.start_date, month.end_date)
                max_daily_run = math.ceil(spots / eligible_days)
                weeks = _weeks_in_range(month.start_date, month.end_date)
                spots_per_week = math.ceil(spots / weeks)

                spot_code = 10 if line.is_bonus else 2
                if line.is_bonus:
                    desc = f'{line.language} ROS BNS {month.label}'
                else:
                    desc = f'{line.block_name} {month.label}'

                line_count += 1
                print(f'\n  [{line_count}] {"BNS" if line.is_bonus else "PAID"} '
                      f'{line.language} — {month.label}')
                print(f'      {adjusted_days} {time_from}–{time_to}  '
                      f'{month.start_date}–{month.end_date}')
                print(f'      {spots} spots  max/day={max_daily_run}  '
                      f'rate=${line.rate:.2f}')

                ok = etere.add_contract_line(
                    contract_number=contract_number,
                    market=RWNY_MARKET,
                    start_date=month.start_date,
                    end_date=month.end_date,
                    days=adjusted_days,
                    time_from=time_from,
                    time_to=time_to,
                    description=desc,
                    spot_code=spot_code,
                    duration_seconds=order.duration_seconds,
                    total_spots=spots,
                    spots_per_week=spots_per_week,
                    max_daily_run=max_daily_run,
                    rate=line.rate,
                    separation_intervals=separation,
                    is_bookend=False,
                    is_billboard=False,
                )

                if not ok:
                    print(f'      ✗ Failed to add line {line_count}')
                    all_ok = False

        if all_ok:
            print(f'\n[RWNY] ✓ All {line_count} Etere lines added')
        else:
            print(f'\n[RWNY] ⚠ Completed with errors — check output above')

        print(f'\n{"=" * 70}')
        print('✓ RWNY PROCESSING COMPLETE' if all_ok else '⚠ RWNY PROCESSING COMPLETED WITH ERRORS')
        print(f'{"=" * 70}')
        return all_ok

    except Exception as exc:
        import traceback
        print(f'\n[RWNY] ✗ Error: {exc}')
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print('Usage: python rwny_automation.py <pdf_or_xlsx_path>')
        sys.exit(1)

    result = gather_rwny_inputs(sys.argv[1])
    if result:
        print('\nInputs collected successfully (browser session not started in standalone mode)')
    else:
        print('\nInput collection cancelled or failed')
