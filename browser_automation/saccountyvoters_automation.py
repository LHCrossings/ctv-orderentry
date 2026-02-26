"""
Sacramento County Voter Registration Automation

Handles browser automation for Sacramento County Voter Registration
insertion orders. The order has two phases with different durations:
  Phase 1: :15s, Apr–May
  Phase 2: :30s, May–Jun

Each phase becomes a separate Etere contract.
Market: CVC, Separation: (15, 0, 0).
"""

import os
import re
import sys
from pathlib import Path
from typing import Optional

# Add project root to path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from browser_automation.etere_client import EtereClient
from browser_automation.ros_definitions import ROS_SCHEDULES
from browser_automation.parsers.saccountyvoters_parser import (
    SacCountyVotersOrder,
    SacCountyVotersPhase,
    SacCountyVotersLine,
    parse_saccountyvoters_pdf,
)
from src.domain.enums import BillingType, OrderType, SeparationInterval


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

SAC_MARKET = "CVC"
SAC_SEPARATION = SeparationInterval.SACCOUNTYVOTERS.value   # (15, 0, 0)
CUSTOMER_DB_PATH = os.path.join("data", "customers.db")


# ─────────────────────────────────────────────────────────────────────────────
# CUSTOMER DATABASE LOOKUP
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_customer(client_name: str) -> Optional[dict]:
    """Look up SacCountyVoters customer in the database by name."""
    if not os.path.exists(CUSTOMER_DB_PATH):
        return None
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        customer = (
            repo.find_by_name(client_name, OrderType.SACCOUNTYVOTERS) or
            repo.find_by_name_fuzzy(client_name, OrderType.SACCOUNTYVOTERS)
        )
        if customer:
            return {
                'customer_id': customer.customer_id,
                'abbreviation': customer.abbreviation,
            }
    except Exception as exc:
        print(f"[CUSTOMER DB] ⚠ Lookup failed: {exc}")
    return None


