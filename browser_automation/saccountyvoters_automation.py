"""
Sacramento County Voter Registration Automation

Handles browser automation for Sacramento County Voter Registration
insertion orders. The order has two phases with different durations:
  Phase 1: :15s, Apr–May
  Phase 2: :30s, May–Jun

Each phase becomes a separate Etere contract.
Market: CVC, Separation: (15, 0, 0).
"""

import math
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Add project root to path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from browser_automation.etere_client import EtereClient
from browser_automation.parsers.saccountyvoters_parser import (
    parse_saccountyvoters_pdf,
)
from browser_automation.ros_definitions import ROS_SCHEDULES
from src.domain.enums import OrderType, SeparationInterval

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

SAC_MARKET = "CVC"
SAC_SEPARATION = SeparationInterval.SACCOUNTYVOTERS.value   # (15, 0, 0)
from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH

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
            separation_order=SAC_SEPARATION[1],
            separation_event=SAC_SEPARATION[2],
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
        paid  = [ln for ln in phase.lines if not ln.is_bonus]
        bonus = [ln for ln in phase.lines if ln.is_bonus]
        total = sum(ln.total_spots for ln in phase.lines)
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

    # Silently upsert customer to DB
    if customer_id is not None:
        try:
            import sys as _sys
            from pathlib import Path as _Path
            _src = _Path(__file__).parent.parent / "src"
            if str(_src) not in _sys.path:
                _sys.path.insert(0, str(_src))
            from data_access.repositories.customer_repository import CustomerRepository as _CR
            from domain.entities import Customer as _Cust
            from domain.enums import OrderType as _OT
            _repo = _CR(CUSTOMER_DB_PATH)
            _repo.save(_Cust(
                customer_id=str(customer_id),
                customer_name=order.client,
                order_type=_OT.SACCOUNTYVOTERS,
                billing_type="client",
            ))
        except Exception:
            pass

    print()

    # ── Phase 1 inputs ────────────────────────────────────────────────────────
    # yymm from flight start: "04/07/2026" → "2604"
    fs1 = order.phases[0].flight_start   # MM/DD/YYYY
    yymm1 = fs1[8:10] + fs1[0:2]        # "26" + "04" = "2604"
    default_code_ph1 = f"Sac County Voters {yymm1}"
    raw = input(f"  Phase 1 contract code [{default_code_ph1}]: ").strip()
    code_ph1 = raw or default_code_ph1

    default_desc_ph1 = f"Sac County Voters Phase 1 {yymm1}"
    raw = input(f"  Phase 1 description   [{default_desc_ph1}]: ").strip()
    desc_ph1 = raw or default_desc_ph1

    # ── Phase 2 inputs ────────────────────────────────────────────────────────
    fs2 = order.phases[1].flight_start   # MM/DD/YYYY
    yymm2 = fs2[8:10] + fs2[0:2]        # "26" + "05" = "2605"

    default_code_ph2 = f"Sac County Voters {yymm2}"
    raw = input(f"  Phase 2 contract code [{default_code_ph2}]: ").strip()
    code_ph2 = raw or default_code_ph2

    default_desc_ph2 = f"Sac County Voters Phase 2 {yymm2}"
    raw = input(f"  Phase 2 description   [{default_desc_ph2}]: ").strip()
    desc_ph2 = raw or default_desc_ph2

    # ── Notes ─────────────────────────────────────────────────────────────────
    default_notes = f"Contact: {order.contact}\nEmail: {order.email}"
    print(f"\n  Notes default:\n    {default_notes.replace(chr(10), chr(10) + '    ')}")
    raw = input("  Notes [Enter to keep]: ").strip()
    notes = raw or default_notes

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
        'separation': SAC_SEPARATION,
    }


# ─────────────────────────────────────────────────────────────────────────────
# DIRECT DB ENTRY
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date(s: str):
    """Parse MM/DD/YYYY string to date object."""
    return datetime.strptime(s, '%m/%d/%Y').date()


def _secs_to_duration(seconds: int) -> str:
    return f":{seconds:02d}"


