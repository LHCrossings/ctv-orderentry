"""
Emerald Queen Casino (EQC) / TH Media direct-DB automation.

EQC buys non-consecutive weeks (typically every other week), so each week-column
becomes its OWN contract line — weeks are never consolidated. Quarters are
entered as SEPARATE contracts (one contract per quarter):

    code        : "TH EQC <yymm>"            e.g. "TH EQC 2607"
    description : "Emerald Queen Casino <yy>Q<n>"  e.g. "Emerald Queen Casino 26Q3"

EQC is the advertiser (ANAGRAF customer 20); TH Media is the agency (id 19).
Rates are GROSS — no gross-up. Bonus = the $0 language rows (booking code 10,
Rotation) entered against their own day/time windows.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
from browser_automation.etere_client import EtereClient
from browser_automation.parsers.eqc_parser import EQCLine, EQCOrder, parse_eqc_xlsx

DEFAULT_CUSTOMER_ID = 20            # Emerald Queen Casino (ANAGRAF)
DEFAULT_CLIENT_NAME = "Emerald Queen Casino"


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _fmt_mmddyyyy(d: date) -> str:
    return d.strftime('%m/%d/%Y')


def _parse_date(s) -> date:
    if isinstance(s, date):
        return s
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


def _quarter_of(d: date) -> int:
    return (d.month - 1) // 3 + 1


def _quarter_groups(week_dates: list[date]) -> list[dict]:
    """
    Group week-column indices by (year, quarter), preserving column order.

    Returns a list of dicts (one per quarter present), each with:
        cols  : list[int]  column indices belonging to this quarter
        start : date       earliest Monday in the quarter
        end   : date       latest Sunday in the quarter
        code  : str        default contract code  ("TH EQC 2607")
        desc  : str        default description     ("Emerald Queen Casino 26Q3")
    """
    grouped: dict[tuple[int, int], list[int]] = {}
    for i, d in enumerate(week_dates):
        grouped.setdefault((d.year, _quarter_of(d)), []).append(i)

    out: list[dict] = []
    for (year, q), cols in grouped.items():
        starts = [week_dates[i] for i in cols]
        start = min(starts)
        end = max(starts) + timedelta(days=6)
        yy = year % 100
        out.append({
            'cols':  cols,
            'start': start,
            'end':   end,
            'code':  f"TH EQC {yy:02d}{start.month:02d}",
            'desc':  f"{DEFAULT_CLIENT_NAME} {yy:02d}Q{q}",
        })
    return out


def _lookup_customer(client_name: str):
    try:
        import os

        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.enums import OrderType
        if not os.path.exists(CUSTOMER_DB_PATH):
            return None
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        return (
            repo.find_by_name(client_name, OrderType.EQC)
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
            order_type=OrderType.EQC,
            billing_type=billing_type,
            separation_customer=separation[0],
            separation_event=separation[1],
            separation_order=separation[2],
        ))
        print(f"[CUSTOMER DB] ✓ Saved: {client_name} → ID {customer_id}")
    except Exception as exc:
        print(f"[CUSTOMER DB] Warning: could not save: {exc}")


def _confirm_start_date(order: EQCOrder) -> Optional[date]:
    """Lesson #15: confirm the start date if the order starts tomorrow or earlier."""
    if not order.flight_start:
        return None
    earliest = _parse_date(order.flight_start)
    if earliest > date.today() + timedelta(days=1):
        return earliest

    def _f(d: date) -> str:
        return f"{d.month}/{d.day}/{d.strftime('%y')}"

    print(f"\n  ⚠ This order starts {_f(earliest)} (today is {_f(date.today())}).")
    raw = input(f"  Confirm start date [{_f(earliest)}]: ").strip()
    if raw and raw.lower() not in ('y', 'yes'):
        try:
            return _parse_date(raw)
        except ValueError:
            print(f"  ✗ Could not parse '{raw}' — keeping {_f(earliest)}")
    return earliest


# ─── Line builder (no DB — used by both the entry path and verification) ─────

def build_quarter_lines(order: EQCOrder, quarter: dict, spot_duration: int = 30) -> list[dict]:
    """
    Build one line-spec per (program × week-column) for a single quarter.

    Each spec is ready to pass straight to add_contract_line(**spec) minus the
    contract/market context. Weeks are NOT consolidated.
    """
    specs: list[dict] = []
    for line in order.lines:
        days = line.days
        time_raw = line.time_raw
        days, _ = EtereClient.check_sunday_6_7a_rule(days, time_raw)
        time_from, time_to = EtereClient.parse_time_range(time_raw.replace('&', ';'))
        time_range = f"{time_from}-{time_to}"

        for col in quarter['cols']:
            spots = line.week_spots[col] if col < len(line.week_spots) else 0
            if spots <= 0:
                continue
            wk_start = order.week_dates[col]
            wk_end = wk_start + timedelta(days=6)   # Mon → Sun
            specs.append({
                'days':        days,
                'time_range':  time_range,
                'description': line.description,
                'rate':        line.rate,
                'total_spots': spots,
                'spots_per_week': spots,
                'date_from':   wk_start,
                'date_to':     wk_end,
                'duration':    str(spot_duration),
                'is_bonus':    line.is_bonus,
                'booking_code': 10 if line.is_bonus else 2,
            })
    return specs


# ─── Input gather ─────────────────────────────────────────────────────────────

