"""
ACM (American Community Media) direct DB automation.

Creates one Etere contract per market section. Each section has paid lines
(specific daypart) and bonus ROS lines per language block.
"""

from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Optional

from browser_automation.etere_client import EtereClient
from browser_automation.parsers.acm_parser import AcmOrder, AcmMarketSection, parse_acm_xlsx


# ─── Month abbreviations (for consolidate_weeks date format) ─────────────────
_MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

# ─── Market short codes for default contract codes ───────────────────────────
_MARKET_SHORT = {
    'CVC': 'CV', 'SFO': 'SF', 'SEA': 'SEA',
    'LAX': 'LA', 'HOU': 'HOU', 'CMP': 'CMP',
    'WDC': 'WDC', 'NYC': 'NYC', 'MMT': 'MMT',
}


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


def _lookup_customer(client_name: str) -> Optional[dict]:
    try:
        from data_access.customer_repository import CustomerRepository
        repo = CustomerRepository()
        return (
            repo.find_customer(client_name, order_type='acm')
            or repo.find_customer(client_name)
        )
    except Exception:
        return None


def _upsert_customer(customer_id: str, client_name: str, separation: tuple) -> None:
    try:
        from data_access.customer_repository import CustomerRepository
        repo = CustomerRepository()
        repo.upsert(
            customer_id=customer_id,
            customer_name=client_name,
            order_type='acm',
            billing_type='agency',
            separation_customer=separation[0],
            separation_event=separation[1],
            separation_order=separation[2],
        )
        print(f"[CUSTOMER DB] ✓ Saved: {client_name} → ID {customer_id}")
    except Exception as exc:
        print(f"[CUSTOMER DB] Warning: could not save: {exc}")


# ─── Input Gather ─────────────────────────────────────────────────────────────

def gather_acm_inputs(xlsx_path: str) -> Optional[dict]:
    """
    Gather user inputs for an ACM order before processing.

    Returns dict with: customer_id, separation, spot_duration, market_inputs.
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
    cust: Optional[dict] = None

    cust = _lookup_customer(order.agency)
    if cust:
        customer_id = cust.get('customer_id')
        raw_sep = cust.get('separation')
        if raw_sep:
            s0 = 25 if raw_sep[0] == 30 else raw_sep[0]
            separation = (s0, raw_sep[1], raw_sep[2])
        print(f"\n[CUSTOMER] ✓ Found in DB → ID {customer_id}, sep {separation}")
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

    # ── Per-market contract code + description ────────────────────────────
    market_inputs: dict[str, dict] = {}

    code_name  = cust.get('code_name',        'ACM') if cust else 'ACM'
    desc_name  = cust.get('description_name', 'American Community Media') if cust else 'American Community Media'
    inc_market = bool(cust.get('include_market_in_code', 1)) if cust else True

    for mkt in order.market_sections:
        print(f"\n  ── {mkt.market_code} contract ──")

        short_mkt = _MARKET_SHORT.get(mkt.market_code, mkt.market_code)
        start_d   = mkt.flight_start
        end_d     = mkt.flight_end

        if inc_market:
            default_code = f"{code_name} {short_mkt}"
        else:
            default_code = code_name

        flight_str = ""
        if start_d and end_d:
            flight_str = (
                f"{start_d.strftime('%-m/%-d')}"
                f"-{end_d.strftime('%-m/%-d/%y')}"
            )
        default_desc = f"{desc_name} {flight_str}".strip()

        raw = input(f"  Contract code [{default_code}]: ").strip()
        contract_code = raw or default_code

        raw = input(f"  Description [{default_desc}]: ").strip()
        description = raw or default_desc

        market_inputs[mkt.market_code] = {
            'contract_code': contract_code,
            'description':   description,
        }

    return {
        'customer_id':   customer_id,
        'separation':    separation,
        'spot_duration': spot_duration,
        'market_inputs': market_inputs,
    }


# ─── Direct DB Entry ──────────────────────────────────────────────────────────

def _create_acm_market_contract(
    mkt: AcmMarketSection,
    inputs: dict,
    mkt_inp: dict,
    spot_duration: int,
) -> Optional[str]:
    """
    Enter one ACM market section into Etere via direct DB.
    Returns the contract code on success, None on failure (rolls back).
    """
    from browser_automation.etere_direct_client import EtereDirectClient, connect
    from browser_automation.ros_definitions import ROS_SCHEDULES

    customer_id = inputs.get('customer_id')
    if customer_id is None:
        print(f"[ACM] ✗ No customer_id")
        return None

    separation    = inputs.get('separation', (15, 0, 0))
    contract_code = mkt_inp.get('contract_code', 'ACM')
    description   = mkt_inp.get('description', '')

    flight_start_d = mkt.flight_start
    flight_end_d   = mkt.flight_end
    if not flight_start_d or not flight_end_d:
        print(f"[ACM] ✗ {mkt.market_code}: no week dates — cannot determine flight range")
        return None

    flight_end_str = _fmt_mmddyyyy(flight_end_d)

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
            billing_type="agency",
            allow_rename=True,
        )
        print(f"[ACM] ✓ Contract header {mkt.market_code}: ID={contract_id}  code='{contract_code}'")

        duration_str = str(spot_duration)
        line_count   = 0

        for line in mkt.lines:
            if line.total_spots == 0:
                continue

            is_bonus     = line.is_bonus
            booking_code = 10 if is_bonus else 2

            if is_bonus:
                # Look up the standard ROS window for this language
                lang = line.language_block.strip()
                ros  = ROS_SCHEDULES.get(lang)
                if ros:
                    days      = ros['days']
                    time_raw  = ros['time']
                else:
                    # Fallback: full-day ROS
                    days, time_raw = 'M-Su', '6a-11:59p'
                    print(f"  [WARN] No ROS schedule for '{lang}' — using {days} {time_raw}")
                desc = f"BNS {lang} ROS"
            else:
                days     = line.days   # normalized in parser (Sun→Su etc.)
                time_raw = line.time
                days, _  = EtereClient.check_sunday_6_7a_rule(days, time_raw)
                label    = line.language_block.strip()
                desc     = f"{label} {line.daypart}"
                if len(desc) > 60:
                    desc = desc[:60]

            time_from, time_to = EtereClient.parse_time_range(time_raw)
            time_range = f"{time_from}-{time_to}"

            # Convert week dates → "Jun 15" strings for consolidate_weeks
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
                    f"  [LINE {line_count}] {desc}: "
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
        print(f"[ACM] ✓ {mkt.market_code}: {line_count} lines committed.")
        return contract_code

    except Exception as exc:
        print(f"[ACM] ✗ {mkt.market_code}: {exc}")
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
    Process all market sections in an ACM order.

    Returns list of (contract_code, success) tuples — one per market.
    """
    spot_duration = inputs.get('spot_duration', 30)
    market_inputs = inputs.get('market_inputs', {})
    results: list[tuple[str, bool]] = []

    for mkt in order.market_sections:
        mkt_inp = market_inputs.get(mkt.market_code, {})
        code    = _create_acm_market_contract(mkt, inputs, mkt_inp, spot_duration)
        label   = mkt_inp.get('contract_code') or 'ACM'
        results.append((label, code is not None))

    return results