def _save_new_customer(
    customer_id: str,
    customer_name: str,
    db_path: str = CUSTOMER_DB_PATH,
) -> None:
    """Save a new SacCountyVoters customer to the database."""
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        repo = CustomerRepository(db_path)
        customer = Customer(
            customer_id=customer_id,
            customer_name=customer_name,
            order_type=OrderType.SACCOUNTYVOTERS,
            abbreviation=customer_name[:8].upper(),
            default_market=SAC_MARKET,
            billing_type="client",
            separation_customer=SAC_SEPARATION[0],
            separation_event=SAC_SEPARATION[1],
            separation_order=SAC_SEPARATION[2],
        )
        repo.save(customer)
        print(f"[CUSTOMER DB] ✓ Saved: {customer_name} → ID {customer_id}")
    except Exception as exc:
        print(f"[CUSTOMER DB] ✗ Save failed: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# DAYPART PARSING
# ─────────────────────────────────────────────────────────────────────────────

def _parse_daypart(daypart: str):
    """
    Parse a SacCountyVoters daypart string into (days, time_range).

    Examples:
        "M-F 7p-8p/ 11:30p-12a"  → ("M-F", "7p-8p; 11:30p-12a")
        "M-Sun 8p-9p/ M-F 9p-10p" → ("M-Su", "8p-9p; 9p-10p")
        "Sat-Sun 6p-8p"            → ("Sa-Su", "6p-8p")
        "M-Sun 6a-11:59p"          → ("M-Su", "6a-11:59p")

    Returns:
        Tuple of (etere_days, etere_time_str)
        etere_time_str may contain semicolons for multi-range entries.
    """
    dp = daypart.strip()

    # Normalise day abbreviations
    dp = re.sub(r'\bM-Sun\b', 'M-Su', dp, flags=re.IGNORECASE)
    dp = re.sub(r'\bM-Sunday\b', 'M-Su', dp, flags=re.IGNORECASE)
    dp = re.sub(r'\bSat-Sun\b', 'Sa-Su', dp, flags=re.IGNORECASE)
    dp = re.sub(r'\bSat-Sunday\b', 'Sa-Su', dp, flags=re.IGNORECASE)
    dp = re.sub(r'\bSunday\b', 'Su', dp, flags=re.IGNORECASE)

    # Find all day patterns and time ranges
    # Day pattern: M-Su, M-F, Sa-Su, M-Sa, etc.
    day_pattern_re = re.compile(
        r'\b(M-Su|M-F|Sa-Su|M-Sa|Sa|Su|Mon-Fri|Mon-Sun)\b', re.IGNORECASE
    )
    day_matches = day_pattern_re.findall(dp)

    # Normalise separators: replace "/" and "," with ";" for time_range
    time_str = re.sub(r'[/,]', ';', dp)

    # Strip out all day-pattern tokens from time_str to leave only times
    time_str = day_pattern_re.sub('', time_str).strip()
    # Clean up leading/trailing separators and whitespace
    time_str = re.sub(r'^[\s;]+|[\s;]+$', '', time_str)
    time_str = re.sub(r'\s*;\s*', '; ', time_str)
    time_str = re.sub(r'\s+', ' ', time_str).strip()

    # Determine broadest day range from all day patterns found
    etere_days = _broadest_day_range(day_matches) if day_matches else "M-Su"

    return etere_days, time_str


def _broadest_day_range(day_patterns: list) -> str:
    """
    Given a list of day-pattern strings, return the broadest range.

    "M-F" + "Sa-Su" = "M-Su"
    "M-Su" alone  = "M-Su"
    "M-F" alone   = "M-F"
    "Sa-Su" alone = "Sa-Su"
    """
    combined_lower = [d.lower() for d in day_patterns]
    has_weekday = any(d in combined_lower for d in ['m-f', 'mon-fri', 'm-sa'])
    has_weekend = any(d in combined_lower for d in ['sa-su', 'sat-su', 'sat-sun'])
    has_full    = any(d in combined_lower for d in ['m-su', 'm-sun', 'mon-sun'])

    if has_full:
        return "M-Su"
    if has_weekday and has_weekend:
        return "M-Su"
    if has_weekday:
        return "M-F"
    if has_weekend:
        return "Sa-Su"
    return "M-Su"


def _language_to_ros_key(language: str) -> Optional[str]:
    """
    Map a SacCountyVoters language string to a ROS_SCHEDULES key.

    "Chinese(Cantonese) News" → "Chinese"
    "Korean"                  → "Korean"
    "Vietnamese"              → "Vietnamese"
    etc.
    """
    lang_lower = language.lower()
    priority_order = [
        "chinese", "cantonese", "mandarin", "filipino", "tagalog",
        "vietnamese", "korean", "hmong", "japanese", "south asian",
        "hindi", "punjabi",
    ]
    for key in priority_order:
        if key in lang_lower:
            # Map to ROS_SCHEDULES keys
            if key in ("cantonese", "mandarin"):
                return "Chinese"
            if key == "tagalog":
                return "Filipino"
            return key.title()
    # Fallback: try direct title match
    for ros_key in ROS_SCHEDULES:
        if ros_key.lower() in lang_lower:
            return ros_key
    return None


# ─────────────────────────────────────────────────────────────────────────────
# UPFRONT INPUT GATHERING
# ─────────────────────────────────────────────────────────────────────────────

def gather_saccountyvoters_inputs(pdf_path: str) -> Optional[dict]:
    """
    Parse PDF and gather all user inputs BEFORE the browser session opens.

    Called by the orchestrator upfront-gathering phase.

    Returns:
        Dict with phase1_inputs, phase2_inputs, customer_id, notes, separation.
        Returns None if user cancels.
    """
    print("\n" + "=" * 70)
    print("SACRAMENTO COUNTY VOTER REGISTRATION - INPUT COLLECTION")
    print("=" * 70)

    print("\n[PARSE] Reading PDF…")
    try:
        order = parse_saccountyvoters_pdf(pdf_path)
    except Exception as exc:
        print(f"[PARSE] ✗ Failed: {exc}")
        return None

    # Summary
    print(f"\nClient:   {order.client}")
    print(f"Contact:  {order.contact}")
    print(f"Email:    {order.email}")
    print(f"Campaign: {order.campaign}")
    print(f"Market:   {order.market}")

    for phase in order.phases:
        paid  = [l for l in phase.lines if not l.is_bonus]
        bonus = [l for l in phase.lines if l.is_bonus]
        total = sum(l.total_spots for l in phase.lines)
        print(f"\n  Phase {phase.phase_number}: :{phase.duration_seconds}s  "
              f"{phase.flight_start} – {phase.flight_end}")
        print(f"    {len(paid)} paid lines, {len(bonus)} bonus lines, {total} total spots")
    print()

    # ── Customer lookup ───────────────────────────────────────────────────────
    customer_info = _lookup_customer(order.client)
    customer_id: Optional[int] = None

    if customer_info:
        customer_id = customer_info['customer_id']
        print(f"[CUSTOMER] ✓ Found in DB: {order.client} → ID {customer_id}")
    else:
        print(f"[CUSTOMER] Not found in DB for '{order.client}'")
        raw_id = input("  Enter Etere customer ID (or blank to select in browser): ").strip()
        if raw_id.isdigit():
            customer_id = int(raw_id)
            save_yn = input(
                f"  Save '{order.client}' (ID {customer_id}) to DB for next time? (y/n): "
            ).strip().lower()
            if save_yn == 'y':
                _save_new_customer(str(customer_id), order.client)
    print()

    # ── Phase 1 inputs ────────────────────────────────────────────────────────
    year_ph1 = order.phases[0].flight_start.split('/')[-1]
    print("[1/5] Phase 1 Contract Code")
    print("-" * 70)
    default_code_ph1 = f"SAC VTR {year_ph1} PH1"
    print(f"Default: {default_code_ph1}")
    use_default = input("Use default? (y/n): ").strip().lower()
    code_ph1 = default_code_ph1 if use_default == 'y' else input("Enter contract code: ").strip()
    print(f"✓ {code_ph1}\n")

    print("[2/5] Phase 1 Contract Description")
    print("-" * 70)
    default_desc_ph1 = f"Sacramento County Voter Registration Phase 1"
    print(f"Default: {default_desc_ph1}")
    use_default = input("Use default? (y/n): ").strip().lower()
    desc_ph1 = default_desc_ph1 if use_default == 'y' else input("Enter description: ").strip()
    print(f"✓ {desc_ph1}\n")

    # ── Phase 2 inputs ────────────────────────────────────────────────────────
    year_ph2 = order.phases[1].flight_start.split('/')[-1]
    print("[3/5] Phase 2 Contract Code")
    print("-" * 70)
    default_code_ph2 = f"SAC VTR {year_ph2} PH2"
    print(f"Default: {default_code_ph2}")
    use_default = input("Use default? (y/n): ").strip().lower()
    code_ph2 = default_code_ph2 if use_default == 'y' else input("Enter contract code: ").strip()
    print(f"✓ {code_ph2}\n")

    print("[4/5] Phase 2 Contract Description")
    print("-" * 70)
    default_desc_ph2 = f"Sacramento County Voter Registration Phase 2"
    print(f"Default: {default_desc_ph2}")
    use_default = input("Use default? (y/n): ").strip().lower()
    desc_ph2 = default_desc_ph2 if use_default == 'y' else input("Enter description: ").strip()
    print(f"✓ {desc_ph2}\n")

    # ── Notes ─────────────────────────────────────────────────────────────────
    print("[5/5] Contract Notes")
    print("-" * 70)
    default_notes = f"Contact: {order.contact}\nEmail: {order.email}"
    print(f"Default:\n{default_notes}")
    use_default = input("Use default? (y/n): ").strip().lower()
    notes = default_notes if use_default == 'y' else input("Enter notes: ").strip()
    print(f"✓ {notes}\n")

    # ── Separation confirmation ────────────────────────────────────────────────
    sep = SAC_SEPARATION
    print(f"Separation: Customer={sep[0]}, Event={sep[1]}, Order={sep[2]}")
    sep_yn = input("Keep default separation? (y/n): ").strip().lower()
    if sep_yn != 'y':
        c = input(f"  Customer separation [{sep[0]}]: ").strip()
        e = input(f"  Event separation [{sep[1]}]: ").strip()
        o = input(f"  Order separation [{sep[2]}]: ").strip()
        sep = (
            int(c) if c.isdigit() else sep[0],
            int(e) if e.isdigit() else sep[1],
            int(o) if o.isdigit() else sep[2],
        )
    print(f"✓ Separation: {sep}\n")

    print("=" * 70)
    print("✓ All inputs gathered — ready for automation")
    print("=" * 70 + "\n")

    return {
        'phase1_inputs': {
            'contract_code': code_ph1,
            'description': desc_ph1,
        },
        'phase2_inputs': {
            'contract_code': code_ph2,
            'description': desc_ph2,
        },
        'notes': notes,
        'customer_id': customer_id,
        'separation': sep,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PROCESSING FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def process_saccountyvoters_order(
    driver,
    pdf_path: str,
    shared_session=None,
    pre_gathered_inputs: Optional[dict] = None,
) -> bool:
    """
    Process a Sacramento County Voter Registration PDF and create contracts.

    Creates one Etere contract per phase (2 total).

    Args:
        driver:               Selenium WebDriver
        pdf_path:             Path to SacCountyVoters PDF
        shared_session:       Optional shared Etere session
        pre_gathered_inputs:  Pre-gathered inputs dict (skips upfront prompts)

    Returns:
        True if all contracts created successfully, False otherwise.
    """
    try:
        order = parse_saccountyvoters_pdf(pdf_path)

        print(f"\n{'=' * 70}")
        print("SACRAMENTO COUNTY VOTER REGISTRATION PROCESSING")
        print(f"{'=' * 70}")
        print(f"Client:   {order.client}")
        print(f"Campaign: {order.campaign}")
        print(f"Market:   {order.market}")
        print(f"Phases:   {len(order.phases)}")
        print(f"{'=' * 70}\n")

        if pre_gathered_inputs:
            inputs = pre_gathered_inputs
            print("[INFO] Using pre-gathered inputs\n")
        else:
            inputs = gather_saccountyvoters_inputs(pdf_path)

        if not inputs:
            print("\n✗ Input gathering cancelled")
            return False

        etere = EtereClient(driver)
        customer_id = inputs.get('customer_id')
        notes = inputs.get('notes', '')
        separation = inputs.get('separation', SAC_SEPARATION)

        phase_input_keys = ['phase1_inputs', 'phase2_inputs']
        all_ok = True

        for phase, input_key in zip(order.phases, phase_input_keys):
            phase_inputs = inputs.get(input_key, {})
            ok = _create_phase_contract(
                etere=etere,
                phase=phase,
                phase_inputs=phase_inputs,
                customer_id=customer_id,
                notes=notes,
                separation=separation,
            )
            if not ok:
                all_ok = False
                print(f"\n✗ Phase {phase.phase_number} contract FAILED")
            else:
                print(f"\n✓ Phase {phase.phase_number} contract created")

        if all_ok:
            print(f"\n{'=' * 70}")
            print("✓ SACRAMENTO COUNTY VOTERS PROCESSING COMPLETE")
            print(f"{'=' * 70}")
        else:
            print(f"\n{'=' * 70}")
            print("✗ SACRAMENTO COUNTY VOTERS PROCESSING FAILED (partial)")
            print(f"{'=' * 70}")

        return all_ok

    except Exception as exc:
        print(f"\n✗ Error processing SacCountyVoters order: {exc}")
        import traceback
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────────────────────────────────────
# PHASE CONTRACT CREATION
# ─────────────────────────────────────────────────────────────────────────────

def _create_phase_contract(
    etere: EtereClient,
    phase: SacCountyVotersPhase,
    phase_inputs: dict,
    customer_id: Optional[int],
    notes: str,
    separation: tuple,
) -> bool:
    """
    Create a single Etere contract for one phase of a SacCountyVoters order.

    Args:
        etere:         EtereClient instance
        phase:         Parsed phase data
        phase_inputs:  Dict with 'contract_code', 'description'
        customer_id:   Etere customer ID (or None)
        notes:         Contract notes
        separation:    (customer, event, order) separation tuple

    Returns:
        True on success, False on failure.
    """
    try:
        code        = phase_inputs.get('contract_code', f"SAC VTR PH{phase.phase_number}")
        description = phase_inputs.get('description', f"Sacramento County Phase {phase.phase_number}")

        print(f"\n[SAC PH{phase.phase_number}] Creating contract: {code}")
        print(f"[SAC PH{phase.phase_number}] Flight: {phase.flight_start} – {phase.flight_end}")

        contract_number = etere.create_contract_header(
            customer_id=customer_id,
            code=code,
            description=description,
            contract_start=phase.flight_start,
            contract_end=phase.flight_end,
            customer_order_ref=None,
            notes=notes,
            charge_to=BillingType.CUSTOMER_DIRECT.get_charge_to(),
            invoice_header=BillingType.CUSTOMER_DIRECT.get_invoice_header(),
        )

        if not contract_number:
            print(f"[SAC PH{phase.phase_number}] ✗ Failed to create contract header")
            return False

        print(f"[SAC PH{phase.phase_number}] ✓ Contract created: {contract_number}")

        line_count = 0

        for line in phase.lines:
            if line.total_spots == 0:
                print(f"  [{line.language}] skipped (0 spots)")
                continue

            spot_code = 10 if line.is_bonus else 2

            if line.is_bonus:
                # ROS bonus: look up schedule by language keyword
                ros_key = _language_to_ros_key(line.language)
                if ros_key and ros_key in ROS_SCHEDULES:
                    sched = ROS_SCHEDULES[ros_key]
                    ros_days = sched['days']
                    ros_time = sched['time']
                    time_from, time_to = EtereClient.parse_time_range(ros_time)
                else:
                    ros_days = "M-Su"
                    time_from, time_to = "06:00", "23:59"

                line_description = f"BNS {line.language} ROS"
                adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(ros_days, f"{time_from}-{time_to}")

                ranges = EtereClient.consolidate_weeks(
                    line.weekly_spots,
                    phase.week_columns,
                    flight_end=phase.flight_end,
                )

                print(f"\n  [{line.language}] BONUS  {line_description}")
                print(f"    Days: {adjusted_days}  Time: {time_from}–{time_to}")
                print(f"    Splits into {len(ranges)} Etere line(s)")

                for rng in ranges:
                    line_count += 1
                    total_spots_rng = rng['spots_per_week'] * rng['weeks']
                    print(f"    Creating line {line_count}: "
                          f"{rng['start_date']} – {rng['end_date']} "
                          f"({rng['spots_per_week']} spots/wk × {rng['weeks']} wks = {total_spots_rng})")

                    ok = etere.add_contract_line(
                        contract_number=contract_number,
                        market=SAC_MARKET,
                        start_date=rng['start_date'],
                        end_date=rng['end_date'],
                        days=adjusted_days,
                        time_from=time_from,
                        time_to=time_to,
                        description=line_description,
                        spot_code=spot_code,
                        duration_seconds=phase.duration_seconds,
                        total_spots=total_spots_rng,
                        spots_per_week=rng['spots_per_week'],
                        rate=0.0,
                        separation_intervals=separation,
                        is_bookend=False,
                        is_billboard=False,
                    )
                    if not ok:
                        print(f"    ✗ Failed to add line {line_count}")
                        return False

            else:
                # Paid line: parse daypart → days + time
                etere_days, time_range_str = _parse_daypart(line.daypart)
                time_from, time_to = EtereClient.parse_time_range(time_range_str)

                # Build description: "{days} {time} {language}"
                line_description = f"{etere_days} {time_range_str} {line.language}"

                adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(etere_days, time_range_str)

                ranges = EtereClient.consolidate_weeks(
                    line.weekly_spots,
                    phase.week_columns,
                    flight_end=phase.flight_end,
                )

                print(f"\n  [{line.language}] PAID  ${line.rate}")
                print(f"    Daypart: {line.daypart!r}  →  {etere_days}  {time_range_str}")
                print(f"    Splits into {len(ranges)} Etere line(s)")

                for rng in ranges:
                    line_count += 1
                    total_spots_rng = rng['spots_per_week'] * rng['weeks']
                    print(f"    Creating line {line_count}: "
                          f"{rng['start_date']} – {rng['end_date']} "
                          f"({rng['spots_per_week']} spots/wk × {rng['weeks']} wks = {total_spots_rng})")

                    ok = etere.add_contract_line(
                        contract_number=contract_number,
                        market=SAC_MARKET,
                        start_date=rng['start_date'],
                        end_date=rng['end_date'],
                        days=adjusted_days,
                        time_from=time_from,
                        time_to=time_to,
                        description=line_description,
                        spot_code=spot_code,
                        duration_seconds=phase.duration_seconds,
                        total_spots=total_spots_rng,
                        spots_per_week=rng['spots_per_week'],
                        rate=line.rate,
                        separation_intervals=separation,
                        is_bookend=False,
                        is_billboard=False,
                    )
                    if not ok:
                        print(f"    ✗ Failed to add line {line_count}")
                        return False

        print(f"\n[SAC PH{phase.phase_number}] ✓ All {line_count} Etere lines added")
        return True

    except Exception as exc:
        print(f"\n[SAC PH{phase.phase_number}] ✗ Error: {exc}")
        import traceback
        traceback.print_exc()
        return False
