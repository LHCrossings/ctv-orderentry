"""
GaleForceMedia Generic Order Automation

Handles browser automation for GaleForceMedia orders that are NOT Sagent
(e.g. BMO/PACO Collective BO-3189 style).

Business Rules:
- Single-market order (market from PDF header)
- Customer: looked up by advertiser name in DB
- Gross-up: ask per order (net / 0.85 if yes)
- Separation: (25, 0, 0)
- Description: "{days} {time} {program.title()}" with BNS prefix for bonus
- Master market: NYC (standard for Crossings TV)
"""

import os
import sys
from pathlib import Path
from typing import Optional

# Add project root to path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from browser_automation.etere_client import EtereClient
from browser_automation.parsers.galeforce_parser import (
    GaleForceOrder,
    parse_galeforce_pdf,
)
from browser_automation.ros_definitions import ROS_SCHEDULES
from src.domain.enums import OrderType, SeparationInterval

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

GALEFORCE_SEPARATION = SeparationInterval.GALEFORCE.value  # (25, 0, 0)
from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH

# ─────────────────────────────────────────────────────────────────────────────
# DATE / DURATION HELPERS (direct DB)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date(s):
    """Parse MM/DD/YYYY, MM/DD/YY, or date objects to datetime.date."""
    from datetime import date, datetime
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

