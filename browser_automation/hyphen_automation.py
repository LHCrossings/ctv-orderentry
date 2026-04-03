"""
Hyphen (formerly JP Marketing) Order Automation

Handles browser automation for Hyphen "Buy Detail Report" orders
(e.g. DPR / Dept of Pesticide Regulation).

Business Rules:
- Single-market order per PDF (CVC or LAX, from PDF header)
- Rate: gross_rate from PDF (listed explicitly — no gross-up needed)
- Separation: from PDF "Separation between spots" field, applied as (N, 0, 0)
- Description: "(Line N) Days short-time Language" — built by HyphenLine.get_description()
- Notes: estimate.description (the campaign description from the IO)
- Order ref: estimate.estimate (the estimate number from the IO)
- Master market: NYC (standard Crossings TV)
"""

import os
import sys
from pathlib import Path
from typing import Optional

_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from browser_automation.etere_client import EtereClient
from browser_automation.parsers.hyphen_parser import HyphenEstimate, HyphenLine, parse_hyphen_pdf
from src.domain.enums import BillingType, OrderType

CUSTOMER_DB_PATH = os.path.join("data", "customers.db")


# ─────────────────────────────────────────────────────────────────────────────
# CUSTOMER DATABASE
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_customer(client_name: str) -> Optional[dict]:
    """Look up Hyphen customer by advertiser name."""
    if not os.path.exists(CUSTOMER_DB_PATH):
        return None
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        customer = (
            repo.find_by_name(client_name, OrderType.HYPHEN) or
            repo.find_by_name_fuzzy(client_name, OrderType.HYPHEN)
        )
        if customer:
            return {
                'customer_id':       customer.customer_id,
                'code_name':         customer.code_name,
                'description_name':  customer.description_name,
                'include_market':    bool(customer.include_market_in_code),
                'separation':        customer.get_separation_intervals(),
            }
    except Exception as exc:
        print(f"[CUSTOMER DB] ⚠ Lookup failed: {exc}")
    return None


