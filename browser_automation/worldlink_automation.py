"""
WorldLink Order Automation
Browser automation for entering WorldLink/Tatari agency orders into Etere.

═══════════════════════════════════════════════════════════════════════════════
WORLDLINK BUSINESS RULES
═══════════════════════════════════════════════════════════════════════════════

Networks:
    1. Crossings TV (CROSSINGS): NYC line at real rate + CMP line at $0
       - Two Etere lines per PDF line (NYC + CMP)
       - CMP line ($0) gets replicated to other markets via block refresh
    2. Asian Channel (ASIAN): Single DAL market line per PDF line

Billing (Universal for ALL WorldLink):
    - Charge To: "Customer share indicating agency %"
    - Invoice Header: "Agency"

Contract Format:
    - Code: "WL {Agency} {Tracking#}" (built by parser)
    - Description: "WL {Agency} {Advertiser} {Spot} {Tracking#}" (built by parser)

Order Types:
    - new: Create new contract header + add all lines
    - revision_add / revision_change: Skip header, prompt for existing contract#

Block Refresh:
    - Required for all WorldLink contracts (requires_block_refresh() = True)
    - User must refresh blocks in Etere after CMP lines are added
    - highest_line tracked for revision orders (partial refresh)

Separation:
    - Customer=5, Order=15, Event=0 (SeparationInterval.WORLDLINK)

═══════════════════════════════════════════════════════════════════════════════
IMPORTS
═══════════════════════════════════════════════════════════════════════════════
"""

import atexit
import math
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from etere_client import EtereClient

from browser_automation.parsers.worldlink_parser import parse_worldlink_pdf
from src.domain.enums import BillingType, OrderType, SeparationInterval

# ═══════════════════════════════════════════════════════════════════════════════
# SESSION SUMMARY — revised contracts accumulator
# ═══════════════════════════════════════════════════════════════════════════════

_session_revised: list[str] = []


def _print_session_summary():
    """Print revised contract list at program exit. Fires via atexit."""
    if _session_revised:
        print("\n" + "=" * 60)
        print("WORLDLINK SESSION SUMMARY — Revised contracts:")
        print("  " + ", ".join(_session_revised))
        print("=" * 60)


atexit.register(_print_session_summary)


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH

WL_DEFAULT_SEPARATION = SeparationInterval.WORLDLINK.value  # (5, 15, 0)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _duration_to_seconds(duration_str: str) -> int:
    """WorldLink parser stores duration as integer string (already in seconds)."""
    try:
        return int(duration_str)
    except (ValueError, TypeError):
        return 30


def _format_time_range_short(time_range: str) -> str:
    """Convert '7:00 AM - 4:00 PM' to '7a-4p' format."""
    parts = time_range.split(' - ')
    if len(parts) != 2:
        return time_range
    start, end = parts
    start_short = start.replace(':00', '').replace(' AM', 'a').replace(' PM', 'p')
    end_short = end.replace(':00', '').replace(' AM', 'a').replace(' PM', 'p')
    return f"{start_short}-{end_short}"


def _format_24hr_short(hhmm: str) -> str:
    """Convert 24-hour 'HH:MM' to short display string.

    Examples: '06:00' → '6a', '23:00' → '11p', '23:59' → '12m', '12:00' → '12n'
    """
    try:
        h, m = int(hhmm[:2]), int(hhmm[3:])
    except (ValueError, IndexError):
        return hhmm
    if hhmm == '23:59':
        return '12m'
    if h == 0:
        suffix, display_h = 'a', 12
    elif h < 12:
        suffix, display_h = 'a', h
    elif h == 12:
        suffix, display_h = 'n', 12
    else:
        suffix, display_h = 'p', h - 12
    mins = f":{m:02d}" if m != 0 else ''
    return f"{display_h}{mins}{suffix}"


def _short_time_range(from_time: str, to_time: str) -> str:
    """Build short time range string from normalized 24hr times, e.g. '6a-12m'."""
    return f"{_format_24hr_short(from_time)}-{_format_24hr_short(to_time)}"


def _parse_date(date_str: str):
    """Convert 'MM/DD/YYYY' string to date object for create_contract_header."""
    return datetime.strptime(date_str.strip(), '%m/%d/%Y').date()


def _build_notes(order_data: dict) -> str:
    """Build contract notes — just the Order Comment from the PDF."""
    import re
    comment = order_data.get('order_comment', '') or ''
    # Strip Unicode private-use area characters (PDF font artifacts like \ue010)
    comment = re.sub(r'[\ue000-\uf8ff]', '', comment).strip()
    return comment


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOMER DATABASE ACCESS
# ═══════════════════════════════════════════════════════════════════════════════

def lookup_customer(
    client_name: str,
    db_path: str = CUSTOMER_DB_PATH
) -> Optional[dict]:
    """
    Look up WorldLink customer in the database.

    No hardcoded fallbacks — WorldLink advertisers are diverse. Returns None if
    not found so gather_worldlink_inputs() can prompt the user and save for next time.
    """
    if not os.path.exists(db_path):
        return None
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        repo = CustomerRepository(db_path)
        customer = (repo.find_by_name(client_name, OrderType.WORLDLINK)
                    or repo.find_by_fuzzy_match(client_name, OrderType.WORLDLINK))
        if customer:
            return {
                'customer_id': customer.customer_id,
                'abbreviation': customer.abbreviation,
                'separation': (
                    customer.separation_customer,
                    customer.separation_order,
                    customer.separation_event,
                ),
            }
    except Exception as e:
        print(f"[CUSTOMER DB] ⚠ Database lookup failed: {e}")
    return None


