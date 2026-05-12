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
from datetime import datetime
from pathlib import Path

# Add project root to sys.path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
_src_path = _project_root / 'src'
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from etere_client import EtereClient
from src.domain.enums import BillingType, OrderType, SeparationInterval

from browser_automation.parsers.intertrend_parser import (
    parse_intertrend_pdf,
    IntertrendOrder,
    IntertrendLine,
    format_time_for_description,
)

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
        tag = ' [BNS]' if line.is_bonus() else ''
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
    paid_lines = [ln for ln in order.lines if not ln.is_bonus()]
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
# BROWSER AUTOMATION
# ═══════════════════════════════════════════════════════════════════════════════

def process_intertrend_order(driver, pdf_path: str, user_input: dict = None) -> bool:
    """
    Enter an Intertrend order into Etere. Fully unattended after gather step.

    Args:
        driver:     Selenium WebDriver (raw driver, not session)
        pdf_path:   Path to Intertrend PDF
        user_input: Pre-collected inputs from orchestrator (or None to gather now)

    Returns:
        True if all lines entered successfully.
    """
    if user_input is None:
        user_input = gather_intertrend_inputs(pdf_path)
        if not user_input:
            return False

    order: IntertrendOrder = user_input['order']
    gross_up_factor: float = user_input['gross_up_factor']
    market: str = user_input['market']
    separation: tuple = user_input['separation']
    billing: BillingType = user_input['billing']

    print('\n' + '=' * 70)
    print('STARTING BROWSER AUTOMATION — INTERTREND')
    print('=' * 70)

    etere = EtereClient(driver)

    try:
        # ─── Create contract header ──────────────────────────────────────────
        contract_number = etere.create_contract_header(
            customer_id=int(user_input['customer_id']),
            code=user_input['contract_code'],
            description=user_input['contract_description'],
            contract_start=order.flight_start,
            contract_end=order.flight_end,
            customer_order_ref=user_input['customer_order_ref'],
            notes=user_input['notes'],
            charge_to=billing.get_charge_to(),
            invoice_header=billing.get_invoice_header(),
        )

        if not contract_number:
            print('[CONTRACT] ✗ Failed to create contract header')
            return False

        print(f'[CONTRACT] ✓ Created: {contract_number}')

        # ─── Enter contract lines ────────────────────────────────────────────
        all_success = True
        etere_line_num = 0

        for line in order.lines:
            is_bonus = line.is_bonus()
            spot_code = 10 if is_bonus else 2

            # Gross-up paid lines; bonus lines stay at $0
            rate = 0.0 if is_bonus else round(line.net_rate * gross_up_factor, 2)

            # Description: "(Line N) {days} {time} Chinese" or "(Line N) {days} {time} Chinese BNS"
            time_fmt = format_time_for_description(line.time)
            desc_parts = [f'(Line {line.line_number})', line.days, time_fmt, 'Chinese']
            if is_bonus:
                desc_parts.append('BNS')
            description = ' '.join(desc_parts)

            # Apply Sunday 6-7a rule
            days, _ = EtereClient.check_sunday_6_7a_rule(line.days, line.time)
            time_from, time_to = EtereClient.parse_time_range(line.time)

            tag = 'BNS' if is_bonus else 'COM'
            print(f'\n[LINE {line.line_number}] {tag}  {line.days} {time_fmt} :{line.duration}  '
                  f'{line.total_spots} spots  ${rate:.2f} gross')

            # Consolidate consecutive equal-count weeks into Etere lines
            # consolidate_weeks accepts "May 11" format and handles flight truncation
            flight_end_mdy   = datetime.strptime(order.flight_end,   '%Y-%m-%d').strftime('%m/%d/%Y')
            flight_start_mdy = datetime.strptime(order.flight_start, '%Y-%m-%d').strftime('%m/%d/%Y')
            week_groups = EtereClient.consolidate_weeks(
                line.weekly_spots, order.week_start_dates, flight_end_mdy, flight_start_mdy
            )

            for group in week_groups:
                group_start = group['start_date']
                group_end = group['end_date']
                spots_per_week = group['spots_per_week']
                num_weeks = group['weeks']
                group_total = spots_per_week * num_weeks

                etere_line_num += 1
                print(f'  [{etere_line_num}] {group_start} – {group_end}  '
                      f'{num_weeks}wk × {spots_per_week}/wk = {group_total} spots')

                success = etere.add_contract_line(
                    contract_number=contract_number,
                    market=market,
                    start_date=group_start,
                    end_date=group_end,
                    days=days,
                    time_from=time_from,
                    time_to=time_to,
                    description=description,
                    spot_code=spot_code,
                    duration_seconds=line.duration,
                    spots_per_week=spots_per_week,
                    total_spots=group_total,
                    rate=rate,
                    separation_intervals=separation,
                    max_daily_run=None,
                )

                if success:
                    print(f'  ✓ Entered')
                else:
                    print(f'  ✗ FAILED')
                    all_success = False

        status = '✓ SUCCESS' if all_success else '✗ SOME LINES FAILED'
        print(f'\n[DONE] {status} — {etere_line_num} Etere lines entered')
        return all_success

    except Exception as e:
        import traceback
        print(f'\n[ERROR] Intertrend automation error: {e}')
        traceback.print_exc()
        return False