def _save_customer(customer_id: str, client_name: str, separation: tuple) -> None:
    """Upsert a Hyphen customer to the database."""
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        customer = Customer(
            customer_id=customer_id,
            customer_name=client_name,
            order_type=OrderType.HYPHEN,
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

def gather_hyphen_inputs(pdf_path: str) -> Optional[dict]:
    """
    Parse PDF and gather all user inputs before the browser session opens.

    Returns:
        Dict with keys: contract_code, description, notes, order_ref,
        customer_id, separation.
        Returns None if the user cancels.
    """
    print("\n" + "=" * 70)
    print("HYPHEN ORDER - INPUT COLLECTION")
    print("=" * 70)

    print("\n[PARSE] Reading PDF…")
    try:
        estimate = parse_hyphen_pdf(pdf_path)
    except Exception as exc:
        print(f"[PARSE] ✗ Failed: {exc}")
        return None

    print(f"\nClient:      {estimate.client}")
    print(f"Estimate:    {estimate.estimate}")
    print(f"Description: {estimate.description}")
    print(f"Product:     {estimate.product}")
    print(f"Market:      {estimate.market}")
    print(f"Flight:      {estimate.flight_start} – {estimate.flight_end}")
    print(f"Separation:  {estimate.separation} min")
    print(f"Buyer:       {estimate.buyer}")
    print(f"Lines:       {len(estimate.lines)}")
    print(f"Total spots: {sum(l.total_spots for l in estimate.lines)}")
    print()

    # ── Customer lookup ──────────────────────────────────────────────────────
    customer_info = _lookup_customer(estimate.client)
    customer_id: Optional[int] = None
    customer_sep = 25 if estimate.separation == 30 else estimate.separation
    separation = (customer_sep, 0, 0)

    if customer_info:
        customer_id = customer_info['customer_id']
        raw_sep    = customer_info['separation']
        # 30-min separation in DB → enter as 25 (allows 2x/hour, buyers OK with this)
        separation = (25 if raw_sep[0] == 30 else raw_sep[0], raw_sep[1], raw_sep[2])
        print(f"[CUSTOMER] ✓ Found in DB: {estimate.client} → ID {customer_id}")
        print(f"[CUSTOMER] Separation: {separation}")
    else:
        print(f"[CUSTOMER] Not found in DB for '{estimate.client}'")
        raw_id = input("  Enter Etere customer ID (or blank to select in browser): ").strip()
        if raw_id.isdigit():
            customer_id = int(raw_id)
            save_yn = input(
                f"  Save '{estimate.client}' (ID {customer_id}) to DB? (y/n): "
            ).strip().lower()
            if save_yn == 'y':
                _save_customer(str(customer_id), estimate.client, separation)
    print()

    # ── Contract code ─────────────────────────────────────────────────────────
    print("[1/3] Contract Code")
    print("-" * 70)
    if customer_info:
        default_code = estimate.get_default_contract_code(
            customer_info['code_name'],
            include_market=customer_info['include_market'],
        )
    else:
        default_code = estimate.client[:6].upper().replace(" ", "")
    print(f"Default: {default_code}")
    use_default = input("Use default? (y/n): ").strip().lower()
    contract_code = default_code if use_default == 'y' else input("Enter contract code: ").strip()
    print(f"✓ {contract_code}\n")

    # ── Contract description ──────────────────────────────────────────────────
    print("[2/3] Contract Description")
    print("-" * 70)
    if customer_info and customer_info.get('description_name'):
        default_desc = estimate.get_default_description(customer_info['description_name'])
    else:
        default_desc = f"{estimate.client} {estimate.estimate}"
    print(f"Default: {default_desc}")
    use_default = input("Use default? (y/n): ").strip().lower()
    description = default_desc if use_default == 'y' else input("Enter description: ").strip()
    print(f"✓ {description}\n")

    # ── Notes (campaign description from IO) ─────────────────────────────────
    print("[3/3] Notes  (IO Description field → contract notes)")
    print("-" * 70)
    default_notes = estimate.description  # "DPR 2026 Crossings TV"
    print(f"Default: {default_notes}")
    use_default = input("Use default? (y/n): ").strip().lower()
    notes = default_notes if use_default == 'y' else input("Enter notes: ").strip()
    print(f"✓ {notes}\n")

    print("=" * 70)
    print("✓ All inputs gathered — ready for automation")
    print("=" * 70 + "\n")

    return {
        'contract_code': contract_code,
        'description':   description,
        'notes':         notes,
        'order_ref':     estimate.estimate,   # estimate number → order ref
        'customer_id':   customer_id,
        'separation':    separation,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PROCESSING FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def process_hyphen_order(
    driver,
    pdf_path: str,
    shared_session=None,
    pre_gathered_inputs: Optional[dict] = None,
) -> bool:
    """
    Process a Hyphen Buy Detail Report PDF and create the contract in Etere.

    Args:
        driver:               Selenium WebDriver
        pdf_path:             Path to Hyphen PDF
        shared_session:       Optional shared Etere session
        pre_gathered_inputs:  Pre-gathered inputs dict (skips upfront prompts)

    Returns:
        True if contract created successfully, False otherwise.
    """
    try:
        estimate = parse_hyphen_pdf(pdf_path)

        print(f"\n{'=' * 70}")
        print("HYPHEN ORDER PROCESSING")
        print(f"{'=' * 70}")
        print(f"Client:  {estimate.client}")
        print(f"Estimate: {estimate.estimate}")
        print(f"Market:  {estimate.market}")
        print(f"Flight:  {estimate.flight_start} – {estimate.flight_end}")
        print(f"Lines:   {len(estimate.lines)}")
        print(f"{'=' * 70}\n")

        if pre_gathered_inputs:
            inputs = pre_gathered_inputs
            print("[INFO] Using pre-gathered inputs\n")
        else:
            inputs = gather_hyphen_inputs(pdf_path)

        if not inputs:
            print("\n✗ Input gathering cancelled")
            return False

        etere = EtereClient(driver)
        return _create_hyphen_contract(etere, estimate, inputs)

    except Exception as exc:
        print(f"\n✗ Error processing Hyphen order: {exc}")
        import traceback
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT CREATION
# ─────────────────────────────────────────────────────────────────────────────

def _create_hyphen_contract(
    etere: EtereClient,
    estimate: HyphenEstimate,
    inputs: dict,
) -> bool:
    """
    Create the Etere contract for a Hyphen order.

    Workflow:
    1. Create contract header (notes = IO description, order ref = estimate number)
    2. For each line with spots > 0:
       - Consolidate consecutive identical weeks
       - Add Etere contract line(s) with gross rate
    """
    try:
        customer_id = inputs.get('customer_id')
        separation  = inputs.get('separation', (estimate.separation, 0, 0))

        print(f"[HYPHEN] Creating contract for {estimate.client}")

        # ── Contract header ───────────────────────────────────────────────────
        contract_number = etere.create_contract_header(
            customer_id=customer_id,
            code=inputs['contract_code'],
            description=inputs['description'],
            contract_start=estimate.flight_start,
            contract_end=estimate.flight_end,
            customer_order_ref=inputs['order_ref'],   # estimate number
            notes=inputs['notes'],                    # IO description
            charge_to=BillingType.CUSTOMER_SHARE_AGENCY.get_charge_to(),
            invoice_header=BillingType.CUSTOMER_SHARE_AGENCY.get_invoice_header(),
        )

        if not contract_number:
            print("[HYPHEN] ✗ Failed to create contract header")
            return False

        print(f"[HYPHEN] ✓ Contract created: {contract_number}")

        # ── Lines ─────────────────────────────────────────────────────────────
        line_count = 0

        for line in estimate.lines:
            if line.total_spots == 0:
                print(f"  Line {line.line_number}: skipped (0 spots)")
                continue

            rate        = line.gross_rate  # PDF provides gross rate directly
            etere_days  = line.get_etere_days()
            etere_time  = line.get_etere_time()
            description = line.get_description(etere_days, etere_time)
            spot_code   = 10 if line.is_bonus else 2
            duration_s  = line.get_duration_seconds()
            block_pfx   = line.get_block_prefixes()

            time_from, time_to = EtereClient.parse_time_range(etere_time)

            # Sunday 6-7a rule
            adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(etere_days, etere_time)

            # Consolidate consecutive weeks with the same spot count
            ranges = EtereClient.consolidate_weeks(
                line.weekly_spots,
                line.week_start_dates,
                flight_end=estimate.flight_end,
            )

            print(f"\n  Line {line.line_number}: {description}")
            print(f"    Rate: ${rate}  {'[BONUS]' if line.is_bonus else ''}")
            print(f"    Days: {adjusted_days}  Time: {etere_time}  Blocks: {block_pfx}")
            print(f"    Splits into {len(ranges)} Etere line(s)")

            for rng in ranges:
                line_count += 1
                total_spots = rng['spots_per_week'] * rng['weeks']

                print(f"    Creating line {line_count}: "
                      f"{rng['start_date']} – {rng['end_date']} "
                      f"({rng['spots_per_week']} spots/wk × {rng['weeks']} wks = {total_spots})")

                success = etere.add_contract_line(
                    contract_number=contract_number,
                    market=estimate.market,
                    start_date=rng['start_date'],
                    end_date=rng['end_date'],
                    days=adjusted_days,
                    time_from=time_from,
                    time_to=time_to,
                    description=description,
                    spot_code=spot_code,
                    duration_seconds=duration_s,
                    total_spots=total_spots,
                    spots_per_week=rng['spots_per_week'],
                    rate=float(rate),
                    separation_intervals=separation,
                    is_bookend=False,
                    is_billboard=False,
                )

                if not success:
                    print(f"    ✗ Failed to add line {line_count}")
                    return False

        print(f"\n[HYPHEN] ✓ All {line_count} Etere lines added successfully")
        return True

    except Exception as exc:
        print(f"\n[HYPHEN] ✗ Error creating contract: {exc}")
        import traceback
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE TEST
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from browser_automation.etere_session import EtereSession

    pdf = input("Enter path to Hyphen PDF: ").strip()

    with EtereSession() as session:
        session.set_market("NYC")
        success = process_hyphen_order(session.driver, pdf)
        print("\n✓ Done" if success else "\n✗ Failed")
