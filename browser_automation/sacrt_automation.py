"""
SacRT / Sacramento Regional Transit direct DB automation.

One PDF = one flight = one Etere contract. All lines air in CVC. Lines have no
weekly cadence — a flight-total spot count only — so they are entered with
spots_per_week=0, which makes EtereDirectClient auto-select Rotation scheduling.

SacRT (customer 442) is BILLED DIRECT. 3Fold Communications directed the buy
but is NOT on the contract: no agency_id, no lookup_customer_defaults —
ANAGRAF 442 has AGENZIA=0, so the header resolves to no agency and no
commission. Rates are printed NET and entered as-is (net = gross with no
commission).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
from browser_automation.etere_client import EtereClient
from browser_automation.parsers.sacrt_parser import SacRTDocument, parse_sacrt_pdf

# SacRT is always Etere customer 442 (direct — no agency).
DEFAULT_CUSTOMER_ID = 442
DEFAULT_CLIENT_NAME = "Sacramento Regional Transit"


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _fmt(d: date) -> str:
    """date → 'M/D/YY' (cross-platform, lesson #13)."""
    return f"{d.month}/{d.day}/{d.strftime('%y')}"


def _parse_user_date(s: str) -> date:
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d', '%m/%d'):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


def _broadcast_month_start(year: int, month: int) -> date:
    """Monday of the week containing the 1st of (year, month)."""
    first = date(year, month, 1)
    return first - timedelta(days=first.weekday())


def _date_to_broadcast_ym(d: date) -> str:
    """Return 'yymm' for the BROADCAST month containing a calendar date.

    A broadcast month starts on the Monday of the week containing the 1st of
    that calendar month, so e.g. 10/30/2026 is broadcast November → '2611'.
    """
    year, month = d.year, d.month
    next_year = year + 1 if month == 12 else year
    next_month = 1 if month == 12 else month + 1
    if d >= _broadcast_month_start(next_year, next_month):
        year, month = next_year, next_month
    elif d < _broadcast_month_start(year, month):
        year = year - 1 if month == 1 else year
        month = 12 if month == 1 else month - 1
    return f"{year % 100:02d}{month:02d}"


def _lookup_customer(client_name: str):
    try:
        import os

        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.enums import OrderType
        if not os.path.exists(CUSTOMER_DB_PATH):
            return None
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        return (
            repo.find_by_name(client_name, OrderType.SACRT)
            or repo.find_by_name_any_type(client_name)
        )
    except Exception as exc:
        print(f"[CUSTOMER] Warning: lookup failed: {exc}")
        return None


def _upsert_customer(customer_id: str, client_name: str, separation: tuple, billing_type: str = 'direct') -> None:
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
            order_type=OrderType.SACRT,
            billing_type=billing_type,
            separation_customer=separation[0],
            separation_event=separation[1],
            separation_order=separation[2],
        ))
        print(f"[CUSTOMER DB] ✓ Saved: {client_name} → ID {customer_id}")
    except Exception as exc:
        print(f"[CUSTOMER DB] Warning: could not save: {exc}")


def _confirm_start_date(doc: SacRTDocument) -> Optional[date]:
    """
    Lesson #14: if the order's earliest start is tomorrow or earlier, confirm it.
    Returns the (possibly overridden) earliest start, or None if none found.
    """
    earliest = doc.flight_start_date
    if earliest is None:
        return None
    if earliest > date.today() + timedelta(days=1):
        return earliest
    print(f"\n  ⚠ This order starts {_fmt(earliest)} (today is {_fmt(date.today())}).")
    raw = input(f"  Confirm start date [{_fmt(earliest)}]: ").strip()
    if raw and raw.lower() not in ('y', 'yes'):
        try:
            return _parse_user_date(raw)
        except ValueError:
            print(f"  ✗ Could not parse '{raw}' — keeping {_fmt(earliest)}")
    return earliest


# ─── Input Gather ─────────────────────────────────────────────────────────────

