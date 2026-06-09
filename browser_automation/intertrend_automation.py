"""
Intertrend Communications Order Automation
Browser automation for entering Intertrend agency orders into Etere.

═══════════════════════════════════════════════════════════════════════════════
INTERTREND BUSINESS RULES
═══════════════════════════════════════════════════════════════════════════════

Known Customer:
    California State Lottery (ID: 280) → SFO market

Billing (Universal for ALL Intertrend):
    - Charge To:      "Customer share indicating agency %"
    - Invoice Header: "Agency"

Separation: (25, 0, 0)

Rates: Always NET — must be grossed up by agency fee (default 15%)
    gross_rate = net_rate / (1 - agency_fee)

DP Codes:
    RS → paid commercial (spot_code = 2)
    AV → bonus / BNS      (spot_code = 10)

Line Description Format:
    "(Line N) {days} {time} Chinese"        for paid lines
    "(Line N) {days} {time} Chinese BNS"    for bonus lines

Contract Code:    "Intertrend CALLOT {estimate}"  e.g. "Intertrend CALLOT 28"  (no leading zeros)
Description:      "CA State Lottery Est {estimate} - {product}"  e.g. "CA State Lottery Est 28 - Late Spring Scratchers"

═══════════════════════════════════════════════════════════════════════════════
"""

import sys
from datetime import datetime, date
from pathlib import Path

# Add project root to sys.path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
_src_path = _project_root / 'src'
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from etere_client import EtereClient
from src.domain.enums import BillingType

from browser_automation.parsers.intertrend_parser import (
    parse_intertrend_pdf,
    IntertrendOrder,
    IntertrendLine,
    format_time_for_description,
)

# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_date_ymd(date_str: str) -> date:
    """Convert YYYY-MM-DD string to date object."""
    return datetime.strptime(date_str, '%Y-%m-%d').date()


def _parse_date_mdy(date_str: str) -> date:
    """Convert MM/DD/YYYY string to date object."""
    return datetime.strptime(date_str, '%m/%d/%Y').date()


def _secs_to_duration(seconds: int) -> str:
    """Convert duration in seconds to Etere duration string (e.g. 30 → ':30')."""
    return f":{seconds}"


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

_CUSTOMER_ID = 280          # California State Lottery in Etere
_DEFAULT_AGENCY_FEE = 15.0  # percent
_SEPARATION = (25, 0, 0)
_BILLING = BillingType.CUSTOMER_SHARE_AGENCY


# ═══════════════════════════════════════════════════════════════════════════════
# UPFRONT INPUT COLLECTION
# ═══════════════════════════════════════════════════════════════════════════════

