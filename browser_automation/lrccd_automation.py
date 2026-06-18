"""
LRCCD / 3Fold Communications direct DB automation.

One PDF carries two flights (FALL and SPRING); each flight becomes its own
Etere contract. All lines air in CVC and carry their own duration (:30 / :15).

Lines have no weekly cadence — a flight-total spot count only — so they are
entered with spots_per_week=0, which makes EtereDirectClient auto-select
Rotation scheduling.

LRCCD (customer 218) is an agency order placed by 3Fold Communications
(agency 203). Rates are GROSS; the 15% agency commission is recorded
automatically by create_contract_header via the ANAGRAF auto-populate path
(lookup_customer_defaults=True), so no gross-up is needed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
from browser_automation.etere_client import EtereClient
from browser_automation.parsers.lrccd_parser import (
    LRCCDDocument,
    LRCCDOrder,
    parse_lrccd_pdf,
)

# LRCCD is always Etere customer 218 (advertiser); 3Fold is the agency.
DEFAULT_CUSTOMER_ID = 218
DEFAULT_CLIENT_NAME = "Los Rios Community College"


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


def _lookup_customer(client_name: str):
    try:
        import os

        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.enums import OrderType
        if not os.path.exists(CUSTOMER_DB_PATH):
            return None
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        return (
            repo.find_by_name(client_name, OrderType.LRCCD)
            or repo.find_by_name_any_type(client_name)
        )
    except Exception as exc:
        print(f"[CUSTOMER] Warning: lookup failed: {exc}")
        return None


def _upsert_customer(customer_id: str, client_name: str, separation: tuple, billing_type: str = 'agency') -> None:
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
            order_type=OrderType.LRCCD,
            billing_type=billing_type,
            separation_customer=separation[0],
            separation_event=separation[1],
            separation_order=separation[2],
        ))
        print(f"[CUSTOMER DB] ✓ Saved: {client_name} → ID {customer_id}")
    except Exception as exc:
        print(f"[CUSTOMER DB] Warning: could not save: {exc}")


def _confirm_start_date(doc: LRCCDDocument) -> Optional[date]:
    """
    Lesson #15: if the order's earliest start is tomorrow or earlier, confirm it.
    Returns the (possibly overridden) earliest start, or None if none found.
    """
    if not doc.flight_start:
        return None
    earliest = _parse_user_date(doc.flight_start)
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

def gather_lrccd_inputs(pdf_path: str) -> Optional[dict]:
    """
    Gather inputs for an LRCCD order: per-flight contract code + description,
    customer ID (defaults to 218), billing type, separation.

    Returns a dict, or None to abort.
    """
    from browser_automation.etere_direct_client import AGENCY_IDS

    doc = parse_lrccd_pdf(pdf_path)

    print(f"\n{'='*64}")
    print(f"Agency:   3Fold Communications  (Etere agency ID {AGENCY_IDS['3FOLD']})")
    print(f"Customer: {DEFAULT_CLIENT_NAME}  (Etere customer ID {DEFAULT_CUSTOMER_ID})")
    print(f"Market:   {', '.join(doc.markets)}")
    for o in doc.orders:
        print(f"\n  {o.season}  ({_fmt(o.flight_start)}–{_fmt(o.flight_end)})  "
              f"{len(o.lines)} lines, {o.total_spots} spots, ${o.total_cost:,.2f} gross")

    # ── Start-date sanity check (lesson #15) ──────────────────────────────
    start_override = _confirm_start_date(doc)
    if start_override is None:
        print("  ✗ No flight start date found — aborting")
        return None

    # ── Customer (advertiser, not the agency) — defaults to LRCCD / 218 ───
    raw = input(f"\n  Customer / advertiser name [{DEFAULT_CLIENT_NAME}]: ").strip()
    client = raw or DEFAULT_CLIENT_NAME

    customer_id = DEFAULT_CUSTOMER_ID
    billing_type = 'agency'
    separation = (15, 0, 0)

    cust = _lookup_customer(client)
    if cust:
        customer_id = int(cust.customer_id) if str(cust.customer_id).isdigit() else DEFAULT_CUSTOMER_ID
        billing_type = cust.billing_type or 'agency'
        s0 = 25 if cust.separation_customer == 30 else cust.separation_customer
        separation = (s0, cust.separation_event, cust.separation_order)
        print(f"[CUSTOMER] ✓ '{client}' in DB → ID {customer_id}, billing={billing_type}, sep {separation}")
    else:
        print(f"[CUSTOMER] '{client}' not in customers.db — defaulting to ANAGRAF ID {DEFAULT_CUSTOMER_ID}.")
        raw_id = input(f"  Customer (ANAGRAF) ID [{DEFAULT_CUSTOMER_ID}]: ").strip()
        customer_id = int(raw_id) if raw_id.isdigit() else DEFAULT_CUSTOMER_ID
        save = input(f"  Save '{client}' (ID {customer_id}, {billing_type}) to DB? (y/n): ").strip().lower()
        if save in ('y', 'yes'):
            _upsert_customer(str(customer_id), client, separation, billing_type)

    # ── Per-flight contract code + description ────────────────────────────
    orders_meta: list[dict] = []
    for o in doc.orders:
        # Code: "3Fold LRCC <yymm>" (flight start). Description:
        # "Los Rios Community College District <yymm>" — or "<yymm>-<yymm>"
        # when the flight spans more than one calendar month.
        start_ym = o.flight_start.strftime('%y%m')   # %y/%m are cross-platform (lesson #13)
        end_ym   = o.flight_end.strftime('%y%m')
        ym_span  = start_ym if start_ym == end_ym else f"{start_ym}-{end_ym}"
        default_code = f"3Fold LRCC {start_ym}"
        default_desc = f"Los Rios Community College District {ym_span}"
        print(f"\n  --- {o.season} ---")
        raw = input(f"  Contract code [{default_code}]: ").strip()
        code = raw or default_code
        raw = input(f"  Description [{default_desc}]: ").strip()
        desc = raw or default_desc
        orders_meta.append({'season': o.season, 'contract_code': code, 'description': desc})

    return {
        'customer_id': customer_id,
        'client': client,
        'billing_type': billing_type,
        'separation': separation,
        'orders': orders_meta,
        'start_date_override': start_override.strftime('%m/%d/%Y'),
    }


# ─── Direct DB Entry ────────────────────────────────────────────────────────

def _create_contract(order: LRCCDOrder, meta: dict, inputs: dict,
                     original_earliest: Optional[date], override_start: Optional[date]) -> bool:
    from browser_automation.etere_direct_client import (
        AGENCY_IDS,
        EtereDirectClient,
        connect,
    )

    customer_id = inputs.get('customer_id') or DEFAULT_CUSTOMER_ID
    separation = inputs.get('separation', (15, 0, 0))
    billing_type = inputs.get('billing_type', 'agency')
    contract_code = meta['contract_code']
    description = meta['description']

    flight_start = order.flight_start
    flight_end = order.flight_end
    if not flight_start or not flight_end:
        print(f"[LRCCD] ✗ {order.season}: no flight range")
        return False

    # Apply the lesson #15 override to the header start when this flight is the
    # one that begins on the document's earliest (overridden) date.
    header_start = flight_start
    if (original_earliest and override_start
            and override_start != original_earliest
            and flight_start == original_earliest):
        header_start = override_start

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=contract_code,
            description=description,
            customer_id=int(customer_id),
            # ANAGRAF links LRCCD (218) → 3Fold (203); the linked agency wins.
            # agency_id here is only a fallback for clients with no linked agency.
            agency_id=AGENCY_IDS["3FOLD"],
            lookup_customer_defaults=True,
            contract_date=header_start,
            contract_end_date=flight_end,
            contract_type=1,
            billing_type=billing_type,
            allow_rename=True,
        )
        print(f"[LRCCD] ✓ {order.season} header: ID={contract_id} code='{contract_code}'")

        line_count = 0
        for ln in order.lines:
            if ln.total_spots == 0:
                continue
            booking_code = 10 if ln.is_bonus else 2

            days = ln.days
            time_raw = ln.time
            days, _ = EtereClient.check_sunday_6_7a_rule(days, time_raw)
            time_from, time_to = EtereClient.parse_time_range(time_raw)
            time_range = f"{time_from}-{time_to}"

            date_from = ln.start
            if (original_earliest and override_start
                    and override_start != original_earliest
                    and date_from == original_earliest):
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
                rate=ln.rate,
                total_spots=ln.total_spots,
                spots_per_week=0,          # flight-total → Rotation
                date_from=date_from,
                date_to=date_to,
                duration=str(ln.duration),
                is_bonus=ln.is_bonus,
                booking_code=booking_code,
                separation_intervals=separation,
            )

        conn.commit()
        conn.close()
        print(f"[LRCCD] ✓ {order.season}: {line_count} lines committed.")
        return True

    except Exception as exc:
        print(f"[LRCCD] ✗ {order.season}: {exc}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return False


def run_lrccd_order(doc: LRCCDDocument, inputs: dict) -> list[tuple[str, bool]]:
    """
    Process an LRCCD order — one Etere contract per flight (FALL, SPRING).

    Returns a list of (contract_code, success) tuples, one per flight.
    """
    orders_meta = inputs.get('orders') or []
    meta_by_season = {m['season']: m for m in orders_meta}

    original_earliest = _parse_user_date(doc.flight_start) if doc.flight_start else None
    ov = inputs.get('start_date_override')
    override_start = _parse_user_date(ov) if ov else original_earliest

    results: list[tuple[str, bool]] = []
    for order in doc.orders:
        meta = meta_by_season.get(order.season) or {
            'contract_code': f"LRCCD {order.season}",
            'description': f"LRCCD {order.season}",
        }
        ok = _create_contract(order, meta, inputs, original_earliest, override_start)
        results.append((meta['contract_code'], ok))
    return results