def gather_sacrt_inputs(pdf_path: str) -> Optional[dict]:
    """
    Gather inputs for a SacRT order: contract code + description, customer ID
    (defaults to 442), separation. Billing is DIRECT — no agency prompt.

    Returns a dict, or None to abort.
    """
    doc = parse_sacrt_pdf(pdf_path)

    print(f"\n{'='*64}")
    print(f"Customer: {DEFAULT_CLIENT_NAME}  (Etere customer ID {DEFAULT_CUSTOMER_ID})")
    print("Billing:  DIRECT — no agency, no commission (3Fold directed the buy only)")
    print(f"Market:   {', '.join(doc.markets)}")
    print(f"Campaign: {doc.campaign}")
    print(f"\n  Flight {doc.flight_start}–{doc.flight_end}  "
          f"{len(doc.lines)} lines, {doc.total_spots} spots "
          f"({doc.paid_spots} paid), ${doc.total_cost:,.2f} net")

    # ── Start-date sanity check (lesson #14) ──────────────────────────────
    start_override = _confirm_start_date(doc)
    if start_override is None:
        print("  ✗ No flight start date found — aborting")
        return None

    # ── Customer — defaults to SacRT / 442, billed direct ─────────────────
    raw = input(f"\n  Customer / advertiser name [{DEFAULT_CLIENT_NAME}]: ").strip()
    client = raw or DEFAULT_CLIENT_NAME

    customer_id = DEFAULT_CUSTOMER_ID
    billing_type = 'direct'
    separation = (15, 0, 0)

    cust = _lookup_customer(client)
    if cust:
        customer_id = int(cust.customer_id) if str(cust.customer_id).isdigit() else DEFAULT_CUSTOMER_ID
        billing_type = cust.billing_type or 'direct'
        s0 = 25 if cust.separation_customer == 30 else cust.separation_customer
        separation = (s0, cust.separation_event, cust.separation_order)
        print(f"[CUSTOMER] ✓ '{client}' in DB → ID {customer_id}, billing={billing_type}, sep {separation}")
    else:
        print(f"[CUSTOMER] '{client}' not in customers.db — defaulting to ANAGRAF ID {DEFAULT_CUSTOMER_ID}.")
        raw_id = input(f"  Customer (ANAGRAF) ID [{DEFAULT_CUSTOMER_ID}]: ").strip()
        customer_id = int(raw_id) if raw_id.isdigit() else DEFAULT_CUSTOMER_ID
        _upsert_customer(str(customer_id), client, separation, billing_type)

    # ── Contract code + description (broadcast-month yymm) ────────────────
    start_bym = _date_to_broadcast_ym(doc.flight_start_date)
    end_bym = _date_to_broadcast_ym(doc.flight_end_date)
    bym_span = start_bym if start_bym == end_bym else f"{start_bym}-{end_bym}"
    default_code = f"SacRT {start_bym}"
    default_desc = f"SacRT {doc.campaign} {bym_span}".strip()

    raw = input(f"\n  Contract code [{default_code}]: ").strip()
    contract_code = raw or default_code
    raw = input(f"  Description [{default_desc}]: ").strip()
    description = raw or default_desc

    return {
        'customer_id': customer_id,
        'client': client,
        'billing_type': billing_type,
        'separation': separation,
        'contract_code': contract_code,
        'description': description,
        'start_date_override': start_override.strftime('%m/%d/%Y'),
    }


# ─── Direct DB Entry ────────────────────────────────────────────────────────

def run_sacrt_order(doc: SacRTDocument, inputs: dict) -> list[tuple[str, bool]]:
    """
    Enter a SacRT order — one Etere contract, billed direct (no agency).

    Returns [(contract_code, success)] for the handler's contracts list.
    """
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = inputs.get('customer_id') or DEFAULT_CUSTOMER_ID
    separation = inputs.get('separation', (15, 0, 0))
    billing_type = inputs.get('billing_type', 'direct')
    contract_code = inputs['contract_code']
    description = inputs['description']

    flight_start = doc.flight_start_date
    flight_end = doc.flight_end_date
    if not flight_start or not flight_end:
        print("[SACRT] ✗ No flight range")
        return [(contract_code, False)]

    original_earliest = flight_start
    ov = inputs.get('start_date_override')
    override_start = _parse_user_date(ov) if ov else original_earliest

    header_start = override_start if override_start != original_earliest else flight_start

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=contract_code,
            description=description,
            customer_id=int(customer_id),
            # DIRECT BILL: no agency_id, no lookup_customer_defaults.
            # ANAGRAF 442 has AGENZIA=0 → agency resolves to 0, no commission.
            contract_date=header_start,
            contract_end_date=flight_end,
            contract_type=1,
            billing_type=billing_type,
            allow_rename=True,
        )
        print(f"[SACRT] ✓ Header: ID={contract_id} code='{contract_code}'")

        line_count = 0
        for ln in doc.lines:
            if ln.total_spots == 0:
                continue
            booking_code = 10 if ln.is_bonus else 2

            days = ln.days
            time_raw = ln.time
            days, _ = EtereClient.check_sunday_6_7a_rule(days, time_raw)
            time_from, time_to = EtereClient.parse_time_range(time_raw)
            time_range = f"{time_from}-{time_to}"

            date_from = ln.start
            if override_start != original_earliest and date_from == original_earliest:
                date_from = override_start
            date_to = ln.end

            line_count += 1
            print(f"  [LINE {line_count}] {ln.description} :{ln.duration} "
                  f"{days} {time_range}  {_fmt(date_from)}–{_fmt(date_to)}  "
                  f"spots={ln.total_spots} rate=${ln.rate} {'BNS' if ln.is_bonus else ''}")
            client.add_contract_line(
                market=ln.market,
                days=days,
                time_range=time_range,
                description=ln.description,
                rate=ln.rate,               # NET entered as-is — direct bill, no gross-up
                total_spots=ln.total_spots,
                spots_per_week=0,           # flight-total → Rotation
                max_daily_run=1,            # few spots over a long flight — never 2 in one day
                date_from=date_from,
                date_to=date_to,
                duration=str(ln.duration),
                is_bonus=ln.is_bonus,
                booking_code=booking_code,
                separation_intervals=separation,
            )

        conn.commit()
        conn.close()
        print(f"[SACRT] ✓ {line_count} lines committed.")
        return [(contract_code, True)]

    except Exception as exc:
        print(f"[SACRT] ✗ {exc}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return [(contract_code, False)]
