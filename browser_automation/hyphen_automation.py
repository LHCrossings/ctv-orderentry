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
from browser_automation.ros_definitions import ROS_SCHEDULES
from src.domain.enums import BillingType, OrderType

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH


# ─────────────────────────────────────────────────────────────────────────────
# DATE / DURATION HELPERS (direct DB)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date(s):
    """Parse MM/DD/YYYY, MM/DD/YY, or date objects to datetime.date."""
    from datetime import datetime, date
    if isinstance(s, date):
        return s
    s = str(s).strip()
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%b %d, %Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


def _secs_to_duration(secs: int) -> str:
    """Convert seconds to HH:MM:SS:FF duration string for EtereDirectClient."""
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:00"


# ─────────────────────────────────────────────────────────────────────────────
# DIRECT DB ENTRY
# ─────────────────────────────────────────────────────────────────────────────

def _create_hyphen_contract_direct(estimate: HyphenEstimate, inputs: dict) -> Optional[int]:
    """
    Enter Hyphen order directly via DB stored procedures (no browser).
    Returns contract_id on success, None on failure (rolls back fully).
    """
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = inputs.get('customer_id')
    if customer_id is None:
        print("[HYPHEN DIRECT] ✗ No customer_id — cannot enter without a known ID")
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
            contract_date=_parse_date(estimate.flight_start),
            contract_end_date=_parse_date(estimate.flight_end),
            contract_type=1,
            billing_type="agency",
            note=inputs['notes'],
            customer_order_ref=inputs['order_ref'],
        )
        print(f"[HYPHEN DIRECT] ✓ Contract header ID={contract_id}")

        separation = inputs.get('separation', (estimate.separation, 0, 0))
        line_count = 0

        for line in estimate.lines:
            if line.total_spots == 0:
                continue

            is_bonus     = line.is_bonus
            booking_code = 10 if is_bonus else 2
            duration_str = _secs_to_duration(line.get_duration_seconds())
            etere_days   = line.get_etere_days()
            etere_time   = line.get_etere_time()
            description  = line.get_description(etere_days, etere_time)

            time_from, time_to = EtereClient.parse_time_range(etere_time)
            time_range         = f"{time_from}-{time_to}"

            if is_bonus:
                language = line.program.split()[0].title()
                ros = ROS_SCHEDULES.get(language)
                if ros:
                    time_from, time_to = EtereClient.parse_time_range(ros['time'])
                    time_range    = f"{time_from}-{time_to}"
                    adjusted_days = ros['days']
                    description   = f"(Line {line.line_number}) BNS {language} ROS"
                    print(f"    [ROS] {language}: {adjusted_days} {ros['time']}")
                else:
                    adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(etere_days, etere_time)
            else:
                adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(etere_days, etere_time)

            ranges = EtereClient.consolidate_weeks(
                line.weekly_spots,
                line.week_start_dates,
                flight_end=estimate.flight_end,
            )

            for rng in ranges:
                line_count  += 1
                total_spots  = rng['spots_per_week'] * rng['weeks']
                print(f"  [LINE {line_count}] {description}: "
                      f"{rng['start_date']}–{rng['end_date']} "
                      f"({rng['spots_per_week']}/wk×{rng['weeks']}w={total_spots})")
                client.add_contract_line(
                    market=estimate.market,
                    days=adjusted_days,
                    time_range=time_range,
                    description=description,
                    rate=float(line.gross_rate),
                    total_spots=total_spots,
                    spots_per_week=rng['spots_per_week'],
                    date_from=_parse_date(rng['start_date']),
                    date_to=_parse_date(rng['end_date']),
                    duration=duration_str,
                    is_bonus=is_bonus,
                    booking_code=booking_code,
                    separation_intervals=separation,
                )

        conn.commit()
        conn.close()
        print(f"[HYPHEN DIRECT] ✓ {line_count} lines committed.")
        return contract_id

    except Exception as exc:
        print(f"[HYPHEN DIRECT] ✗ {exc}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


def process_hyphen_order_direct(pdf_path: str, user_input: dict) -> Optional[int]:
    """Direct DB entry point for the order processing service (no browser needed)."""
    estimate = parse_hyphen_pdf(pdf_path)
    return _create_hyphen_contract_direct(estimate, user_input)


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

    # ── Contract code / description / notes ───────────────────────────────────
    if customer_info:
        default_code = estimate.get_default_contract_code(
            customer_info['code_name'],
            include_market=customer_info['include_market'],
        )
    else:
        default_code = estimate.client[:6].upper().replace(" ", "")
    if customer_info and customer_info.get('description_name'):
        default_desc = estimate.get_default_description(customer_info['description_name'])
    else:
        default_desc = f"{estimate.client} {estimate.estimate}"
    default_notes = estimate.description

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

        # Try direct DB first (no browser needed)
        contract_id = _create_hyphen_contract_direct(estimate, inputs)
        if contract_id is not None:
            return True
        elif driver is not None:
            print("[FALLBACK] Direct DB failed — retrying via browser automation...")
            etere = EtereClient(driver)
            return _create_hyphen_contract(etere, estimate, inputs)
        else:
            print("[HYPHEN] ✗ Direct DB failed and no browser driver available")
            return False

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

            # ROS override for bonus lines: replace time/days with standard ROS schedule
            if line.is_bonus:
                language = line.program.split()[0].title()  # "Hmong", "Filipino", etc.
                ros = ROS_SCHEDULES.get(language)
                if ros:
                    time_from, time_to = EtereClient.parse_time_range(ros['time'])
                    adjusted_days = ros['days']
                    description = f"(Line {line.line_number}) BNS {language} ROS"
                    print(f"    [ROS] {language}: {adjusted_days} {ros['time']}")
                else:
                    adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(etere_days, etere_time)
            else:
                # Sunday 6-7a rule (paid lines only)
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
