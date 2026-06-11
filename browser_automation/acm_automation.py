"""
ACM (American Community Media) direct DB automation.

Creates ONE Etere contract for the entire order. Each market section's lines
are entered under that single contract, with each line carrying its own market code.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
from browser_automation.etere_client import EtereClient
from browser_automation.parsers.acm_parser import AcmOrder, parse_acm_xlsx

# ─── Month abbreviations (for consolidate_weeks date format) ─────────────────
_MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _fmt_mon_dd(d: date) -> str:
    """date → 'Jun 15' (consolidate_weeks input format)."""
    return f"{_MONTH_ABBR[d.month - 1]} {d.day}"


def _fmt_mmddyyyy(d: date) -> str:
    """date → 'MM/DD/YYYY' (EtereDirectClient date format)."""
    return d.strftime('%m/%d/%Y')


def _parse_date(s) -> date:
    """Parse 'MM/DD/YYYY' string or date object → date."""
    if isinstance(s, date):
        return s
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


def _lookup_customer(client_name: str):
    try:
        import os

        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.enums import OrderType
        if not os.path.exists(CUSTOMER_DB_PATH):
            return None
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        # find_by_name_any_type does case-insensitive name match across all order types,
        # which catches manually-entered records where order_type casing may differ.
        return (
            repo.find_by_name(client_name, OrderType.ACM)
            or repo.find_by_name_any_type(client_name)
        )
    except Exception as exc:
        print(f"[CUSTOMER] Warning: lookup failed: {exc}")
        return None


def _upsert_customer(customer_id: str, client_name: str, separation: tuple) -> None:
    try:
        import os

        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        from src.domain.enums import OrderType
        if not os.path.exists(CUSTOMER_DB_PATH):
            return
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        repo.save(Customer(
            customer_id=customer_id,
            customer_name=client_name,
            order_type=OrderType.ACM,
            billing_type='agency',
            separation_customer=separation[0],
            separation_event=separation[1],
            separation_order=separation[2],
        ))
        print(f"[CUSTOMER DB] ✓ Saved: {client_name} → ID {customer_id}")
    except Exception as exc:
        print(f"[CUSTOMER DB] Warning: could not save: {exc}")


# ─── Input Gather ─────────────────────────────────────────────────────────────

def gather_acm_inputs(xlsx_path: str) -> Optional[dict]:
    """
    Gather user inputs for an ACM order before processing.

    Returns dict with: customer_id, separation, spot_duration, contract_code, description.
    Returns None to abort.
    """
    order = parse_acm_xlsx(xlsx_path)

    print(f"\n{'='*60}")
    print(f"ACM: {order.agency}")
    if order.order_date:
        print(f"Date: {order.order_date:%B %d, %Y}")
    print(f"Markets: {', '.join(m.market_code for m in order.market_sections)}")

    for mkt in order.market_sections:
        print(f"\n  {mkt.market_code}:")
        for ln in mkt.lines:
            tag = "BNS" if ln.is_bonus else "   "
            rate_str = f"${ln.rate:.0f}/sp" if ln.rate else "      "
            spots = ' + '.join(str(s) for s in ln.week_spots)
            print(f"    {tag}  {ln.language_block.strip():<40} {ln.daypart:<22} {rate_str}  [{spots}]")

    # ── Customer lookup ───────────────────────────────────────────────────
    customer_id: Optional[int] = None
    separation = (15, 0, 0)

    billing_type = 'direct'   # ACM default; overridden by DB record if present
    cust = _lookup_customer(order.agency)
    if cust:
        customer_id  = cust.customer_id
        billing_type = cust.billing_type or 'direct'
        s0 = 25 if cust.separation_customer == 30 else cust.separation_customer
        separation = (s0, cust.separation_event, cust.separation_order)
        print(f"\n[CUSTOMER] ✓ Found in DB → ID {customer_id}, billing={billing_type}, sep {separation}")
    else:
        print(f"\n[CUSTOMER] '{order.agency}' not found in DB.")
        raw_id = input("  Enter Etere customer ID: ").strip()
        if not raw_id.isdigit():
            print("  ✗ Invalid ID — aborting")
            return None
        customer_id = int(raw_id)
        save = input(
            f"  Save '{order.agency}' (ID {customer_id}) to DB? (y/n): "
        ).strip().lower()
        if save in ('y', 'yes'):
            _upsert_customer(str(customer_id), order.agency, separation)

    # ── Spot duration ─────────────────────────────────────────────────────
    raw = input("\n  Spot duration in seconds [30]: ").strip()
    spot_duration = int(raw) if raw.isdigit() else 30

    # ── Single contract code + description (covers all markets) ──────────
    code_name = (cust.code_name        or 'ACM')                      if cust else 'ACM'
    desc_name = (cust.description_name or 'American Community Media') if cust else 'American Community Media'

    # Use overall flight range across all markets for the default description
    start_d = _parse_date(order.flight_start) if order.flight_start else None
    end_d   = _parse_date(order.flight_end)   if order.flight_end   else None
    flight_str = ""
    if start_d and end_d:
        flight_str = (
            f"{start_d.month}/{start_d.day}"
            f"-{end_d.month}/{end_d.day}/{end_d.strftime('%y')}"
        )
    default_code = code_name
    default_desc = f"{desc_name} {flight_str}".strip()

    print()
    raw = input(f"  Contract code [{default_code}]: ").strip()
    contract_code = raw or default_code

    raw = input(f"  Description [{default_desc}]: ").strip()
    description = raw or default_desc

    return {
        'customer_id':   customer_id,
        'billing_type':  billing_type,
        'separation':    separation,
        'spot_duration': spot_duration,
        'contract_code': contract_code,
        'description':   description,
    }


# ─── Direct DB Entry ──────────────────────────────────────────────────────────

def _create_acm_contract(order: AcmOrder, inputs: dict) -> Optional[str]:
    """
    Enter an ACM order into Etere as a single contract.

    All market sections are written as lines under one contract header.
    Returns the contract code on success, None on failure (rolls back).
    """
    from browser_automation.etere_direct_client import EtereDirectClient, connect
    from browser_automation.ros_definitions import ROS_SCHEDULES

    customer_id   = inputs.get('customer_id')
    if customer_id is None:
        print("[ACM] ✗ No customer_id")
        return None

    separation    = inputs.get('separation', (15, 0, 0))
    billing_type  = inputs.get('billing_type', 'direct')
    contract_code = inputs.get('contract_code', 'ACM')
    description   = inputs.get('description', '')
    spot_duration = inputs.get('spot_duration', 30)
    duration_str  = str(spot_duration)

    # Overall flight range across all markets
    flight_start_d = _parse_date(order.flight_start) if order.flight_start else None
    flight_end_d   = _parse_date(order.flight_end)   if order.flight_end   else None
    if not flight_start_d or not flight_end_d:
        print("[ACM] ✗ Could not determine overall flight range")
        return None

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=contract_code,
            description=description,
            customer_id=int(customer_id),
            contract_date=flight_start_d,
            contract_end_date=flight_end_d,
            contract_type=1,
            billing_type=billing_type,
            allow_rename=True,
        )
        print(f"[ACM] ✓ Contract header: ID={contract_id}  code='{contract_code}'")

        line_count = 0

        for mkt in order.market_sections:
            flight_end_str = _fmt_mmddyyyy(
                mkt.flight_end or flight_end_d
            )

            for line in mkt.lines:
                if line.total_spots == 0:
                    continue

                is_bonus     = line.is_bonus
                booking_code = 10 if is_bonus else 2

                if is_bonus:
                    lang = line.language_block.strip()
                    ros  = ROS_SCHEDULES.get(lang)
                    if ros:
                        days     = ros['days']
                        time_raw = ros['time']
                    else:
                        days, time_raw = 'M-Su', '6a-11:59p'
                        print(f"  [WARN] No ROS schedule for '{lang}' — using {days} {time_raw}")
                    desc = f"BNS {lang} ROS"
                else:
                    days     = line.days
                    time_raw = line.time
                    days, _  = EtereClient.check_sunday_6_7a_rule(days, time_raw)
                    label    = line.language_block.strip()
                    desc     = f"{label} {line.daypart}"
                    if len(desc) > 60:
                        desc = desc[:60]

                time_from, time_to = EtereClient.parse_time_range(time_raw)
                time_range = f"{time_from}-{time_to}"

                week_start_strs = [_fmt_mon_dd(d) for d in line.week_dates]
                ranges = EtereClient.consolidate_weeks(
                    line.week_spots,
                    week_start_strs,
                    flight_end=flight_end_str,
                )

                for rng in ranges:
                    total_spots = rng['spots_per_week'] * rng['weeks']
                    line_count += 1
                    print(
                        f"  [LINE {line_count}] {mkt.market_code} {desc}: "
                        f"{rng['start_date']}–{rng['end_date']} "
                        f"({rng['spots_per_week']}/wk×{rng['weeks']}w={total_spots})"
                    )
                    client.add_contract_line(
                        market=mkt.market_code,
                        days=days,
                        time_range=time_range,
                        description=desc,
                        rate=line.rate,
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
        print(f"[ACM] ✓ {line_count} lines committed across {len(order.market_sections)} markets.")
        return contract_code

    except Exception as exc:
        print(f"[ACM] ✗ {exc}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


def run_acm_order(order: AcmOrder, inputs: dict) -> list[tuple[str, bool]]:
    """
    Process an ACM order as a single contract covering all markets.

    Returns list with one (contract_code, success) tuple.
    """
    code = _create_acm_contract(order, inputs)
    label = inputs.get('contract_code') or 'ACM'
    return [(label, code is not None)]