def gather_intertrend_inputs(pdf_path: str) -> dict:
    """
    Parse the PDF and collect all inputs before browser automation starts.

    Returns a dict ready for process_intertrend_order().
    """
    print('\n' + '=' * 70)
    print('INTERTREND ORDER — UPFRONT INPUT COLLECTION')
    print('=' * 70)

    print('\n[PARSE] Reading PDF...')
    try:
        order = parse_intertrend_pdf(pdf_path)
    except Exception as e:
        print(f'[PARSE] ✗ Failed: {e}')
        return None

    print(f'[PARSE] ✓ Order:    {order.order_number}')
    print(f'[PARSE] ✓ Client:   {order.client} ({order.client_code})')
    print(f'[PARSE] ✓ Product:  {order.product} ({order.product_code})')
    print(f'[PARSE] ✓ Estimate: {order.estimate}')
    print(f'[PARSE] ✓ Market:   {order.market}')
    print(f'[PARSE] ✓ Flight:   {order.flight_start} – {order.flight_end}')
    print(f'[PARSE] ✓ Weeks:    {", ".join(order.week_start_dates)}')
    print(f'[PARSE] ✓ Lines:    {len(order.lines)}')

    # Show line summary
    print()
    for line in order.lines:
        tag = ' [BNS]' if line.is_bonus else ''
        time_fmt = format_time_for_description(line.time)
        print(f'  Line {line.line_number}: {line.days} {time_fmt} :{line.duration} {line.dp_code}{tag}'
              f'  — {line.total_spots} spots @ ${line.net_rate:.2f} net')
        print(f'           Weekly: {line.weekly_spots}')

    # ─── Gross-up ───────────────────────────────────────────────────────────
    print(f'\n[GROSS-UP] Rates in this IO are NET. Agency fee must be applied.')
    print(f'Agency fee % [{_DEFAULT_AGENCY_FEE}]: ', end='')
    raw = input().strip()
    agency_fee = float(raw) if raw else _DEFAULT_AGENCY_FEE
    gross_up_factor = 1.0 / (1.0 - agency_fee / 100.0)
    print(f'[GROSS-UP] Factor: {gross_up_factor:.6f}  (÷{1 - agency_fee/100:.2f})')

    # Verify expected gross amounts
    paid_lines = [ln for ln in order.lines if not ln.is_bonus]
    net_total = sum(ln.net_rate * ln.total_spots for ln in paid_lines)
    gross_total = net_total * gross_up_factor
    print(f'[GROSS-UP] Net total: ${net_total:,.2f}  →  Gross total: ${gross_total:,.2f}')

    # ─── Customer ────────────────────────────────────────────────────────────
    print(f'\n[CUSTOMER] California State Lottery → Etere ID {_CUSTOMER_ID}')
    print(f'Customer ID [{_CUSTOMER_ID}]: ', end='')
    raw = input().strip()
    customer_id = int(raw) if raw else _CUSTOMER_ID

    # ─── Contract code / description ─────────────────────────────────────────
    est_num = str(int(order.estimate)) if order.estimate.isdigit() else order.estimate
    default_code = f'Intertrend CALLOT {est_num}'
    default_desc = f'CA State Lottery Est {est_num} - {order.product}'

    print(f'\n[CONTRACT] Code [{default_code}]: ', end='')
    raw = input().strip()
    contract_code = raw if raw else default_code

    print(f'[CONTRACT] Description [{default_desc}]: ', end='')
    raw = input().strip()
    contract_description = raw if raw else default_desc

    # ─── Order ref / notes ───────────────────────────────────────────────────
    default_ref = f'Order {order.order_number}, Est {est_num}'
    print(f'[REF] Customer order ref [{default_ref}]: ', end='')
    raw = input().strip()
    customer_order_ref = raw if raw else default_ref

    print(f'[NOTES] Notes (optional): ', end='')
    notes = input().strip()

    print('\n' + '=' * 70)
    print('READY TO ENTER:')
    print(f'  Code:    {contract_code}')
    print(f'  Desc:    {contract_description}')
    print(f'  Market:  {order.market}')
    print(f'  Flight:  {order.flight_start} – {order.flight_end}')
    print(f'  Lines:   {len(order.lines)}')
    print(f'  Gross-up factor: {gross_up_factor:.4f}')
    print('=' * 70)
    print('\nProceed? (Enter = yes, N = abort): ', end='')
    if input().strip().upper() == 'N':
        print('[ABORT] User cancelled.')
        return None

    return {
        'order': order,
        'customer_id': customer_id,
        'market': order.market,
        'gross_up_factor': gross_up_factor,
        'contract_code': contract_code,
        'contract_description': contract_description,
        'customer_order_ref': customer_order_ref,
        'notes': notes,
        'billing': _BILLING,
        'separation': _SEPARATION,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DIRECT DB ENTRY
# ═══════════════════════════════════════════════════════════════════════════════

def _create_intertrend_contract_direct(order: IntertrendOrder, inputs: dict) -> Optional[int]:
    """Enter Intertrend order directly via DB stored procedures (no browser).

    Returns contract_id on success, None on failure (rolls back fully).
    """
    from browser_automation.etere_direct_client import EtereDirectClient, connect
    from typing import Optional as _Opt

    customer_id     = inputs.get('customer_id', _CUSTOMER_ID)
    gross_up_factor = inputs.get('gross_up_factor', 1.0 / (1.0 - _DEFAULT_AGENCY_FEE / 100.0))
    market          = inputs.get('market', order.market)
    separation      = inputs.get('separation', _SEPARATION)

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=inputs['contract_code'],
            description=inputs['contract_description'],
            customer_id=int(customer_id),
            contract_date=_parse_date_ymd(order.flight_start),
            contract_end_date=_parse_date_ymd(order.flight_end),
            contract_type=1,
            billing_type="agency",
            note=inputs.get('notes', ''),
            customer_order_ref=inputs.get('customer_order_ref', ''),
            allow_rename=True,
        )
        if not contract_id:
            print("[INTERTREND DIRECT] ✗ Failed to create contract header")
            return None
        print(f"[INTERTREND DIRECT] ✓ Contract header ID={contract_id}")

        etere_line_num = 0
        flight_end_mdy   = datetime.strptime(order.flight_end,   '%Y-%m-%d').strftime('%m/%d/%Y')
        flight_start_mdy = datetime.strptime(order.flight_start, '%Y-%m-%d').strftime('%m/%d/%Y')

        for line in order.lines:
            is_bonus     = line.is_bonus
            booking_code = 10 if is_bonus else 2
            rate         = 0.0 if is_bonus else round(float(line.net_rate) * gross_up_factor, 2)

            time_fmt = format_time_for_description(line.time)
            desc_parts = [f'(Line {line.line_number})', line.days, time_fmt, 'Chinese']
            if is_bonus:
                desc_parts.append('BNS')
            description = ' '.join(desc_parts)

            adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(line.days, line.time)
            time_from, time_to = EtereClient.parse_time_range(line.time)
            time_range = f"{time_from}-{time_to}"
            duration_str = _secs_to_duration(line.duration)

            week_groups = EtereClient.consolidate_weeks(
                line.weekly_spots, order.week_start_dates, flight_end_mdy, flight_start_mdy
            )

            for group in week_groups:
                spots_per_week = group['spots_per_week']
                num_weeks      = group['weeks']
                group_total    = spots_per_week * num_weeks

                etere_line_num += 1
                print(f"  [LINE {etere_line_num}] {'BNS' if is_bonus else 'COM'} "
                      f"{group['start_date']}–{group['end_date']} "
                      f"{num_weeks}wk × {spots_per_week}/wk = {group_total} spots  ${rate:.2f}")

                client.add_contract_line(
                    market=market,
                    days=adjusted_days,
                    time_range=time_range,
                    description=description,
                    rate=rate,
                    total_spots=group_total,
                    spots_per_week=spots_per_week,
                    date_from=_parse_date_mdy(group['start_date']),
                    date_to=_parse_date_mdy(group['end_date']),
                    duration=duration_str,
                    is_bonus=is_bonus,
                    booking_code=booking_code,
                    separation_intervals=separation,
                )

        conn.commit()
        conn.close()
        print(f"[INTERTREND DIRECT] ✓ {etere_line_num} lines committed.")
        return contract_id

    except Exception as exc:
        print(f"[INTERTREND DIRECT] ✗ {exc}")
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
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def process_intertrend_order(pdf_path: str, shared_session=None, pre_gathered_inputs=None) -> bool:
    """
    Enter an Intertrend order into Etere via direct DB entry.

    Args:
        pdf_path:             Path to Intertrend PDF
        shared_session:       Unused — kept for orchestrator compatibility
        pre_gathered_inputs:  Pre-collected inputs from orchestrator (or None to gather now)

    Returns:
        True if all lines entered successfully.
    """
    user_input = pre_gathered_inputs
    if user_input is None:
        user_input = gather_intertrend_inputs(pdf_path)
        if not user_input:
            return False

    order: IntertrendOrder = user_input['order']

    contract_id = _create_intertrend_contract_direct(order, user_input)
    return contract_id is not None