def save_new_customer(
    customer_id: str,
    customer_name: str,
    abbreviation: str,
    separation: tuple,
    db_path: str = CUSTOMER_DB_PATH,
) -> None:
    """Save a new WorldLink customer to the database for future orders."""
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        repo = CustomerRepository(db_path)
        repo.save(Customer(
            customer_id=customer_id,
            customer_name=customer_name,
            order_type=OrderType.WORLDLINK,
            abbreviation=abbreviation,
            default_market=None,
            billing_type='agency',
            separation_customer=separation[0],
            separation_order=separation[1],
            separation_event=separation[2],
        ))
        print(f"[CUSTOMER DB] ✓ Saved: {customer_name} → ID {customer_id}")
    except Exception as e:
        print(f"[CUSTOMER DB] ✗ Save failed: {e}")


def _lookup_contract_by_tracking(tracking_number: str) -> tuple:
    """
    Query CONTRATTITESTATA for a contract whose CUSTOMERREF matches the WL tracking number.

    Returns (id_str, cod_contratto) where id_str is the integer ID used in Etere URLs,
    or (None, None) if not found or DB unavailable.
    """
    if not tracking_number:
        return None, None
    try:
        from browser_automation.etere_direct_client import connect as db_connect
        with db_connect() as conn:
            ph = '%s' if type(conn).__module__.startswith('pymssql') else '?'
            cursor = conn.cursor()
            sql = (
                f"SELECT TOP 1 ID_CONTRATTITESTATA, COD_CONTRATTO FROM CONTRATTITESTATA"
                f" WHERE CUSTOMERREF = {ph}"
                f" ORDER BY ID_CONTRATTITESTATA DESC"
            )
            cursor.execute(sql, (tracking_number,))
            row = cursor.fetchone()
            if row:
                return str(row[0]).strip(), str(row[1]).strip()
    except Exception as e:
        print(f"[REVISION] ⚠ DB lookup failed: {e}")
    return None, None


# ═══════════════════════════════════════════════════════════════════════════════
# UPFRONT INPUT COLLECTION
# ═══════════════════════════════════════════════════════════════════════════════

