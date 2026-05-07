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

from browser_automation.etere_client import EtereClient
from browser_automation.parsers.bvk_parser import BVKOrder, parse_bvk_pdf
from src.domain.enums import BillingType, OrderType

CUSTOMER_DB_PATH = os.path.join("data", "customers.db")


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

    # ── Contract code ─────────────────────────────────────────────────────────
    print("[1/3] Contract Code")
    print("-" * 70)
    if customer_info:
        default_code = order.get_default_contract_code(
            customer_info['code_name'],
            include_market=customer_info['include_market'],
        )
    else:
        default_code = order.get_default_contract_code()
    print(f"Default: {default_code}")
    contract_code = (
        default_code
        if input("Use default? (y/n): ").strip().lower() == 'y'
        else input("Enter contract code: ").strip()
    )
    print(f"✓ {contract_code}\n")

    # ── Contract description ──────────────────────────────────────────────────
    print("[2/3] Contract Description")
    print("-" * 70)
    if customer_info and customer_info.get('description_name'):
        default_desc = order.get_default_description(customer_info['description_name'])
    else:
        default_desc = order.get_default_description()
    print(f"Default: {default_desc}")
    description = (
        default_desc
        if input("Use default? (y/n): ").strip().lower() == 'y'
        else input("Enter description: ").strip()
    )
    print(f"✓ {description}\n")

    # ── Notes (CPE number) ────────────────────────────────────────────────────
    print("[3/3] Notes")
    print("-" * 70)
    default_notes = f"CPE: {order.estimate}"
    print(f"Default: {default_notes}")
    notes = (
        default_notes
        if input("Use default? (y/n): ").strip().lower() == 'y'
        else input("Enter notes: ").strip()
    )
    print(f"✓ {notes}\n")

    print("=" * 70)
    print("✓ All inputs gathered — ready for automation")
    print("=" * 70 + "\n")

    return {
        'contract_code': contract_code,
        'description':   description,
        'notes':         notes,
        'order_ref':     order.estimate,
        'customer_id':   customer_id,
        'separation':    separation,
        'parsed_order':  order,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PROCESSING FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def process_bvk_order(
    driver,
    pdf_path: str,
    shared_session=None,
    pre_gathered_inputs: Optional[dict] = None,
) -> bool:
    """
    Process a BVK broadcast order PDF and create the contract in Etere.

    Args:
        driver:               Selenium WebDriver
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

        etere = EtereClient(driver)
        return _create_bvk_contract(etere, order, inputs)

    except Exception as exc:
        print(f"\n✗ Error processing BVK order: {exc}")
        import traceback
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT CREATION
# ─────────────────────────────────────────────────────────────────────────────

def _create_bvk_contract(
    etere: EtereClient,
    order: BVKOrder,
    inputs: dict,
) -> bool:
    """
    Create the Etere contract for a BVK order.

    Workflow:
    1. Create contract header
    2. For each line with spots > 0:
       - Apply Sunday 6-7a rule
       - Consolidate consecutive identical weeks
       - Add Etere contract line(s)
    """
    try:
        customer_id = inputs.get('customer_id')
        separation  = inputs.get('separation', (25, 0, 0))

        print(f"[BVK] Creating contract for {order.client}")

        # ── Contract header ───────────────────────────────────────────────────
        contract_number = etere.create_contract_header(
            customer_id=customer_id,
            code=inputs['contract_code'],
            description=inputs['description'],
            contract_start=order.flight_start,
            contract_end=order.flight_end,
            customer_order_ref=inputs['order_ref'],
            notes=inputs['notes'],
            charge_to=BillingType.CUSTOMER_SHARE_AGENCY.get_charge_to(),
            invoice_header=BillingType.CUSTOMER_SHARE_AGENCY.get_invoice_header(),
        )

        if not contract_number:
            print("[BVK] ✗ Failed to create contract header")
            return False

        print(f"[BVK] ✓ Contract created: {contract_number}")

        # ── Lines ─────────────────────────────────────────────────────────────
        line_count = 0

        for line in order.lines:
            if line.total_spots == 0:
                print(f"  Line {line.line_no}: skipped (0 spots)")
                continue

            spot_code   = 10 if line.is_bonus else 2
            description = line.get_description()

            time_from, time_to = EtereClient.parse_time_range(line.time_str)
            adjusted_days, _   = EtereClient.check_sunday_6_7a_rule(line.days, line.time_str)

            ranges = EtereClient.consolidate_weeks(
                line.weekly_spots,
                order.week_dates,
                flight_end=order.flight_end,
            )

            print(f"\n  Line {line.line_no}: {description}")
            print(f"    Rate: ${line.gross_rate:.2f}  {'[BONUS]' if line.is_bonus else ''}")
            print(f"    Days: {adjusted_days}  Time: {line.time_str}  Lang: {line.language}")
            print(f"    Splits into {len(ranges)} Etere line(s)")

            for rng in ranges:
                line_count += 1
                total_spots = rng['spots_per_week'] * rng['weeks']

                print(
                    f"    Line {line_count}: {rng['start_date']} – {rng['end_date']} "
                    f"({rng['spots_per_week']}/wk × {rng['weeks']} wks = {total_spots})"
                )

                success = etere.add_contract_line(
                    contract_number=contract_number,
                    market=order.market,
                    start_date=rng['start_date'],
                    end_date=rng['end_date'],
                    days=adjusted_days,
                    time_from=time_from,
                    time_to=time_to,
                    description=description,
                    spot_code=spot_code,
                    duration_seconds=line.duration,
                    total_spots=total_spots,
                    spots_per_week=rng['spots_per_week'],
                    rate=float(line.gross_rate),
                    separation_intervals=separation,
                    is_bookend=False,
                    is_billboard=False,
                )

                if not success:
                    print(f"    ✗ Failed to add line {line_count}")
                    return False

        print(f"\n[BVK] ✓ All {line_count} Etere lines added successfully")
        return True

    except Exception as exc:
        print(f"\n[BVK] ✗ Error creating contract: {exc}")
        import traceback
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE TEST
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    from browser_automation.etere_session import EtereSession

    pdf = input("Enter path to BVK PDF: ").strip()

    with EtereSession() as session:
        session.set_market("NYC")
        success = process_bvk_order(session.driver, pdf)
        print("\n✓ Done" if success else "\n✗ Failed")
