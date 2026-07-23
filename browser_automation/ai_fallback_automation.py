"""
AI fallback direct DB automation.

Enters an order that was extracted by the AI parser (browser_automation/parsers/
ai_parser.py) when no deterministic parser matched. Reuses the same downstream
domain logic the hand-written parsers use:
  * ROS bonus lines resolve to the language's standard window via ROS_SCHEDULES
  * weekly-column grids split into contiguous runs via EtereClient.consolidate_weeks
  * EtereDirectClient.add_contract_line applies time normalization, auto-Rotation
    for flight-total lines, separation, etc.

The model supplies the advertiser/agency NAMES; the Etere customer ID is resolved
in the gather step (customers.db → ANAGRAF), and ANAGRAF's client→agency link +
commission are applied via lookup_customer_defaults=True. One contract is created
covering all extracted lines.

Nothing here runs unless the operator has reviewed the AI extraction in the
preview and explicitly triggered processing.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
from browser_automation.etere_client import EtereClient
from browser_automation.parsers.ai_parser import AIOrder, parse_ai_order

# Web/banner and production/service lines are NOT entered into Etere yet — there's
# no defined accounting rule for them. They're still extracted (tagged by `kind`)
# and reconciled, just filtered out at entry. Flip this to True once the rule is
# defined and add the per-kind entry logic.
_ENTER_WEB_PRODUCTION = False
_AIRTIME = "airtime"

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _fmt(d: date) -> str:
    return f"{d.month}/{d.day}/{d.strftime('%y')}"


def _parse_date(s) -> Optional[date]:
    if isinstance(s, date):
        return s
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d', '%m/%d'):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


class _Wk:
    """Shim so AI week dates feed consolidate_weeks (expects .start_date MM/DD/YYYY)."""
    def __init__(self, d: str):
        self.start_date = d


def _lookup_customer(client_name: str):
    try:
        import os

        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.enums import OrderType
        if not client_name or not os.path.exists(CUSTOMER_DB_PATH):
            return None
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        return (
            repo.find_by_name(client_name, OrderType.AI_FALLBACK)
            or repo.find_by_name_any_type(client_name)
        )
    except Exception as exc:
        print(f"[CUSTOMER] Warning: lookup failed: {exc}")
        return None


def _upsert_customer(customer_id: str, client_name: str, separation: tuple, billing_type: str) -> None:
    try:
        import os

        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        from src.domain.enums import OrderType
        if not os.path.exists(CUSTOMER_DB_PATH):
            return
        CustomerRepository(CUSTOMER_DB_PATH).save(Customer(
            customer_id=customer_id,
            customer_name=client_name,
            order_type=OrderType.AI_FALLBACK,
            billing_type=billing_type,
            separation_customer=separation[0],
            separation_event=separation[1],
            separation_order=separation[2],
        ))
        print(f"[CUSTOMER DB] ✓ Saved: {client_name} → ID {customer_id}")
    except Exception as exc:
        print(f"[CUSTOMER DB] Warning: could not save: {exc}")


# Language names the model may emit → ROS_SCHEDULES keys (which use CTV block names).
_LANG_ALIASES = {"mandarin": "Chinese", "cantonese": "Cantonese", "filipino": "Filipino",
                 "tagalog": "Filipino", "hindi": "Hindi", "south asian": "South Asian"}


def _resolve_days_time(line) -> tuple[str, str]:
    """Resolve (days, raw_time_window) for an AI line, applying ROS schedules."""
    tr = (line.time_range or "").strip()
    if not tr or tr.upper() == "ROS":
        try:
            from browser_automation.ros_definitions import ROS_SCHEDULES
            lang = (line.language or "").strip()
            sched = ROS_SCHEDULES.get(lang) or ROS_SCHEDULES.get(_LANG_ALIASES.get(lang.lower(), ""))
            if sched:
                return sched["days"], sched["time"]
        except Exception:
            pass
        return (line.days or "M-Su"), "6a-11:59p"
    return (line.days or "M-Su"), tr


# ─── Input gather ────────────────────────────────────────────────────────────

def gather_ai_fallback_inputs(pdf_path: str) -> Optional[dict]:
    """Gather inputs for an AI-extracted order. Returns dict or None to abort."""
    order = parse_ai_order(pdf_path)

    print(f"\n{'='*64}")
    print("AI-EXTRACTED ORDER  (review carefully — extracted by Claude, not a tested parser)")
    print(f"{'='*64}")
    print(f"Advertiser: {order.client or '(unknown)'}")
    print(f"Agency:     {order.agency or '(none / direct)'}")
    print(f"Markets:    {', '.join(order.markets)}")
    print(f"Flight:     {order.flight_start} -> {order.flight_end}   rates_are_net={order.rates_are_net}")
    print(f"Lines:      {len(order.lines)}")
    for ln in order.lines:
        d, t = _resolve_days_time(ln)
        tag = "BNS" if ln.is_bonus else "   "
        grid = f"  weeks={list(ln.week_spots)}" if ln.week_spots else ""
        print(f"  {tag} :{ln.duration:<2} {ln.market:<4} {(ln.language or ''):<10} {d:<6} {t:<11} sp={ln.total_spots:<4} ${ln.rate}{grid}")
    if order.warnings:
        print("\n⚠ MODEL WARNINGS — verify these before entering:")
        for w in order.warnings:
            print(f"   - {w}")

    proceed = input("\n  Proceed to enter this AI-extracted order? (y/n): ").strip().lower()
    if proceed not in ('y', 'yes'):
        print("  Aborted.")
        return None

    # ── Start-date sanity check (lesson #15) ──────────────────────────────
    earliest = _parse_date(order.flight_start)
    start_override = None
    if earliest and earliest <= date.today() + timedelta(days=1):
        print(f"\n  ⚠ This order starts {_fmt(earliest)} (today is {_fmt(date.today())}).")
        raw = input(f"  Confirm start date [{_fmt(earliest)}]: ").strip()
        if raw and raw.lower() not in ('y', 'yes'):
            start_override = _parse_date(raw)

    # ── Customer (advertiser) resolution ──────────────────────────────────
    raw = input(f"\n  Advertiser / customer name [{order.client}]: ").strip()
    client = raw or order.client
    customer_id: Optional[int] = None
    billing_type = 'agency' if order.agency else 'direct'
    separation = (15, 0, 0)

    cust = _lookup_customer(client)
    if cust:
        customer_id = int(cust.customer_id) if str(cust.customer_id).isdigit() else None
        billing_type = cust.billing_type or billing_type
        s0 = 25 if cust.separation_customer == 30 else cust.separation_customer
        separation = (s0, cust.separation_event, cust.separation_order)
        print(f"[CUSTOMER] ✓ '{client}' in DB → ID {customer_id}, billing={billing_type}, sep {separation}")
    else:
        print(f"[CUSTOMER] '{client}' not in customers.db — enter its Etere (ANAGRAF) customer ID.")
        raw_id = input("  Customer ID (NOT the agency ID): ").strip()
        if not raw_id.isdigit():
            print("  ✗ Invalid customer ID — aborting")
            return None
        customer_id = int(raw_id)
        raw_bt = input(f"  Billing type [agency/direct] [{billing_type}]: ").strip().lower()
        if raw_bt:
            billing_type = 'agency' if raw_bt.startswith('a') else 'direct'
        if input(f"  Save '{client}' (ID {customer_id}, {billing_type}) to DB? (y/n): ").strip().lower() in ('y', 'yes'):
            _upsert_customer(str(customer_id), client, separation, billing_type)

    # ── Contract code + description ───────────────────────────────────────
    s = start_override or earliest
    e = _parse_date(order.flight_end)
    flight = f"{s.month}/{s.day}-{e.month}/{e.day}/{e.strftime('%y')}" if (s and e) else ""
    default_code = f"{client} {flight}".strip()
    default_desc = default_code
    print()
    raw = input(f"  Contract code [{default_code}]: ").strip()
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
        'start_date_override': start_override.strftime('%m/%d/%Y') if start_override else None,
    }


# ─── Direct DB entry ─────────────────────────────────────────────────────────

def run_ai_order(order: AIOrder, inputs: dict) -> list[tuple[str, bool]]:
    """Enter the AI-extracted order as one contract. Returns [(contract_code, success)]."""
    from browser_automation.etere_direct_client import (
        EtereDirectClient,
        connect,
    )

    customer_id = inputs.get('customer_id')
    if not customer_id:
        print("[AI] ✗ No customer_id — cannot enter")
        return [(inputs.get('contract_code') or 'AI', False)]

    separation = inputs.get('separation', (15, 0, 0))
    billing_type = inputs.get('billing_type', 'agency')
    contract_code = inputs.get('contract_code', 'AI')
    description = inputs.get('description', contract_code)

    original_earliest = _parse_date(order.flight_start)
    ov = inputs.get('start_date_override')
    override_start = _parse_date(ov) if ov else original_earliest
    flight_start = override_start or original_earliest
    flight_end = _parse_date(order.flight_end)
    if not flight_start or not flight_end:
        print("[AI] ✗ Could not determine flight range")
        return [(contract_code, False)]

    # Filter out web/production lines (not entered yet) and reconcile BEFORE any
    # DB write. Enterable = a real airtime line = kind 'airtime' AND a positive
    # spot count. This deterministically drops web (n/a → 0 spots), production, and
    # any empty row regardless of the model's `kind` guess — the vision model
    # sometimes mislabels the web banner as 'airtime', but it always has 0 spots.
    def _is_airtime(ln) -> bool:
        return (ln.kind or _AIRTIME) == _AIRTIME and (ln.total_spots or 0) > 0

    enterable = [ln for ln in order.lines if _ENTER_WEB_PRODUCTION or _is_airtime(ln)]
    excluded = [ln for ln in order.lines if not (_ENTER_WEB_PRODUCTION or _is_airtime(ln))]
    for ex in excluded:
        print(f"[AI] ⓘ Excluding non-airtime line (kind={ex.kind}, spots={ex.total_spots}, "
              f"${ex.rate}): {ex.description or ex.language or '?'} — no Etere rule yet.")

    # Spot guard: the airtime spots we're about to enter must equal the order's OWN
    # stated grand total. Excluding web/production can't skew it (they carry 0
    # spots). A misread spot count must refuse to enter, never enter silently
    # (same totals-guard the deterministic parsers use).
    stated = getattr(order, "stated_total_spots", 0) or 0
    entered_spots = sum((ln.total_spots or 0) for ln in enterable)
    if stated and entered_spots != stated:
        print(f"[AI] ✗ Spot reconciliation failed: about to enter {entered_spots} airtime spot(s) "
              f"but the order states {stated}. Refusing to enter — re-check the extraction.")
        return [(contract_code, False)]

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=contract_code, description=description, customer_id=int(customer_id),
            # ANAGRAF's client→agency link (and its commission) win; no hardcoded agency.
            lookup_customer_defaults=True,
            contract_date=flight_start, contract_end_date=flight_end,
            contract_type=1, billing_type=billing_type, allow_rename=True,
        )
        print(f"[AI] ✓ Contract header: ID={contract_id} code='{contract_code}'")

        n = 0
        for ln in enterable:
            if ln.total_spots == 0:
                continue
            booking_code = 10 if ln.is_bonus else 2
            days, time_raw = _resolve_days_time(ln)
            days, _ = EtereClient.check_sunday_6_7a_rule(days, time_raw)
            tf, tt = EtereClient.parse_time_range(time_raw)
            time_range = f"{tf}-{tt}"
            desc = (ln.description or f"{ln.language} {days}").strip()[:60]
            dur = str(ln.duration)

            # Build (date_from, date_to, total, per_week) runs:
            runs: list[tuple[date, date, int, int]] = []
            if ln.week_dates and ln.week_spots:
                consolidated = EtereClient.consolidate_weeks(
                    list(ln.week_spots), [_Wk(d) for d in ln.week_dates], flight_end=order.flight_end,
                )
                for r in consolidated:
                    df = _parse_date(r['start_date'])
                    if original_earliest and override_start and override_start != original_earliest and df == original_earliest:
                        df = override_start
                    runs.append((df, _parse_date(r['end_date']), r['spots_per_week'] * r['weeks'], r['spots_per_week']))
            else:
                df = _parse_date(ln.start_date) or flight_start
                if original_earliest and override_start and override_start != original_earliest and df == original_earliest:
                    df = override_start
                runs.append((df, _parse_date(ln.end_date) or flight_end, ln.total_spots, ln.spots_per_week))

            for df, dt, total, per_week in runs:
                if not total:
                    continue
                n += 1
                print(f"  [LINE {n}] {ln.market} {desc} :{dur} {days} {time_range} "
                      f"{_fmt(df)}–{_fmt(dt)} sp={total}/wk{per_week} ${ln.rate} {'BNS' if ln.is_bonus else ''}")
                client.add_contract_line(
                    market=ln.market, days=days, time_range=time_range, description=desc,
                    rate=ln.rate, total_spots=total, spots_per_week=per_week,
                    date_from=df, date_to=dt, duration=dur,
                    is_bonus=ln.is_bonus, is_billboard=getattr(ln, "is_billboard", False),
                    booking_code=booking_code, separation_intervals=separation,
                )

        conn.commit()
        conn.close()
        print(f"[AI] ✓ {n} lines committed under '{contract_code}'.")
        return [(contract_code, True)]

    except Exception as exc:
        print(f"[AI] ✗ {exc}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return [(contract_code, False)]