def gather_worldlink_inputs(pdf_path: str) -> Optional[dict]:
    """
    Gather ALL user inputs BEFORE browser automation starts.

    Parses PDF, detects network type, auto-detects customer from database,
    handles revision contract lookup, and returns everything needed for
    unattended automation.
    """
    print("\n" + "="*70)
    print("WORLDLINK ORDER - UPFRONT INPUT COLLECTION")
    print("="*70)

    print("\n[PARSE] Reading PDF...")
    try:
        order_data = parse_worldlink_pdf(pdf_path)
    except Exception as e:
        print(f"[PARSE] ✗ Failed: {e}")
        return None

    if not order_data or not order_data.get('lines'):
        print("[PARSE] ✗ Could not parse order or no lines found")
        return None

    lines = order_data['lines']
    network = order_data.get('network', 'CROSSINGS')
    order_type_str = order_data.get('order_type', 'new')
    advertiser = order_data.get('advertiser', '')

    print(f"[PARSE] ✓ Advertiser: {advertiser}")
    print(f"[PARSE] ✓ Tracking: {order_data.get('tracking_number', '')}")
    print(f"[PARSE] ✓ Network: {network} | Type: {order_type_str.upper()}")
    print(f"[PARSE] ✓ Lines: {len(lines)}")

    # Revision orders: skip customer details — contract already exists in Etere.
    # contract_number = COD_CONTRATTO string (Selenium navigates by code)
    # contract_id     = ID_CONTRATTITESTATA integer (direct DB uses the integer PK)
    contract_number = None
    contract_id = None
    if order_type_str != 'new':
        print(f"\n[REVISION] Order type: {order_type_str.upper()}")
        tracking = order_data.get('tracking_number', '')
        found_id, found_code = _lookup_contract_by_tracking(tracking)
        if found_id:
            print(f"[REVISION] ✓ Found contract {found_code} (ID {found_id}) for tracking '{tracking}'")
            confirm = input(f"  Use {found_code} (ID {found_id})? (y/n): ").strip().lower()
            if confirm == 'y':
                contract_number = found_code
                contract_id = int(found_id)
            else:
                contract_number = input("  Existing contract number: ").strip()
                contract_id = None
        else:
            if tracking:
                print(f"[REVISION] ✗ No contract found for tracking '{tracking}'")
            contract_number = input("  Existing contract number: ").strip()
            contract_id = None
        customer_id = None
        customer = lookup_customer(advertiser)
        separation = customer['separation'] if customer else WL_DEFAULT_SEPARATION
        print(f"[CUSTOMER] ✓ Revision — using existing contract {contract_number}")

        if order_type_str == 'revision_change':
            while True:
                raw = input("  First available date for changes (MM/DD/YYYY): ").strip()
                try:
                    first_available_date = datetime.strptime(raw, '%m/%d/%Y').date()
                    print(f"  ✓ Changes apply from {first_available_date}")
                    break
                except ValueError:
                    print("  Invalid date. Use MM/DD/YYYY.")
        else:
            first_available_date = None
    else:
        first_available_date = None
        # New order: look up or prompt for customer details
        customer = lookup_customer(advertiser)
        if customer:
            print(f"\n[CUSTOMER] ✓ Found: ID={customer['customer_id']}, "
                  f"Abbrev={customer['abbreviation']}")
            customer_id = customer['customer_id']
            abbreviation = customer['abbreviation']
            separation = customer['separation']
        else:
            print(f"\n[CUSTOMER] ✗ Not found: {advertiser}")
            print("Please enter customer details:")
            customer_id = input("  Customer ID: ").strip()
            abbreviation = input("  Abbreviation (e.g., Muck, Cross): ").strip()
            cust_sep = input("  Customer separation [5]: ").strip() or "5"
            order_sep = input("  Order separation [20]: ").strip() or "20"
            event_sep = input("  Event separation [0]: ").strip() or "0"
            separation = (int(cust_sep), int(order_sep), int(event_sep))
            save_new_customer(customer_id, advertiser, abbreviation, separation)

    billing = BillingType.CUSTOMER_SHARE_AGENCY
    print("\n[BILLING] ✓ Customer share indicating agency % / Agency")

    print("\n" + "="*70)
    print("INPUT COLLECTION COMPLETE - Ready for automation")
    print("="*70)

    return {
        'order_data': order_data,
        'customer_id': customer_id,
        'separation': separation,
        'billing': billing,
        'network': network,
        'contract_number': contract_number,      # COD_CONTRATTO string — Selenium navigates by this
        'contract_id': contract_id,              # ID_CONTRATTITESTATA integer — direct DB uses this
        'notes': _build_notes(order_data),
        'first_available_date': first_available_date,  # date or None; set for revision_change only
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DIRECT DB ENTRY
# ═══════════════════════════════════════════════════════════════════════════════

# Markets that receive a $0 line in Crossings direct entry.
# Order matches Etere UI entry: CMP=2, HOU=3, SFO=4, SEA=5, LAX=6, CVC=7, WDC=8, MMT=9
CROSSINGS_ZERO_MARKETS = ["CMP", "HOU", "SFO", "SEA", "LAX", "CVC", "WDC", "MMT"]


def _secs_to_duration(secs: int) -> str:
    """Convert integer seconds to HH:MM:SS:FF string for EtereDirectClient."""
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:00"


def _extend_end_date_direct(cursor, ph: str, contract_id: int, new_end) -> None:
    """Extend CONTRATTITESTATA.DATA_TERMINE if new lines go beyond current end."""
    cursor.execute(
        f"SELECT DATA_TERMINE FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = {ph}",
        (contract_id,)
    )
    row = cursor.fetchone()
    if not row:
        raise ValueError(f"Contract ID {contract_id} not found in CONTRATTITESTATA")
    current_end = row[0]
    current_date = current_end.date() if hasattr(current_end, 'date') else current_end
    if current_date is None or current_date < new_end:
        print(f"[DATES] Extending contract end: {current_date} → {new_end}")
        cursor.execute(
            f"UPDATE CONTRATTITESTATA SET DATA_TERMINE = {ph} "
            f"WHERE ID_CONTRATTITESTATA = {ph}",
            (new_end, contract_id)
        )
    else:
        print(f"[DATES] Contract end {current_date} already covers {new_end} — no update")


def _add_crossings_lines_direct(client, lines: list, separation: tuple, row_status: int = 0) -> None:
    """
    CROSSINGS direct entry: NYC at real rate + 8 $0 market lines per PDF line.
    All markets are entered individually via SP — no block refresh required.
    """
    for line in lines:
        days = line['days_of_week']
        rate = float(line['rate'])
        is_bonus = rate == 0.0
        from_time = line['from_time']
        to_time = line['to_time']
        time_range = f"{from_time}-{to_time}"
        duration = _secs_to_duration(int(line['duration']))
        spots_pw = int(line['spots'])
        total = int(line['total_spots'])
        date_from = _parse_date(line['start_date'])
        date_to = _parse_date(line['end_date'])
        label = "BNS " if is_bonus else ""
        time_short = _short_time_range(from_time, to_time)
        desc = f"(Line {line['line_number']}) {label}{days} {time_short}"

        print(f"\n  [LINE {line['line_number']}] {days} {line['time_range']} | "
              f"{line['duration']}s | {spots_pw}/wk | ${rate}"
              + (" [BNS]" if is_bonus else ""))

        booking = 10 if is_bonus else 2
        nyc_id = client.add_contract_line(
            market="NYC", days=days, time_range=time_range, description=desc,
            rate=rate, total_spots=total, spots_per_week=spots_pw,
            date_from=date_from, date_to=date_to, duration=duration,
            is_bonus=is_bonus, booking_code=booking, separation_intervals=separation,
            scheduling_type=0, row_status=row_status,
        )
        print(f"    NYC line_id={nyc_id}  rate=${rate}")

        for mkt in CROSSINGS_ZERO_MARKETS:
            mkt_id = client.add_contract_line(
                market=mkt, days=days, time_range=time_range, description=desc,
                rate=0.0, total_spots=total, spots_per_week=spots_pw,
                date_from=date_from, date_to=date_to, duration=duration,
                is_bonus=is_bonus, booking_code=booking, separation_intervals=separation,
                scheduling_type=0, row_status=row_status,
            )
            print(f"    {mkt} line_id={mkt_id}  rate=$0.00")


def _add_asian_lines_direct(client, lines: list, separation: tuple, row_status: int = 0) -> None:
    """ASIAN direct entry: single DAL market line per PDF line."""
    for line in lines:
        days = line['days_of_week']
        rate = float(line['rate'])
        is_bonus = rate == 0.0
        from_time = line['from_time']
        to_time = line['to_time']
        time_range = f"{from_time}-{to_time}"
        duration = _secs_to_duration(int(line['duration']))
        spots_pw = int(line['spots'])
        total = int(line['total_spots'])
        date_from = _parse_date(line['start_date'])
        date_to = _parse_date(line['end_date'])
        label = "BNS " if is_bonus else ""
        time_short = _short_time_range(from_time, to_time)
        desc = f"(Line {line['line_number']}) {label}{days} {time_short}"

        print(f"\n  [LINE {line['line_number']}] {days} {line['time_range']} | "
              f"{line['duration']}s | {spots_pw}/wk | ${rate}"
              + (" [BNS]" if is_bonus else ""))

        dal_id = client.add_contract_line(
            market="DAL", days=days, time_range=time_range, description=desc,
            rate=rate, total_spots=total, spots_per_week=spots_pw,
            date_from=date_from, date_to=date_to, duration=duration,
            is_bonus=is_bonus, booking_code=10 if is_bonus else 2,
            separation_intervals=separation, scheduling_type=0, row_status=row_status,
        )
        print(f"    DAL line_id={dal_id}  rate=${rate}")


def _line_exists_in_etere(conn, ph: str, contract_id: int, wl_line_number: int) -> bool:
    """Return True if any CONTRATTIRIGHE row already exists for this WL line number."""
    cur = conn.cursor()
    cur.execute(
        f"SELECT COUNT(*) FROM CONTRATTIRIGHE "
        f"WHERE ID_CONTRATTITESTATA = {ph} AND DESCRIZIONE LIKE {ph}",
        (contract_id, f'(Line {wl_line_number})%')
    )
    return (cur.fetchone()[0] or 0) > 0


def _find_wl_line_ids(conn, ph: str, contract_id: int, wl_line_number: int) -> list:
    """Return list of (ID_CONTRATTIRIGHE, COD_USER) for a WL line number across all markets."""
    cur = conn.cursor()
    cur.execute(
        f"SELECT ID_CONTRATTIRIGHE, COD_USER FROM CONTRATTIRIGHE "
        f"WHERE ID_CONTRATTITESTATA = {ph} AND DESCRIZIONE LIKE {ph}",
        (contract_id, f'(Line {wl_line_number})%')
    )
    return cur.fetchall()


def _count_spots(conn, ph: str, line_ids: list, first_available_date,
                 paid_line_id: int) -> tuple:
    """
    Count locked and removable spots. No deletes.

    locked          — spots before cutoff on paid market line only (for remaining math).
    removable_paid  — spots on/after cutoff on paid market line only (for display).
    removable_total — spots on/after cutoff across ALL market lines (actual delete scope).
    """
    cur = conn.cursor()
    cur.execute(
        f"SELECT COUNT(*) FROM trafficPalinse "
        f"WHERE ID_ContrattiRighe = {ph} AND Date < {ph}",
        (paid_line_id, first_available_date)
    )
    locked = cur.fetchone()[0] or 0

    cur.execute(
        f"SELECT COUNT(*) FROM trafficPalinse "
        f"WHERE ID_ContrattiRighe = {ph} AND Date >= {ph}",
        (paid_line_id, first_available_date)
    )
    removable_paid = cur.fetchone()[0] or 0

    id_ph = ','.join([ph] * len(line_ids))
    cur.execute(
        f"SELECT COUNT(*) FROM trafficPalinse "
        f"WHERE ID_ContrattiRighe IN ({id_ph}) AND Date >= {ph}",
        (*line_ids, first_available_date)
    )
    removable_total = cur.fetchone()[0] or 0
    return locked, removable_paid, removable_total


def _unschedule_spots(conn, ph: str, line_ids: list, first_available_date) -> int:
    """Delete trafficPalinse rows on or after cutoff. Returns deleted count."""
    id_ph = ','.join([ph] * len(line_ids))
    cur = conn.cursor()
    cur.execute(
        f"DELETE FROM trafficPalinse "
        f"WHERE ID_ContrattiRighe IN ({id_ph}) AND Date >= {ph}",
        (*line_ids, first_available_date)
    )
    return cur.rowcount


def _count_remaining_days(first_available_date: date, new_end_date: date, day_bits: dict) -> int:
    """Count weekdays in [first_available_date, new_end_date] that match the day pattern."""
    keys = ['lun', 'mar', 'mer', 'gio', 'ven', 'sab', 'dom']
    count = 0
    current = first_available_date
    while current <= new_end_date:
        if day_bits[keys[current.weekday()]]:
            count += 1
        current += timedelta(days=1)
    return count


def _query_current_line_params(conn, ph: str, line_id: int) -> Optional[dict]:
    """Query end date, total spots, rate, and day flags from a CONTRATTIRIGHE row."""
    cur = conn.cursor()
    cur.execute(
        f"SELECT DATA_FINE, N_PASSAGGI, IMPORTO, "
        f"LUNEDI, MARTEDI, MERCOLEDI, GIOVEDI, VENERDI, SABATO, DOMENICA "
        f"FROM CONTRATTIRIGHE WHERE ID_CONTRATTIRIGHE = {ph}",
        (line_id,)
    )
    row = cur.fetchone()
    if not row:
        return None
    end = row[0]
    if hasattr(end, 'date'):
        end = end.date()
    return {
        'end_date':   end,
        'total_spots': int(row[1]) if row[1] is not None else 0,
        'rate':        float(row[2]) if row[2] is not None else 0.0,
        'day_bits':    tuple(int(v or 0) for v in row[3:10]),
    }


def _day_bits_to_display(bits_tuple: tuple) -> str:
    """Convert (mon, tue, wed, thu, fri, sat, sun) flags to a short display string."""
    names = ['M', 'Tu', 'W', 'Th', 'F', 'Sa', 'Su']
    active = [i for i, v in enumerate(bits_tuple) if v]
    if not active:
        return '—'
    if len(active) == 1:
        return names[active[0]]
    if active == list(range(active[0], active[-1] + 1)):
        return f"{names[active[0]]}-{names[active[-1]]}"
    return '/'.join(names[i] for i in active)


def _update_change_lines(
    conn, ph: str, lines_with_market: list, io_line: dict,
    locked_count: int, is_cancel: bool, remaining_days: int,
    paid_market_id: int = 1,
) -> None:
    """
    UPDATE CONTRATTIRIGHE rows to match the IO for a CHANGE or CANCEL line.

    Updates: end date, total spots, day flags, ROWSTATUS.
    Also updates max/day (PASSAGGI_GIORNALIERI) when spots still need placement.
    Does NOT touch: start date, times, rate, weekly cap.

    ROWSTATUS: 1 if no spots remain to place, else 0 (Ready) so the line passes
    straight to the scheduler — revisions no longer use 2 (Change Data).
    """
    from browser_automation.etere_direct_client import parse_day_bits

    end_date_obj = _parse_date(io_line['end_date'])
    # Pass as ISO string — avoids pymssql datetime binding ambiguity with some columns
    end_dt = end_date_obj.strftime('%Y-%m-%d')

    n_passaggi = locked_count if is_cancel else int(io_line['total_spots'])
    remaining  = n_passaggi - locked_count
    rowstatus  = 1 if remaining == 0 else 0
    day_bits   = parse_day_bits(io_line['days_of_week'])

    new_max_daily = (math.ceil(remaining / remaining_days)
                     if remaining > 0 and remaining_days > 0 else None)

    extra_col = f"PASSAGGI_GIORNALIERI = {ph}, " if new_max_daily is not None else ""
    extra_val = (new_max_daily,) if new_max_daily is not None else ()

    # Build updated description: preserve "(Line N)" prefix, update days
    wl_num     = io_line['line_number']
    is_bonus   = float(io_line['rate']) == 0.0
    label      = "BNS " if is_bonus else ""
    time_short = _short_time_range(io_line['from_time'], io_line['to_time'])
    new_desc   = f"(Line {wl_num}) {label}{io_line['days_of_week']} {time_short}"

    cur = conn.cursor()
    for line_id, _ in lines_with_market:
        cur.execute(
            f"UPDATE CONTRATTIRIGHE SET "
            f"DATA_FINE = {ph}, DATEEND = {ph}, N_PASSAGGI = {ph}, "
            f"LUNEDI = {ph}, MARTEDI = {ph}, MERCOLEDI = {ph}, "
            f"GIOVEDI = {ph}, VENERDI = {ph}, SABATO = {ph}, DOMENICA = {ph}, "
            f"DESCRIZIONE = {ph}, "
            f"{extra_col}ROWSTATUS = {ph} "
            f"WHERE ID_CONTRATTIRIGHE = {ph}",
            (
                end_dt, end_dt, n_passaggi,
                1 if day_bits['lun'] else 0,
                1 if day_bits['mar'] else 0,
                1 if day_bits['mer'] else 0,
                1 if day_bits['gio'] else 0,
                1 if day_bits['ven'] else 0,
                1 if day_bits['sab'] else 0,
                1 if day_bits['dom'] else 0,
                new_desc,
                *extra_val,
                rowstatus,
                line_id,
            )
        )



def process_worldlink_order_direct(user_input: dict) -> Optional[str]:
    """
    Enter a WorldLink order directly via DB stored procedures (no browser).

    Returns COD_CONTRATTO string on success, None on failure (rolls back fully).
    Crossings TV: enters all 9 markets explicitly — block refresh not required.
    """
    from browser_automation.etere_direct_client import EtereDirectClient
    from browser_automation.etere_direct_client import connect as db_connect

    order_data = user_input['order_data']
    network = user_input['network']
    lines = order_data['lines']
    order_type_str = order_data.get('order_type', 'new')
    separation = user_input['separation']
    is_revision = order_type_str != 'new'
    master_market = "DAL" if network == "ASIAN" else "NYC"

    print("\n" + "="*70)
    print(f"WORLDLINK DIRECT DB ENTRY — {network} / {order_type_str.upper()}")
    print("="*70)

    flight_start = min(_parse_date(ln['start_date']) for ln in lines)
    flight_end   = max(_parse_date(ln['end_date'])   for ln in lines)

    conn = None
    try:
        conn = db_connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market(master_market)
        ph = client._ph

        if is_revision:
            contract_id = user_input.get('contract_id')
            if contract_id is None:
                # contract_number is the code — look up integer ID
                cur = conn.cursor()
                cur.execute(
                    f"SELECT TOP 1 ID_CONTRATTITESTATA FROM CONTRATTITESTATA "
                    f"WHERE COD_CONTRATTO = {ph}",
                    (user_input['contract_number'],)
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"Contract '{user_input['contract_number']}' not found in DB")
                contract_id = int(row[0])
            else:
                contract_id = int(contract_id)

            cur = conn.cursor()
            _extend_end_date_direct(cur, ph, contract_id, flight_end)
            client._contract_id = contract_id

            cur.execute(
                f"SELECT COMMITTENTE FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = {ph}",
                (contract_id,)
            )
            row = cur.fetchone()
            customer_id = int(row[0]) if row else None
            print(f"[REVISION] Adding lines to existing contract #{contract_id}")

            # Safety check: catch ADD lines that may be mislabelled (agency typo).
            # For any ADD line with line_number > 1, verify it doesn't already exist.
            for io_line in lines:
                action = (io_line.get('action') or 'ADD').upper()
                wl_num = io_line['line_number']
                if action != 'ADD' or wl_num <= 1:
                    continue
                if _line_exists_in_etere(conn, ph, contract_id, wl_num):
                    print(f"\n  ⚠ [Line {wl_num}] is marked ADD but already exists on contract {contract_id}.")
                    ans = input(f"  Treat Line {wl_num} as CHANGE instead? [y/n]: ").strip().lower()
                    if ans == 'y':
                        io_line['action'] = 'CHANGE'
                        print(f"  [Line {wl_num}] → action overridden to CHANGE")
        else:
            customer_id = int(user_input['customer_id'])
            contract_id = client.create_contract_header(
                code=order_data['order_code'],
                description=order_data['description'],
                customer_id=customer_id,
                agency_id=None,
                media_center_id=None,
                contract_date=flight_start,
                contract_end_date=flight_end,
                contract_type=1,
                billing_type="agency",
                master_market=master_market,
                note=user_input['notes'],
                customer_order_ref=order_data.get('tracking_number', ''),
            )
            print(f"[CONTRACT] ✓ ID={contract_id}")

        if customer_id:
            wl_defaults = client.get_client_defaults(customer_id)
            if wl_defaults.get("nielsen_id"):
                client._nielsen_id   = wl_defaults["nielsen_id"]
                client._nielsen_code = wl_defaults["nielsen_code"]
                print(f"[DB] Nielsen → id={client._nielsen_id}  code={client._nielsen_code}")

        # All WorldLink lines use ROWSTATUS=0 (Ready) so they pass straight to the
        # scheduler. New orders are gated by the Proposal contract type (we still
        # monitor those). Revision/change lines go directly to the scheduler — no
        # ROWSTATUS=2 (Change Data) / manual re-approval step.
        line_row_status = 0

        print(f"[LINES] Processing {len(lines)} PDF line(s) ({order_type_str.upper()})...")
        if order_type_str == 'revision_change':
            from browser_automation.etere_direct_client import parse_day_bits
            first_available = user_input['first_available_date']
            paid_market_id  = 10 if network == "ASIAN" else 1
            doc_str         = f"{first_available.month}/{first_available.day}"
            add_lines = []

            for io_line in lines:
                action  = (io_line.get('action') or 'ADD').upper()
                wl_num  = io_line['line_number']

                if action in ('CHANGE', 'CANCEL'):
                    etere_lines = _find_wl_line_ids(conn, ph, contract_id, wl_num)
                    if not etere_lines:
                        print(f"\n  [Line {wl_num}] {action} ✗ No matching Etere lines — skipped")
                        continue

                    line_ids = [r[0] for r in etere_lines]

                    # Paid market row drives spot counts and current-param display
                    paid_row = next((r for r in etere_lines if r[1] == paid_market_id),
                                    etere_lines[0])
                    locked, removable_paid, removable_total = _count_spots(
                        conn, ph, line_ids, first_available, paid_line_id=paid_row[0]
                    )
                    current = _query_current_line_params(conn, ph, paid_row[0])

                    n_io      = locked if action == 'CANCEL' else int(io_line['total_spots'])
                    remaining = n_io - locked
                    new_end   = _parse_date(io_line['end_date'])
                    day_bits  = parse_day_bits(io_line['days_of_week'])
                    rem_days  = _count_remaining_days(first_available, new_end, day_bits)
                    new_max_daily = (math.ceil(remaining / rem_days)
                                     if remaining > 0 and rem_days > 0 else None)

                    # Format display strings
                    def _fmt_date(d):
                        return f"{d.month}/{d.day}/{d.year}" if d else '?'

                    cur_end_str  = _fmt_date(current['end_date']) if current else '?'
                    new_end_str  = _fmt_date(new_end)
                    cur_days_str = _day_bits_to_display(current['day_bits']) if current else '?'
                    new_days_str = _day_bits_to_display(
                        tuple(1 if day_bits[k] else 0
                              for k in ['lun','mar','mer','gio','ven','sab','dom'])
                    )
                    cur_spots = current['total_spots'] if current else '?'
                    cur_rate  = current['rate'] if current else 0.0

                    print(f"\n  [Line {wl_num}]  {action}")
                    print(f"    End date:     {cur_end_str} → {new_end_str}")
                    days_note = "(unchanged)" if cur_days_str == new_days_str else f"→ {new_days_str}"
                    print(f"    Days:         {cur_days_str} {days_note}")
                    if action == 'CANCEL':
                        print(f"    Total spots:  {cur_spots} → 0  (CANCEL)")
                    elif cur_spots == n_io:
                        print(f"    Total spots:  {n_io} (unchanged)")
                    else:
                        print(f"    Total spots:  {cur_spots} → {n_io}")
                    print(f"    Rate:         ${cur_rate:.2f}  (update manually if changed)")
                    print()
                    total_note = (f"  ({removable_total} total across all markets)"
                                  if removable_total != removable_paid else "")
                    print(f"    Locked before {doc_str}:  {locked} spot{'s' if locked != 1 else ''}")
                    print(f"    Unschedule {doc_str}+:     {removable_paid} spot{'s' if removable_paid != 1 else ''}{total_note}")
                    if remaining == 0:
                        print("    Remaining to place:  0 — no rescheduling needed")
                        print("    Status → Scheduled (no re-approval needed)")
                    else:
                        max_note = f" → new max/day: {new_max_daily}" if new_max_daily else ""
                        print(f"    Remaining to place:  {remaining} on {rem_days} "
                              f"day{'s' if rem_days != 1 else ''}{max_note}")
                        print("    Status → Ready — sent straight to scheduler")

                    if action != 'CANCEL' and locked > n_io:
                        print(f"\n    ⚠ CONFLICT — IO orders {n_io} spots but {locked} are already locked.")
                        print("    Cannot proceed. Manual correction required.")
                        continue

                    answer = input("\n  Apply? [y/n]: ").strip().lower()
                    if answer != 'y':
                        print(f"  [Line {wl_num}] Skipped.")
                        continue

                    removed = _unschedule_spots(conn, ph, line_ids, first_available)
                    _update_change_lines(conn, ph, etere_lines, io_line, locked,
                                         is_cancel=(action == 'CANCEL'),
                                         remaining_days=rem_days,
                                         paid_market_id=paid_market_id)
                    status_str = "Scheduled" if remaining == 0 else "Ready (sent to scheduler)"
                    print(f"  [Line {wl_num}] ✓ Applied — {removed} spot(s) unscheduled across all markets, "
                          f"status: {status_str}")

                elif action == 'ADD':
                    add_lines.append(io_line)

            if add_lines:
                print(f"\n[LINES] Adding {len(add_lines)} new ADD line(s)...")
                if network == "ASIAN":
                    _add_asian_lines_direct(client, add_lines, separation, row_status=0)
                else:
                    _add_crossings_lines_direct(client, add_lines, separation, row_status=0)
        else:
            # new and revision_add: add all lines
            if network == "ASIAN":
                _add_asian_lines_direct(client, lines, separation, row_status=line_row_status)
            else:
                _add_crossings_lines_direct(client, lines, separation, row_status=line_row_status)

        conn.commit()

        cur = conn.cursor()
        cur.execute(
            f"SELECT COD_CONTRATTO FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = {ph}",
            (contract_id,)
        )
        row = cur.fetchone()
        contract_code = str(row[0]).strip() if row else str(contract_id)
        conn.close()
        print(f"\n[DIRECT] ✓ Contract {contract_code} committed.")
        if is_revision:
            _session_revised.append(contract_code)
        return contract_code

    except Exception as exc:
        print(f"\n[DIRECT] ✗ Error: {exc}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# BLOCK REFRESH (direct DB — replaces Selenium perform_block_refresh)
# ═══════════════════════════════════════════════════════════════════════════════

def _perform_block_refresh_direct(
    etere: EtereClient,
    contract_number: str,
    only_lines_above=None,
) -> bool:
    """
    Assign blocks for Crossings TV lines via direct DB instead of Selenium.

    Uses etere.get_all_line_ids_with_numbers() to enumerate line IDs (same
    filtering as perform_block_refresh), then reads each line's parameters
    from CONTRATTIRIGHE and inserts into CONTRATTIFASCE in one SQL call per
    line — no browser navigation or 8-second waits required.
    """
    from browser_automation.etere_direct_client import EtereDirectClient
    from browser_automation.etere_direct_client import connect as db_connect

    print(f"\n{'='*60}")
    print(f"BLOCK REFRESH (direct DB): Contract {contract_number}")
    if only_lines_above is not None:
        print(f"Filter: lines > {only_lines_above} only")
    print(f"{'='*60}")

    with db_connect() as conn:
        direct = EtereDirectClient(conn)
        all_ids = direct.get_all_line_ids(contract_number)

    if not all_ids:
        print("[REFRESH] ✗ No lines found in DB")
        return False

    if only_lines_above is not None:
        all_ids = [lid for lid in all_ids if lid > only_lines_above]
        if not all_ids:
            print("[REFRESH] ✓ No new lines to refresh")
            return True

    print(f"[REFRESH] Assigning blocks for {len(all_ids)} lines via direct DB...")
    ok_count = 0
    with db_connect() as conn:
        direct = EtereDirectClient(conn)
        for idx, line_id in enumerate(all_ids, 1):
            print(f"[REFRESH] {idx}/{len(all_ids)}: ID {line_id}")
            count = direct.assign_blocks_for_existing_line(line_id)
            if count >= 0:
                ok_count += 1
                print(f"[REFRESH] ✓ {count} block(s)")
            else:
                print("[REFRESH] ✗ failed (see above)")

    print(f"\n[REFRESH] Complete — {ok_count}/{len(all_ids)} succeeded")
    return ok_count == len(all_ids)


# ═══════════════════════════════════════════════════════════════════════════════
# LINE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _add_crossings_lines(
    etere: EtereClient,
    contract_number: str,
    lines: list,
    separation: tuple
) -> bool:
    """
    Add Crossings TV lines: NYC line at real rate + CMP line at $0 per PDF line.

    The CMP $0 line is replicated to other markets via block refresh after processing.
    """
    all_success = True
    for line in lines:
        days = line['days_of_week']
        days, _ = EtereClient.check_sunday_6_7a_rule(days, line['time_range'])
        time_from = line['from_time']
        time_to = line['to_time']
        rate = float(line['rate'])
        is_bonus = rate == 0.0
        spot_code = 10 if is_bonus else 2
        time_short = _short_time_range(line['from_time'], line['to_time'])
        label = "BNS " if is_bonus else ""
        desc = f"(Line {line['line_number']}) {label}{days} {time_short}"
        duration = _duration_to_seconds(line['duration'])

        print(f"\n[LINE {line['line_number']}] {days} {line['time_range']} | "
              f"{duration}s | {line['spots']}/wk | ${rate}"
              + (" [BNS]" if is_bonus else ""))

        # NYC line: actual rate
        ok = etere.add_contract_line(
            contract_number=contract_number, market="NYC",
            start_date=line['start_date'], end_date=line['end_date'],
            days=days, time_from=time_from, time_to=time_to,
            description=desc, spot_code=spot_code, duration_seconds=duration,
            total_spots=line['total_spots'], spots_per_week=line['spots'],
            rate=rate, separation_intervals=separation,
        )
        if not ok:
            print("  ✗ NYC line failed")
            all_success = False

        # CMP line: $0 — replicated to other markets via Options tab selection
        ok = etere.add_contract_line(
            contract_number=contract_number, market="CMP",
            start_date=line['start_date'], end_date=line['end_date'],
            days=days, time_from=time_from, time_to=time_to,
            description=desc, spot_code=spot_code, duration_seconds=duration,
            total_spots=line['total_spots'], spots_per_week=line['spots'],
            rate=0.0, separation_intervals=separation,
            other_markets=["CVC", "SFO", "LAX", "SEA", "HOU", "WDC", "MMT"],
        )
        if not ok:
            print("  ✗ CMP line failed")
            all_success = False

    return all_success


def _add_asian_lines(
    etere: EtereClient,
    contract_number: str,
    lines: list,
    separation: tuple
) -> bool:
    """Add Asian Channel (TAC) lines: single DAL market per PDF line."""
    all_success = True
    for line in lines:
        days = line['days_of_week']
        days, _ = EtereClient.check_sunday_6_7a_rule(days, line['time_range'])
        rate = float(line['rate'])
        is_bonus = rate == 0.0
        spot_code = 10 if is_bonus else 2
        time_short = _short_time_range(line['from_time'], line['to_time'])
        label = "BNS " if is_bonus else ""
        desc = f"(Line {line['line_number']}) {label}{days} {time_short}"
        print(f"\n[LINE {line['line_number']}] {days} {line['time_range']} | "
              f"{_duration_to_seconds(line['duration'])}s | {line['spots']}/wk | ${rate}"
              + (" [BNS]" if is_bonus else ""))
        ok = etere.add_contract_line(
            contract_number=contract_number, market="DAL",
            start_date=line['start_date'], end_date=line['end_date'],
            days=days, time_from=line['from_time'], time_to=line['to_time'],
            description=desc, spot_code=spot_code,
            duration_seconds=_duration_to_seconds(line['duration']),
            total_spots=line['total_spots'], spots_per_week=line['spots'],
            rate=rate, separation_intervals=separation,
        )
        if not ok:
            print(f"  ✗ DAL line {line['line_number']} failed")
            all_success = False
    return all_success

