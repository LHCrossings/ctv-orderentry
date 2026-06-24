"""
BVK Order Automation

Handles browser automation for BVK broadcast orders (e.g. UCD Health, etc.).

Business Rules:
- Market: from PDF header (typically Sacramento → CVC)
- Rates: GROSS from PDF — no gross-up needed
- Separation: PDF says 30 min → enter as (25, 0, 0) per lessons rule
- Bonus lines ($0.00): spot_code=10
- Paid lines: spot_code=2
- Master market: NYC (standard Crossings TV)
- "Revision" header / Version field are BVK's internal — always create NEW contract
"""

import os
import sys
from pathlib import Path
from typing import Optional

_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
from browser_automation.etere_client import EtereClient
from browser_automation.parsers.bvk_parser import BVKOrder, parse_bvk_pdf
from src.domain.enums import OrderType

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


def _create_bvk_contract_direct(order: 'BVKOrder', inputs: dict) -> Optional[int]:
    """Enter BVK order directly via DB stored procedures (no browser)."""
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = inputs.get('customer_id')
    if customer_id is None:
        print("[BVK DIRECT] ✗ No customer_id — cannot enter without a known ID")
        return None

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
            note=inputs['notes'],
            customer_order_ref=inputs['order_ref'],
            allow_rename=True,
        )
        if not contract_id:
            print("[BVK DIRECT] ✗ Failed to create contract header")
            return None
        print(f"[BVK DIRECT] ✓ Contract ID={contract_id}")

        separation = inputs.get('separation', (25, 0, 0))
        line_count = 0

        for line in order.lines:
            if line.total_spots == 0:
                continue

            is_bonus     = line.is_bonus
            booking_code = 10 if is_bonus else 2
            description  = line.get_description()
            time_from, time_to = EtereClient.parse_time_range(line.time_str)
            adjusted_days, _   = EtereClient.check_sunday_6_7a_rule(line.days, line.time_str)

            ranges = EtereClient.consolidate_weeks(
                line.weekly_spots,
                order.week_dates,
                flight_end=order.flight_end,
            )

            for rng in ranges:
                line_count  += 1
                total_spots  = rng['spots_per_week'] * rng['weeks']
                print(f"  [LINE {line_count}] {description}: "
                      f"{rng['start_date']}–{rng['end_date']} "
                      f"({rng['spots_per_week']}/wk×{rng['weeks']}={total_spots})")
                client.add_contract_line(
                    market=order.market,
                    days=adjusted_days,
                    time_range=f"{time_from}-{time_to}",
                    description=description,
                    rate=float(line.gross_rate),
                    total_spots=total_spots,
                    spots_per_week=rng['spots_per_week'],
                    date_from=_parse_date(rng['start_date']),
                    date_to=_parse_date(rng['end_date']),
                    duration=_secs_to_duration(line.duration),
                    is_bonus=is_bonus,
                    booking_code=booking_code,
                    separation_intervals=separation,
                )

        conn.commit()
        conn.close()
        print(f"[BVK DIRECT] ✓ {line_count} line(s) entered")
        return contract_id

    except Exception as exc:
        print(f"[BVK DIRECT] ✗ {exc}")
        import traceback; traceback.print_exc()
        if conn:
            try: conn.rollback(); conn.close()
            except: pass
        return None


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
            repo.find_by_name(client_name, OrderType.BVK) or
            repo.find_by_name_fuzzy(client_name, OrderType.BVK)
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