def gather_eqc_inputs(xlsx_path: str) -> Optional[dict]:
    """
    Gather user inputs for an EQC order. One contract per quarter present in the
    workbook. Returns the inputs dict, or None to abort.
    """
    from browser_automation.etere_direct_client import AGENCY_IDS

    order = parse_eqc_xlsx(xlsx_path)
    quarters = _quarter_groups(order.week_dates)

    print(f"\n{'='*64}")
    print(f"Agency:   {order.agency}  (fixed — Etere agency ID {AGENCY_IDS['THMEDIA']})")
    print(f"Customer: {order.client}")
    print(f"Market:   {order.market_code}")
    print(f"Weeks:    {', '.join(d.strftime('%m/%d') for d in order.week_dates)}")
    print(f"Quarters: {', '.join(q['desc'].split()[-1] for q in quarters)}")
    for ln in order.lines:
        tag = "BNS" if ln.is_bonus else "   "
        rate = f"${ln.rate:.0f}" if ln.rate else "    "
        print(f"  {tag} {ln.program.strip():28} {ln.days:6} {ln.time_raw:16} {rate}  {ln.week_spots}")

    # Start-date sanity check
    start_override = _confirm_start_date(order)
    if start_override is None:
        print("  ✗ No flight start date found — aborting")
        return None

    # Customer (advertiser). EQC is a fixed single client; default to ID 20.
    cust = _lookup_customer(order.client)
    separation = (15, 0, 0)
    billing_type = 'agency'
    if cust:
        customer_id = int(cust.customer_id)
        billing_type = cust.billing_type or 'agency'
        separation = (cust.separation_customer, cust.separation_event, cust.separation_order)
        print(f"\n[CUSTOMER] ✓ '{order.client}' in DB → ID {customer_id}, billing={billing_type}, sep {separation}")
    else:
        raw_id = input(f"\n  Customer ID [{DEFAULT_CUSTOMER_ID}]: ").strip()
        customer_id = int(raw_id) if raw_id.isdigit() else DEFAULT_CUSTOMER_ID
        save = input(f"  Save '{order.client}' (ID {customer_id}, {billing_type}) to DB? (y/n): ").strip().lower()
        if save in ('y', 'yes'):
            _upsert_customer(str(customer_id), order.client, separation, billing_type)

    # Spot duration
    raw = input("\n  Spot duration in seconds [30]: ").strip()
    spot_duration = int(raw) if raw.isdigit() else 30

    # Per-quarter contract code + description (one contract each)
    print()
    quarter_inputs: list[dict] = []
    for q in quarters:
        qlabel = q['desc'].split()[-1]
        print(f"  ── {qlabel} ({_fmt_mmddyyyy(q['start'])} – {_fmt_mmddyyyy(q['end'])}) ──")
        raw = input(f"    Contract code [{q['code']}]: ").strip()
        code = raw or q['code']
        raw = input(f"    Description [{q['desc']}]: ").strip()
        desc = raw or q['desc']
        quarter_inputs.append({
            'cols':  q['cols'],
            'start': _fmt_mmddyyyy(q['start']),
            'end':   _fmt_mmddyyyy(q['end']),
            'code':  code,
            'description': desc,
        })

    return {
        'customer_id':   customer_id,
        'client':        order.client,
        'billing_type':  billing_type,
        'separation':    separation,
        'spot_duration': spot_duration,
        'quarters':      quarter_inputs,
    }


# ─── Direct DB entry ───────────────────────────────────────────────────────────

def _create_quarter_contract(order: EQCOrder, inputs: dict, quarter: dict) -> Optional[str]:
    """Enter one quarter as a single Etere contract. Returns the code or None."""
    from browser_automation.etere_direct_client import (
        AGENCY_IDS,
        EtereDirectClient,
        connect,
    )

    customer_id = inputs.get('customer_id')
    if customer_id is None:
        print("[EQC] ✗ No customer_id")
        return None

    separation   = inputs.get('separation', (15, 0, 0))
    billing_type = inputs.get('billing_type', 'agency')
    spot_duration = inputs.get('spot_duration', 30)
    code = quarter['code']
    description = quarter['description']
    start_d = _parse_date(quarter['start'])
    end_d   = _parse_date(quarter['end'])

    # Resolve the column subset for this quarter back into an order-like view
    quarter_view = {'cols': quarter['cols']}
    specs = build_quarter_lines(order, quarter_view, spot_duration)
    if not specs:
        print(f"[EQC] ✗ No lines for {code}")
        return None

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=code,
            description=description,
            customer_id=int(customer_id),
            agency_id=AGENCY_IDS["THMEDIA"],   # fallback; ANAGRAF link wins
            lookup_customer_defaults=True,
            contract_date=start_d,
            contract_end_date=end_d,
            contract_type=1,
            billing_type=billing_type,
            allow_rename=True,
        )
        print(f"[EQC] ✓ Contract header: ID={contract_id}  code='{code}'")

        for n, spec in enumerate(specs, 1):
            print(
                f"  [LINE {n}] {order.market_code} {spec['description']}: "
                f"{_fmt_mmddyyyy(spec['date_from'])}–{_fmt_mmddyyyy(spec['date_to'])} "
                f"({spec['spots_per_week']}/wk) bc={spec['booking_code']}"
            )
            client.add_contract_line(
                market=order.market_code,
                separation_intervals=separation,
                **spec,
            )

        conn.commit()
        conn.close()
        print(f"[EQC] ✓ {len(specs)} lines committed for {code}.")
        return code

    except Exception as exc:
        print(f"[EQC] ✗ {exc}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


def run_eqc_order(order: EQCOrder, inputs: dict) -> list[tuple[str, bool]]:
    """
    Process an EQC order: one contract per quarter.

    Returns a list of (contract_code, success) tuples (one per quarter).
    """
    results: list[tuple[str, bool]] = []
    for quarter in inputs.get('quarters', []):
        code = _create_quarter_contract(order, inputs, quarter)
        results.append((quarter.get('code', 'TH EQC'), code is not None))
    return results