def _create_saccountyvoters_contracts_direct(order, inputs: dict) -> bool:
    """Enter both SacCountyVoters phases directly via DB (no browser).
    Returns True on full success, False on any failure.
    """
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = inputs.get('customer_id')
    if customer_id is None:
        print("[SAC DIRECT] ✗ No customer_id — cannot enter without a known ID")
        return False

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        notes      = inputs.get('notes', '')
        separation = inputs.get('separation', SAC_SEPARATION)

        phase_input_keys = ['phase1_inputs', 'phase2_inputs']

        for phase, input_key in zip(order.phases, phase_input_keys):
            phase_inputs = inputs.get(input_key, {})
            fs   = phase.flight_start
            yymm = fs[8:10] + fs[0:2]
            code        = phase_inputs.get('contract_code', f"Sac County Voters {yymm}")
            description = phase_inputs.get('description', f"Sacramento County Phase {phase.phase_number}")

            # Convert "4-May" → "May 4" for consolidate_weeks
            week_dates = [
                f"{label.split('-')[1]} {label.split('-')[0]}"
                for label in phase.week_columns
            ]

            print(f"\n[SAC PH{phase.phase_number} DIRECT] Creating: {code}")
            print(f"[SAC PH{phase.phase_number} DIRECT] Flight: {phase.flight_start} – {phase.flight_end}")

            contract_id = client.create_contract_header(
                code=code,
                description=description,
                customer_id=int(customer_id),
                contract_date=_parse_date(phase.flight_start),
                contract_end_date=_parse_date(phase.flight_end),
                billing_type="client",
                note=notes,
                allow_rename=True,
            )
            print(f"[SAC PH{phase.phase_number} DIRECT] ✓ Contract ID={contract_id}")

            line_count   = 0
            duration_str = _secs_to_duration(phase.duration_seconds)

            for line in phase.lines:
                if line.total_spots == 0:
                    print(f"  [{line.language}] skipped (0 spots)")
                    continue

                booking_code = 10 if line.is_bonus else 2

                if line.is_bonus:
                    ros_key = _language_to_ros_key(line.language)
                    if ros_key and ros_key in ROS_SCHEDULES:
                        sched      = ROS_SCHEDULES[ros_key]
                        ros_days   = sched['days']
                        ros_time   = sched['time']
                        time_from, time_to = EtereClient.parse_time_range(ros_time)
                        lang_label = sched.get('language', line.language)
                    else:
                        ros_days   = "M-Su"
                        time_from, time_to = "06:00", "23:59"
                        lang_label = line.language

                    line_description = f"BNS {lang_label} ROS"
                    adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(ros_days, f"{time_from}-{time_to}")
                    time_range       = f"{time_from}-{time_to}"

                    ranges = EtereClient.consolidate_weeks(
                        line.weekly_spots,
                        week_dates,
                        flight_end=phase.flight_end,
                        flight_start=phase.flight_start,
                    )

                    for rng in ranges:
                        line_count      += 1
                        total_spots_rng  = rng['spots_per_week'] * rng['weeks']
                        max_daily        = _compute_max_daily_run(
                            adjusted_days, rng['start_date'], rng['end_date'], rng['spots_per_week']
                        )
                        client.add_contract_line(
                            market=SAC_MARKET,
                            days=adjusted_days,
                            time_range=time_range,
                            description=line_description,
                            rate=0.0,
                            total_spots=total_spots_rng,
                            spots_per_week=rng['spots_per_week'],
                            max_daily_run=max_daily,
                            date_from=_parse_date(rng['start_date']),
                            date_to=_parse_date(rng['end_date']),
                            duration=duration_str,
                            is_bonus=True,
                            booking_code=booking_code,
                            separation_intervals=separation,
                            contract_id=contract_id,
                        )

                else:
                    etere_days, time_range_str = _parse_daypart(line.daypart)
                    time_from, time_to         = EtereClient.parse_time_range(time_range_str)
                    time_range                 = f"{time_from}-{time_to}"
                    lang_label                 = re.sub(r'(\w)\(', r'\1 (', line.language)
                    line_description           = f"{etere_days} {lang_label}"
                    adjusted_days, _           = EtereClient.check_sunday_6_7a_rule(etere_days, time_range_str)

                    ranges = EtereClient.consolidate_weeks(
                        line.weekly_spots,
                        week_dates,
                        flight_end=phase.flight_end,
                        flight_start=phase.flight_start,
                    )

                    for rng in ranges:
                        line_count      += 1
                        total_spots_rng  = rng['spots_per_week'] * rng['weeks']
                        max_daily        = _compute_max_daily_run(
                            adjusted_days, rng['start_date'], rng['end_date'], rng['spots_per_week']
                        )
                        client.add_contract_line(
                            market=SAC_MARKET,
                            days=adjusted_days,
                            time_range=time_range,
                            description=line_description,
                            rate=float(line.rate),
                            total_spots=total_spots_rng,
                            spots_per_week=rng['spots_per_week'],
                            max_daily_run=max_daily,
                            date_from=_parse_date(rng['start_date']),
                            date_to=_parse_date(rng['end_date']),
                            duration=duration_str,
                            booking_code=booking_code,
                            separation_intervals=separation,
                            contract_id=contract_id,
                        )

            print(f"[SAC PH{phase.phase_number} DIRECT] ✓ {line_count} lines added")

        conn.commit()
        conn.close()
        print("\n[SAC DIRECT] ✓ Both phases committed")
        return True

    except Exception as exc:
        print(f"[SAC DIRECT] ✗ {exc}")
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
# MAIN PROCESSING FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def process_saccountyvoters_order(
    pdf_path: str,
    shared_session=None,
    pre_gathered_inputs: Optional[dict] = None,
) -> bool:
    """
    Process a Sacramento County Voter Registration PDF and create contracts.

    Creates one Etere contract per phase (2 total) via direct DB entry.

    Args:
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

        return _create_saccountyvoters_contracts_direct(order, inputs)

    except Exception as exc:
        print(f"\n✗ Error processing SacCountyVoters order: {exc}")
        import traceback
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────────────────────────────────────
# MAX DAILY RUN CALCULATION
# ─────────────────────────────────────────────────────────────────────────────

_DAY_SETS = {
    'M-F':  {0, 1, 2, 3, 4},
    'M-Sa': {0, 1, 2, 3, 4, 5},
    'M-Su': {0, 1, 2, 3, 4, 5, 6},
    'Sa-Su': {5, 6},
    'Sa':   {5},
    'Su':   {6},
}

def _compute_max_daily_run(etere_days: str, start_date_str: str, end_date_str: str, spots_per_week: int) -> int:
    """
    Calculate max_daily_run based on actual eligible days within the first week
    of the date range, not just the day pattern's theoretical day count.

    Handles partial weeks correctly: if a flight ends on Monday with 2 spots
    scheduled that week, both must air Monday → max_daily_run = 2, not 1.
    """
    eligible = _DAY_SETS.get(etere_days, {0, 1, 2, 3, 4})
    start = datetime.strptime(start_date_str, '%m/%d/%Y')
    end = datetime.strptime(end_date_str, '%m/%d/%Y')

    # Count eligible days in first 7 days of range (one week's window)
    week_end = min(end, start + timedelta(days=6))
    count = sum(
        1 for i in range((week_end - start).days + 1)
        if (start + timedelta(days=i)).weekday() in eligible
    )
    return math.ceil(spots_per_week / count) if count > 0 else spots_per_week