def _create_galeforce_contract_direct(order: GaleForceOrder, inputs: dict) -> Optional[int]:
    """
    Enter GaleForce order directly via DB stored procedures (no browser).
    Returns contract_id on success, None on failure (rolls back fully).
    """
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = inputs.get('customer_id')
    if customer_id is None:
        print("[PACO DIRECT] ✗ No customer_id — cannot enter without a known ID")
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
            customer_order_ref=order.order_number,
        )
        print(f"[PACO DIRECT] ✓ Contract header ID={contract_id}")

        separation = inputs.get('separation', GALEFORCE_SEPARATION)
        line_count = 0

        for line in order.lines:
            if line.total_spots == 0:
                continue

            is_bonus     = line.is_bonus
            booking_code = 10 if is_bonus else 2
            duration_str = _secs_to_duration(line.get_duration_seconds())

            if inputs.get('gross_up') and not is_bonus:
                rate = _gross_up(line.net_rate)
            else:
                rate = line.net_rate  # bonus lines are always 0

            etere_days   = line.get_etere_days()
            etere_time   = line.get_etere_time()
            description  = line.get_description(etere_days, etere_time)
            description  = f"(Line {line.line_number}) {description}"

            time_from, time_to = EtereClient.parse_time_range(etere_time)
            time_range         = f"{time_from}-{time_to}"
            adjusted_days, _   = EtereClient.check_sunday_6_7a_rule(etere_days, etere_time)

            # ROS override for all-day bonus lines
            is_ros = (
                is_bonus
                and not line.is_billboard
                and line.length in (':15', ':30')
                and etere_time == '12a-12a'
            )
            if is_ros:
                prog_lower = line.program.lower()
                for language, sched in ROS_SCHEDULES.items():
                    if language.lower() in prog_lower:
                        time_from, time_to = EtereClient.parse_time_range(sched['time'])
                        time_range   = f"{time_from}-{time_to}"
                        adjusted_days = etere_days
                        description  = f"(Line {line.line_number}) {etere_days} BNS {language} ROS"
                        print(f"    [ROS] {language} — {sched['time']}, desc: {description!r}")
                        break

            ranges = EtereClient.consolidate_weeks(
                line.weekly_spots,
                order.week_start_dates,
                flight_end=order.flight_end,
            )

            for rng in ranges:
                line_count  += 1
                total_spots  = rng['spots_per_week'] * rng['weeks']
                print(f"  [LINE {line_count}] {description}: "
                      f"{rng['start_date']}–{rng['end_date']} "
                      f"({rng['spots_per_week']}/wk×{rng['weeks']}w={total_spots})")
                client.add_contract_line(
                    market=order.market,
                    days=adjusted_days,
                    time_range=time_range,
                    description=description,
                    rate=float(rate),
                    total_spots=total_spots,
                    spots_per_week=rng['spots_per_week'],
                    date_from=_parse_date(rng['start_date']),
                    date_to=_parse_date(rng['end_date']),
                    duration=duration_str,
                    is_bonus=is_bonus,
                    is_billboard=line.is_billboard,
                    booking_code=booking_code,
                    separation_intervals=separation,
                )

        conn.commit()
        conn.close()
        print(f"[PACO DIRECT] ✓ {line_count} lines committed.")
        return contract_id

    except Exception as exc:
        print(f"[PACO DIRECT] ✗ {exc}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


def process_galeforce_order_direct(pdf_path: str, user_input: dict) -> Optional[int]:
    """Direct DB entry point for the order processing service (no browser needed)."""
    order = parse_galeforce_pdf(pdf_path)
    return _create_galeforce_contract_direct(order, user_input)


# ─────────────────────────────────────────────────────────────────────────────
# CUSTOMER DATABASE LOOKUP
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_customer(client_name: str) -> Optional[dict]:
    """Look up GaleForce customer in the database by advertiser name."""
    if not os.path.exists(CUSTOMER_DB_PATH):
        return None
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        customer = (
            repo.find_by_name(client_name, OrderType.GALEFORCE) or
            repo.find_by_name_fuzzy(client_name, OrderType.GALEFORCE)
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
    """Save a new GaleForce customer to the database."""
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        repo = CustomerRepository(db_path)
        customer = Customer(
            customer_id=customer_id,
            customer_name=customer_name,
            order_type=OrderType.GALEFORCE,
            abbreviation=customer_name[:6].upper(),
            default_market="LAX",
            billing_type="agency",
            separation_customer=GALEFORCE_SEPARATION[0],
            separation_event=GALEFORCE_SEPARATION[1],
            separation_order=GALEFORCE_SEPARATION[2],
        )
        repo.save(customer)
        print(f"[CUSTOMER DB] ✓ Saved: {customer_name} → ID {customer_id}")
    except Exception as exc:
        print(f"[CUSTOMER DB] ✗ Save failed: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# UPFRONT INPUT GATHERING
# ─────────────────────────────────────────────────────────────────────────────

def gather_galeforce_inputs(pdf_path: str) -> Optional[dict]:
    """
    Parse PDF and gather all user inputs BEFORE the browser session opens.

    Called by the orchestrator upfront-gathering phase.

    Returns:
        Dict with contract_code, description, notes, gross_up (bool),
        customer_id (int or None), separation.
        Returns None if the user cancels.
    """
    print("\n" + "=" * 70)
    print("PACO ORDER - INPUT COLLECTION")
    print("=" * 70)

    print("\n[PARSE] Reading PDF…")
    try:
        order = parse_galeforce_pdf(pdf_path)
    except Exception as exc:
        print(f"[PARSE] ✗ Failed: {exc}")
        return None

    print(f"\nAdvertiser:  {order.advertiser}")
    print(f"Campaign:    {order.campaign}")
    print(f"Flight:      {order.flight_start} – {order.flight_end}")
    print(f"Market:      {order.market}")
    print(f"Order #:     {order.order_number}")
    print(f"Estimate #:  {order.estimate_number} (stripped: {order.estimate_stripped})")
    print(f"Agency:      {order.agency}")
    print(f"Lines:       {len(order.lines)}")
    print(f"Total spots: {sum(l.total_spots for l in order.lines)}")
    print()

    # ── Customer lookup ──────────────────────────────────────────────────────
    customer_info = _lookup_customer(order.advertiser)
    customer_id: Optional[int] = None

    if customer_info:
        customer_id = customer_info['customer_id']
        print(f"[CUSTOMER] ✓ Found in DB: {order.advertiser} → ID {customer_id}")
    else:
        print(f"[CUSTOMER] Not found in DB for '{order.advertiser}'")
        raw_id = input("  Enter Etere customer ID (or blank to select in browser): ").strip()
        if raw_id.isdigit():
            customer_id = int(raw_id)
            save_yn = input(f"  Save '{order.advertiser}' (ID {customer_id}) to DB for next time? (y/n): ").strip().lower()
            if save_yn == 'y':
                _save_new_customer(str(customer_id), order.advertiser)
    print()

    # ── Contract code / description / notes ───────────────────────────────────
    default_code  = order.get_default_contract_code()
    default_desc  = order.get_default_description()
    default_notes = order.get_default_notes()

    raw = input(f"  Contract code [{default_code}]: ").strip()
    contract_code = raw or default_code

    raw = input(f"  Description   [{default_desc}]: ").strip()
    description = raw or default_desc

    raw = input(f"  Notes         [{default_notes}]: ").strip()
    notes = raw or default_notes

    # ── Gross-up ─────────────────────────────────────────────────────────────
    gross_yn = input("  Apply gross-up (net ÷ 0.85)? [y/N]: ").strip().lower()
    gross_up = gross_yn == 'y'

    return {
        'contract_code': contract_code,
        'description': description,
        'notes': notes,
        'gross_up': gross_up,
        'customer_id': customer_id,
        'separation': GALEFORCE_SEPARATION,
    }

