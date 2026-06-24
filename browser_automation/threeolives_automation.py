"""
3 Olives Media Order Automation

Handles browser automation for 3 Olives Media insertion orders
(e.g. Riverside County Voter Registration and Elections).

Business Rules:
- Single contract per file; single market (default LAX — Riverside County)
- Rate: Discounted rate from IO (column "Discounted") — already the billing rate
- Separation: (15, 0, 0) standard
- Bonus lines: spot_code = BNS, rate = 0
- ROS Bonus lines: time/days from parse_daypart("ROS Bonus") → M-Su 6a-11:59p
- Master market: NYC (standard Crossings TV)
- Description: "Days time Program" or "Days time BNS Program"
"""

import math
import os
import sys
from pathlib import Path
from typing import Optional

_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
from browser_automation.etere_client import EtereClient
from browser_automation.parsers.threeolives_parser import (
    ThreeOlivesOrder,
    parse_daypart,
    parse_threeolives,
)
from src.domain.enums import OrderType, SeparationInterval

DEFAULT_MARKET = 'LAX'   # Riverside County is in the LA DMA
THREEOLIVES_SEPARATION = SeparationInterval.for_order_type(OrderType.THREEOLIVES)


# ─────────────────────────────────────────────────────────────────────────────
# CUSTOMER DATABASE
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_customer(client_name: str) -> Optional[dict]:
    if not os.path.exists(CUSTOMER_DB_PATH):
        return None
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        customer = (
            repo.find_by_name(client_name, OrderType.THREEOLIVES) or
            repo.find_by_name_fuzzy(client_name, OrderType.THREEOLIVES)
        )
        if customer:
            return {
                'customer_id':      customer.customer_id,
                'code_name':        customer.code_name,
                'description_name': customer.description_name,
                'include_market':   bool(customer.include_market_in_code),
                'separation':       customer.get_separation_intervals(),
            }
    except Exception as exc:
        print(f"[CUSTOMER DB] ⚠ Lookup failed: {exc}")
    return None