def _save_customer(customer_id: str, client_name: str, separation: tuple) -> None:
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        customer = Customer(
            customer_id=customer_id,
            customer_name=client_name,
            order_type=OrderType.BVK,
            billing_type="agency",
            separation_customer=separation[0],
            separation_event=separation[1],
            separation_order=separation[2],
        )
        repo.save(customer)
        print(f"[CUSTOMER DB] ✓ Saved: {client_name} → ID {customer_id}")
    except Exception as exc:
        print(f"[CUSTOMER DB] ✗ Save failed: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# TIME VALIDATION AND ROS BONUS REVIEW
# ─────────────────────────────────────────────────────────────────────────────

_LANG_NAMES = {
    'C': 'Cantonese', 'M': 'Mandarin', 'V': 'Vietnamese', 'K': 'Korean',
    'SA': 'South Asian', 'P': 'Punjabi', 'Hm': 'Hmong', 'T': 'Filipino',
    'J': 'Japanese',
}

# Expected paid-spot daypart windows per language (minutes from midnight)
_LANG_WINDOWS = {
    'C':  (18*60,  24*60),  # Cantonese:   6p–12a
    'M':  (18*60,  24*60),  # Mandarin:    6p–12a
    'V':  (10*60,  13*60),  # Vietnamese:  10a–1p
    'K':  ( 8*60,  10*60),  # Korean:      8a–10a
    'SA': (13*60,  16*60),  # South Asian: 1p–4p
    'P':  (13*60,  16*60),  # Punjabi:     1p–4p
    'Hm': (18*60,  20*60),  # Hmong:       6p–8p (Sa-Su)
    'T':  (16*60,  19*60),  # Filipino:    4p–7p
    'J':  (10*60,  11*60),  # Japanese:    10a–11a
}

# Correct ROS schedule times per language (from CTV language schedule)
_LANG_ROS_TIMES = {
    'C':  '6:00A-11:59P',
    'M':  '6:00A-11:59P',
    'V':  '10:00A-1:00P',
    'K':  '8:00A-10:00A',
    'SA': '1:00P-4:00P',
    'P':  '1:00P-4:00P',
    'Hm': '6:00P-8:00P',
    'T':  '4:00P-7:00P',
    'J':  '10:00A-11:00A',
}

# Correct ROS days per language (from CTV language schedule)
_LANG_ROS_DAYS = {
    'C':  'M-F',
    'M':  'M-Su',
    'V':  'M-Su',
    'K':  'M-Su',
    'SA': 'M-Su',
    'P':  'M-F',
    'Hm': 'Sa-Su',
    'T':  'M-Su',
    'J':  'M-Su',
}

# Program name keywords that indicate the language is already explicit
_EXPLICIT_LANG_KEYWORDS = [
    'cantonese', 'mandarin', 'chinese', 'vietnamese', 'korean',
    'hindi', 'hinid', 'punjabi', 'hmong', 'filipino', 'japanese', 'jananese',
    'south asian',
]


def _parse_time_minutes(t: str) -> Optional[int]:
    """Convert '11:30A' or '11:30P' to minutes from midnight. 12:00A → 1440."""
    t = t.strip().upper()
    if not t or t[-1] not in 'AP':
        return None
    try:
        h, m = map(int, t[:-1].split(':'))
    except ValueError:
        return None
    if t[-1] == 'A':
        return (24 if h == 12 else h) * 60 + m  # 12:xxA = midnight
    else:
        return (h if h == 12 else h + 12) * 60 + m


def _time_outside_window(time_str: str, lang: str) -> bool:
    window = _LANG_WINDOWS.get(lang)
    if not window:
        return False
    parts = time_str.split('-', 1)
    if len(parts) != 2:
        return False
    t_from = _parse_time_minutes(parts[0])
    t_to   = _parse_time_minutes(parts[1])
    if t_from is None or t_to is None:
        return False
    return t_from < window[0] or t_to > window[1]


def _interactive_time_check(order) -> bool:
    """
    Interactively validate paid-line times and review ROS bonus lines.

    Paid lines: flags times outside the expected language window and asks
    the user for a correction.

    Bonus lines: confirms the inferred language (for generic 'ROS Bonus'
    lines) and offers to apply the correct ROS schedule time from our tables.

    Mutates line objects in place. Returns False if the user cancels.
    """
    # ── Paid line time validation ─────────────────────────────────────────────
    flagged_paid = [
        l for l in order.lines
        if not l.is_bonus
        and l.total_spots > 0
        and _time_outside_window(l.time_str, l.language)
    ]

    if flagged_paid:
        print("\n⚠ TIME RANGE WARNINGS")
        print("-" * 70)
        print("  The following paid lines have times outside the expected window.")
        print("  This usually means a typo on the BVK IO.\n")
        for line in flagged_paid:
            lang = _LANG_NAMES.get(line.language, line.language)
            win  = _LANG_WINDOWS[line.language]
            win_str = (
                f"{win[0]//60}:{win[0]%60:02d}{'a' if win[0] < 12*60 else 'p'}–"
                f"{win[1]//60 % 12 or 12}:{win[1]%60:02d}{'a' if win[1] <= 12*60 else 'p'}"
            )
            print(f"  Line {line.line_no:2d} [{lang}]  PDF: '{line.time_str}'  expected window: {win_str}")
            resp = input("  Corrected time (or Enter to keep as-is): ").strip()
            if resp:
                line.time_str = resp
                print(f"  ✓ Updated to: {line.time_str}")
        print()

    # ── Bonus line language + ROS time review ────────────────────────────────
    active_bonus = [l for l in order.lines if l.is_bonus and l.total_spots > 0]

    if active_bonus:
        print("[BONUS LINE REVIEW] Confirming language and ROS schedule times")
        print("-" * 70)

        for line in active_bonus:
            lang     = line.language
            lang_name = _LANG_NAMES.get(lang, lang)
            ros_time  = _LANG_ROS_TIMES.get(lang, '')
            ros_days  = _LANG_ROS_DAYS.get(lang, '')

            # Is the language inferred from context, or explicit in the program name?
            prog_lower = line.program.lower()
            is_inferred = not any(kw in prog_lower for kw in _EXPLICIT_LANG_KEYWORDS)
            label = 'Inferred' if is_inferred else 'Detected'

            print(f"\n  Line {line.line_no:2d}: {label} {lang_name} ({lang})")
            print(f"           Program:  {line.program}")
            print(f"           PDF days: {line.days}  PDF time: {line.time_str}")

            # For inferred language, let user confirm or override
            if is_inferred:
                resp = input(
                    f"  Confirm language [{lang_name}] or enter code"
                    f" (C/M/V/K/SA/P/Hm/T/J): "
                ).strip()
                if resp:
                    line.language = resp
                    lang_name = _LANG_NAMES.get(resp, resp)
                    ros_time  = _LANG_ROS_TIMES.get(resp, '')
                    ros_days  = _LANG_ROS_DAYS.get(resp, '')

            # Offer to apply the correct ROS schedule days
            if ros_days and ros_days != line.days:
                print(f"  ⚠ Days mismatch — PDF: '{line.days}'  ROS table: '{ros_days}'")
                resp = input(
                    f"  Apply ROS days '{ros_days}'? (y/n or type custom): "
                ).strip()
                if resp.lower() == 'y':
                    line.days = ros_days
                    print(f"  ✓ Days set to: {line.days}")
                elif resp and resp.lower() != 'n':
                    line.days = resp
                    print(f"  ✓ Days set to: {line.days}")

            # Offer to apply the correct ROS schedule time
            if ros_time:
                print(f"           ROS time: {ros_time}")
                resp = input(
                    "  Apply ROS time? (y/n or type custom): "
                ).strip()
                if resp.lower() == 'y':
                    line.time_str = ros_time
                    print(f"  ✓ Set to: {line.time_str}")
                elif resp and resp.lower() != 'n':
                    line.time_str = resp
                    print(f"  ✓ Set to: {line.time_str}")
            else:
                resp = input(f"  Enter correct time (or Enter to keep '{line.time_str}'): ").strip()
                if resp:
                    line.time_str = resp
                    print(f"  ✓ Set to: {line.time_str}")

        print()

    return True


# ─────────────────────────────────────────────────────────────────────────────
# INPUT GATHERING
# ─────────────────────────────────────────────────────────────────────────────

def gather_bvk_inputs(pdf_path: str) -> Optional[dict]:
    """
    Parse PDF and gather all user inputs before the browser session opens.

    Returns:
        Dict with keys: contract_code, description, notes, order_ref,
        customer_id, separation, parsed_order.
        Returns None if the user cancels.
    """
    print("\n" + "=" * 70)
    print("BVK ORDER - INPUT COLLECTION")
    print("=" * 70)

    print("\n[PARSE] Reading PDF…")
    try:
        order = parse_bvk_pdf(pdf_path)
    except Exception as exc:
        print(f"[PARSE] ✗ Failed: {exc}")
        return None

    if not _interactive_time_check(order):
        return None

    active_lines = [l for l in order.lines if l.total_spots > 0]

    print(f"\nClient:      {order.client}")
    print(f"Product:     {order.product}")
    print(f"Description: {order.description}")
    print(f"Market:      {order.market}")
    print(f"Flight:      {order.flight_start} – {order.flight_end}")
    print(f"CPE:         {order.estimate}")
    print(f"Separation:  {order.separation_min} min → will enter as 25")
    print(f"Lines:       {len(order.lines)} ({len(active_lines)} with spots)")
    print(f"Total spots: {sum(l.total_spots for l in order.lines)}")
    print()

    # ── Separation (30 min PDF → 25 per lessons rule) ─────────────────────────
    pdf_sep = order.separation_min
    separation_min = 25 if pdf_sep == 30 else pdf_sep
    separation = (separation_min, 0, 0)

    # ── Customer lookup ──────────────────────────────────────────────────────
    customer_info = _lookup_customer(order.client)
    customer_id: Optional[int] = None

    if customer_info:
        customer_id = customer_info['customer_id']
        raw_sep    = customer_info['separation']
        separation = (25 if raw_sep[0] == 30 else raw_sep[0], raw_sep[1], raw_sep[2])
        print(f"[CUSTOMER] ✓ Found: {order.client} → ID {customer_id}")
        print(f"[CUSTOMER] Separation: {separation}")
    else:
        print(f"[CUSTOMER] Not found in DB for '{order.client}'")
        raw_id = input("  Enter Etere customer ID (or blank to select in browser): ").strip()
        if raw_id.isdigit():
            customer_id = int(raw_id)
            if input(f"  Save '{order.client}' (ID {customer_id}) to DB? (y/n): ").strip().lower() == 'y':
                _save_customer(str(customer_id), order.client, separation)
    print()

    # ── Contract code / description / notes ───────────────────────────────────
    if customer_info:
        default_code = order.get_default_contract_code(
            customer_info['code_name'],
            include_market=customer_info['include_market'],
        )
    else:
        default_code = order.get_default_contract_code()
    if customer_info and customer_info.get('description_name'):
        default_desc = order.get_default_description(customer_info['description_name'])
    else:
        default_desc = order.get_default_description()
    default_notes = f"CPE: {order.estimate} | {order.description}"

    raw = input(f"  Contract code [{default_code}]: ").strip()
    contract_code = raw or default_code

    raw = input(f"  Description   [{default_desc}]: ").strip()
    description = raw or default_desc

    raw = input(f"  Notes         [{default_notes}]: ").strip()
    notes = raw or default_notes

    return {
        'contract_code': contract_code,
        'description':   description,
        'notes':         notes,
        'order_ref':     order.estimate.split('/')[-1],
        'customer_id':   customer_id,
        'separation':    separation,
        'parsed_order':  order,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PROCESSING FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def process_bvk_order(
    pdf_path: str,
    shared_session=None,
    pre_gathered_inputs: Optional[dict] = None,
) -> bool:
    """
    Process a BVK broadcast order PDF and create the contract in Etere.

    Args:
        pdf_path:             Path to BVK PDF
        shared_session:       Optional shared Etere session
        pre_gathered_inputs:  Pre-gathered inputs dict (skips upfront prompts)

    Returns:
        True if contract created successfully, False otherwise.
    """
    try:
        order = parse_bvk_pdf(pdf_path)

        print(f"\n{'=' * 70}")
        print("BVK ORDER PROCESSING")
        print(f"{'=' * 70}")
        print(f"Client:  {order.client}")
        print(f"Market:  {order.market}")
        print(f"Flight:  {order.flight_start} – {order.flight_end}")
        print(f"Lines:   {len(order.lines)}")
        print(f"{'=' * 70}\n")

        if pre_gathered_inputs:
            inputs = pre_gathered_inputs
            if 'parsed_order' in inputs:
                order = inputs['parsed_order']  # reuse already-parsed object
            print("[INFO] Using pre-gathered inputs\n")
        else:
            inputs = gather_bvk_inputs(pdf_path)

        if not inputs:
            print("\n✗ Input gathering cancelled")
            return False

        contract_id = _create_bvk_contract_direct(order, inputs)
        return contract_id is not None

    except Exception as exc:
        print(f"\n✗ Error processing BVK order: {exc}")
        import traceback
        traceback.print_exc()
        return False