def _save_customer(customer_id: str, client_name: str) -> None:
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        repo.save(Customer(
            customer_id=customer_id,
            customer_name=client_name,
            order_type=OrderType.THREEOLIVES,
            billing_type='agency',
            separation_customer=THREEOLIVES_SEPARATION[0],
            separation_event=THREEOLIVES_SEPARATION[1],
            separation_order=THREEOLIVES_SEPARATION[2],
        ))
        print(f"[CUSTOMER DB] ✓ Saved: {client_name} → ID {customer_id}")
    except Exception as exc:
        print(f"[CUSTOMER DB] ✗ Save failed: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# DIRECT DB HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date(s):
    from datetime import date, datetime
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


def _create_threeolives_contract_direct(order: 'ThreeOlivesOrder', inputs: dict) -> bool:
    """Enter 3 Olives order directly via DB stored procedures (no browser)."""
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = inputs.get('customer_id')
    if customer_id is None:
        print("[3OLIVES DIRECT] ✗ No customer_id — cannot enter without a known ID")
        return False

    market     = inputs['market']
    separation = inputs.get('separation', THREEOLIVES_SEPARATION)

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=inputs['contract_code'],
            description=inputs['description'],
            customer_id=int(customer_id),
            contract_date=_parse_date(order.flight_start),
            contract_end_date=_parse_date(order.flight_end),
            contract_type=1,
            billing_type="agency",
            allow_rename=True,
        )
        if not contract_id:
            print("[3OLIVES DIRECT] ✗ Failed to create contract header")
            return False
        print(f"[3OLIVES DIRECT] ✓ Contract ID={contract_id}")

        line_count = 0

        for line in order.lines:
            etere_days, etere_time = parse_daypart(line.time_str)
            adjusted_days, _       = EtereClient.check_sunday_6_7a_rule(etere_days, etere_time)
            time_from, time_to     = EtereClient.parse_time_range(etere_time)

            is_bonus     = line.is_bonus
            booking_code = 10 if is_bonus else 2
            description  = line.get_description(adjusted_days, etere_time)

            ranges = EtereClient.consolidate_weeks(
                weekly_spots=line.weekly_spots,
                week_start_dates=line.week_start_dates,
                flight_end=order.flight_end,
            )

            for rng in ranges:
                line_count  += 1
                spw          = rng['spots_per_week']
                weeks        = rng['weeks']
                total_spots  = spw * weeks
                print(f"  [LINE {line_count}] {description}: "
                      f"{rng['start_date']}–{rng['end_date']} "
                      f"({spw}/wk×{weeks}={total_spots})")
                client.add_contract_line(
                    market=market,
                    days=adjusted_days,
                    time_range=f"{time_from}-{time_to}",
                    description=description,
                    rate=float(line.rate),
                    total_spots=total_spots,
                    spots_per_week=spw,
                    date_from=_parse_date(rng['start_date']),
                    date_to=_parse_date(rng['end_date']),
                    duration=_secs_to_duration(30),
                    is_bonus=is_bonus,
                    booking_code=booking_code,
                    separation_intervals=separation,
                )

        conn.commit()
        conn.close()
        print(f"[3OLIVES DIRECT] ✓ {line_count} line(s) entered")
        return True

    except Exception as exc:
        print(f"[3OLIVES DIRECT] ✗ {exc}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return False


# ─────────────────────────────────────────────────────────────────────────────
# MAX DAILY RUN
# ─────────────────────────────────────────────────────────────────────────────

_DAY_COUNTS = {'M-F': 5, 'M-Sa': 6, 'M-Su': 7, 'Sa-Su': 2, 'Sa': 1, 'Su': 1}


def _max_daily(etere_days: str, spots_per_week: int) -> int:
    days = _DAY_COUNTS.get(etere_days, 7)
    return math.ceil(spots_per_week / days) if days > 0 else spots_per_week


# ─────────────────────────────────────────────────────────────────────────────
# UPFRONT INPUT GATHERING
# ─────────────────────────────────────────────────────────────────────────────

def gather_threeolives_inputs(file_path: str) -> Optional[dict]:
    """
    Parse the order and gather all operator inputs before the browser session.

    Returns dict with keys: contract_code, description, market, customer_id,
    separation, sheet_name.  Returns None if operator cancels.
    """
    print('\n' + '=' * 70)
    print('3 OLIVES MEDIA - INPUT COLLECTION')
    print('=' * 70)

    # Determine sheet name for Excel files
    sheet_name = 'Option 1'
    ext = Path(file_path).suffix.lower()
    if ext in {'.xlsx', '.xlsm'}:
        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, read_only=True)
            sheets = wb.sheetnames
            wb.close()
        except Exception:
            sheets = ['Option 1']

        if len(sheets) > 1:
            print(f'\nAvailable sheets: {sheets}')
            choice = input(f'Sheet to parse (default "{sheets[0]}"): ').strip()
            sheet_name = choice if choice in sheets else sheets[0]

    print('\n[PARSE] Reading file…')
    try:
        order = parse_threeolives(file_path, sheet_name=sheet_name)
    except Exception as exc:
        print(f'[PARSE] ✗ Failed: {exc}')
        return None

    print(f'\nClient:  {order.client}')
    print(f'Contact: {order.contact}')
    print(f'Email:   {order.email}')
    print(f'Channel: {order.channel}')
    print(f'Date:    {order.order_date}')
    print(f'Flight:  {order.flight_start} – {order.flight_end}')
    print(f'Source:  {order.source_sheet}')
    print(f'Lines:   {len(order.lines)}')
    paid = sum(ln.total_spots for ln in order.lines if not ln.is_bonus)
    bonus = sum(ln.total_spots for ln in order.lines if ln.is_bonus)
    cost = sum(ln.rate * ln.total_spots for ln in order.lines)
    print(f'Spots:   {paid} paid + {bonus} bonus = {paid + bonus} total')
    print(f'Cost:    ${float(cost):,.2f}')
    print()

    # ── Customer lookup ───────────────────────────────────────────────────────
    customer_info = _lookup_customer(order.client)
    customer_id: Optional[int] = None
    separation = THREEOLIVES_SEPARATION

    if customer_info:
        customer_id = customer_info['customer_id']
        separation = customer_info['separation']
        print(f'[CUSTOMER] ✓ Found in DB: {order.client} → ID {customer_id}')
    else:
        print(f'[CUSTOMER] Not found in DB for "{order.client}"')
        raw_id = input('  Enter Etere customer ID (or blank to select in browser): ').strip()
        if raw_id.isdigit():
            customer_id = int(raw_id)
            save_yn = input(
                f'  Save "{order.client}" (ID {customer_id}) to DB? (y/n): '
            ).strip().lower()
            if save_yn == 'y':
                _save_customer(str(customer_id), order.client)
    print()

    # ── Market ────────────────────────────────────────────────────────────────
    print(f'[MARKET] Default: {DEFAULT_MARKET} (Riverside County = LA DMA)')
    market_input = input(f'Market code (Enter for {DEFAULT_MARKET}): ').strip().upper()
    market = market_input if market_input else DEFAULT_MARKET
    print(f'✓ {market}\n')

    # ── Contract code / description ────────────────────────────────────────────
    # yymm from flight start e.g. "5/4/2026" → "2605"
    try:
        fs_parts = order.flight_start.split('/')
        yymm = fs_parts[2][2:] + fs_parts[0].zfill(2)
    except Exception:
        yymm = '2605'
    default_code = f'RiVoters {yymm}'
    if customer_info and customer_info.get('code_name'):
        cn = customer_info['code_name']
        default_code = f'{cn} {yymm}'
        if customer_info.get('include_market'):
            market_short = {'CVC': 'CV', 'SFO': 'SF', 'SEA': 'SEA'}.get(market, market)
            default_code = f'{cn} {market_short} {yymm}'
    default_desc = f'Riverside County Voters {yymm}'
    if customer_info and customer_info.get('description_name'):
        default_desc = f'{customer_info["description_name"]} {yymm}'

    raw = input(f'  Contract code [{default_code}]: ').strip()
    contract_code = raw or default_code

    raw = input(f'  Description   [{default_desc}]: ').strip()
    description = raw or default_desc

    # ── Separation ────────────────────────────────────────────────────────────
    print('[3/3] Spot Separation')
    print('-' * 70)
    print(f'Default: {separation[0]} min customer / {separation[1]} event / {separation[2]} order')
    use_def = input('Use default? (y/n): ').strip().lower()
    if use_def != 'y':
        try:
            c = int(input('  Customer separation (min): ').strip())
            separation = (c, separation[1], separation[2])
        except ValueError:
            pass
    print(f'✓ {separation}\n')

    return {
        'contract_code': contract_code,
        'description': description,
        'market': market,
        'customer_id': customer_id,
        'separation': separation,
        'sheet_name': sheet_name,
        '_order': order,          # carry parsed order so process_ doesn't re-parse
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PROCESSING FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def process_threeolives_order(
    file_path: str,
    shared_session=None,
    pre_gathered_inputs: Optional[dict] = None,
) -> bool:
    """
    Process a 3 Olives Media order file and create the Etere contract.

    Args:
        file_path:            Path to PDF or Excel file
        shared_session:       Optional shared Etere session
        pre_gathered_inputs:  Pre-gathered inputs dict (skips upfront prompts)

    Returns:
        True if contract created successfully, False otherwise.
    """
    try:
        if pre_gathered_inputs and '_order' in pre_gathered_inputs:
            order = pre_gathered_inputs['_order']
        else:
            sheet_name = (pre_gathered_inputs or {}).get('sheet_name', 'Option 1')
            order = parse_threeolives(file_path, sheet_name=sheet_name)

        print(f"\n{'=' * 70}")
        print('3 OLIVES MEDIA ORDER PROCESSING')
        print(f"{'=' * 70}")
        print(f'Client:  {order.client}')
        print(f'Flight:  {order.flight_start} – {order.flight_end}')
        print(f'Lines:   {len(order.lines)}')
        print(f"{'=' * 70}\n")

        if pre_gathered_inputs:
            inputs = pre_gathered_inputs
            print('[INFO] Using pre-gathered inputs\n')
        else:
            inputs = gather_threeolives_inputs(file_path)

        if not inputs:
            print('\n✗ Input gathering cancelled')
            return False

        return _create_threeolives_contract_direct(order, inputs)

    except Exception as exc:
        print(f'\n✗ Error processing 3 Olives order: {exc}')
        import traceback
        traceback.print_exc()
        return False



# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE TEST
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys as _sys
    path = _sys.argv[1] if len(_sys.argv) > 1 else \
        'incoming/3Olives Media_2026 Primary_Riverside CountyVoters.pdf'
    result = gather_threeolives_inputs(path)
    if result:
        print('\n--- Collected inputs ---')
        for k, v in result.items():
            if k != '_order':
                print(f'  {k}: {v}')
