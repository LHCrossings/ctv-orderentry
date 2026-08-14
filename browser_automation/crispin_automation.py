"""
Crispin LLC direct-DB automation (advertiser: Bay Area AQMD).

Crispin is an AGENCY parser. The agency is fixed (Crispin LLC → ANAGRAF agency
446). The advertiser is resolved in ANAGRAF, disambiguated by the agency link:
Bay Area AQMD exists twice —
    183  "Bay Area Air Quality Management District (AP)"      AGENZIA 187
    448  "Bay Area Air Quality Management District (Crispin)"  AGENZIA 446
so the record whose AGENZIA equals the order's agency (446) is the right one.

The agency commission is taken from the ANAGRAF link (Etere's client-select
behaviour) and **never overridden here** — whatever Commissione the ANAGRAF
client/agency carries is what the header gets. That single rule is what makes
the two source formats safe to mix, because they quote money differently:

  - the proposal workbook quotes **net** (the Discounted Rate we sold at);
  - the official Brand Time Schedule IO quotes **gross** (net ÷ 0.85).

Rates enter verbatim from whichever document we read, and the ANAGRAF commission
nets them back down. So if an IO's rates look grossed-up, the fix is the ANAGRAF
commission, never a multiplier in this file. (Lee set Crispin to 15% on
2026-08-10 for exactly this reason; it had been 0% while we only had the net-rate
proposal.)

One Etere contract for the whole order (single market, SFO). Paid :30s News
lines keep their explicit dayparts; :15s bonus lines are ROS (booking code 10)
scheduled via CRISPIN_ROS_WINDOWS.

**Production / translation money never becomes a contract line** (Lee
2026-08-10). It goes in the line form's **Production box** on the first paid
line, which Etere turns into a CONTRATTISPESE charge named 'Production' dated
that line's flight start. Backwrite has not been tuned to accept a production
line, and a zero-spot carrier line would read as airtime there. The carrier-line
pattern is reserved for orders that are production-ONLY.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
from browser_automation.etere_client import EtereClient

# The late-start planner is shared with other proposal-grid automations
# (ntooitive_automation) — the implementations live in line_planner. The
# underscore aliases keep this module's public-ish surface (tests import
# `_plan_ranges`/`_active_days`/`_line_plan` from here) unchanged.
from browser_automation.line_planner import (  # noqa: F401
    WeekCol as _WeekCol,
)
from browser_automation.line_planner import (  # noqa: F401
    active_days as _active_days,  # re-export: tests import it from here
)
from browser_automation.line_planner import (
    broadcast_yymm as _broadcast_yymm,
)
from browser_automation.line_planner import (
    confirm_start_date as _confirm_start_date_impl,
)
from browser_automation.line_planner import (
    fmt_mmddyyyy as _fmt_mmddyyyy,
)
from browser_automation.line_planner import (
    parse_date as _parse_date,
)
from browser_automation.line_planner import (
    plan_ranges as _plan_ranges,
)
from browser_automation.line_planner import (
    verify_production_charge as _verify_production_charge_impl,
)
from browser_automation.parsers.crispin_parser import (
    CrispinOrder,
    parse_crispin,
    split_daypart,
)

# Bonus :15s ROS windows (Lee-confirmed 2026-07-22). Matches the shared
# ROS_SCHEDULES for Cantonese/Filipino/Vietnamese; Mandarin is Crispin-specific
# (not defined in the shared table, which folds Mandarin into "Chinese").
CRISPIN_ROS_WINDOWS = {
    'Cantonese':  {'days': 'M-F',  'time': '7p-11:59p'},
    'Mandarin':   {'days': 'M-Su', 'time': '8p-11:59p'},
    'Filipino':   {'days': 'M-Su', 'time': '4p-7p'},
    'Vietnamese': {'days': 'M-Su', 'time': '10a-1p'},
}

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _resolve_customer(advertiser: str, agency_id: int) -> Optional[dict]:
    """Resolve the advertiser's ANAGRAF customer id, disambiguated by agency link.

    Returns {'id', 'name'} for the best match, or None. Prefers a customer whose
    AGENZIA == agency_id (Lee's rule: client + agency → the right client number);
    among those, the best name-token overlap with the parsed advertiser.
    """
    try:
        from browser_automation.etere_direct_client import connect
        conn = connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT ID_ANAGRAF, RAG_SOCIAL FROM ANAGRAF WHERE AGENZIA = %s",
            (int(agency_id),),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as exc:
        print(f"[CUSTOMER] ANAGRAF lookup failed: {exc}")
        return None
    if not rows:
        return None

    stop = {"the", "of", "and", "inc", "llc", "co", "district", "management"}
    want = {w for w in _tokens(advertiser) if w not in stop}

    best, best_score = None, -1
    for anid, name in rows:
        have = {w for w in _tokens(name) if w not in stop}
        score = len(want & have)
        if score > best_score:
            best, best_score = (anid, name), score
    if best is None:
        return None
    return {'id': int(best[0]), 'name': str(best[1])}


def _tokens(s: str) -> set[str]:
    import re
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t}


def _confirm_start_date(order: CrispinOrder) -> Optional[date]:
    """Ask what date the order should actually start when the IO is late."""
    return _confirm_start_date_impl(order.flight_start, order.flight_end)


def _line_plan(order: CrispinOrder, start_from: date,
               flight_end_str: str) -> list[tuple]:
    """(line, days, time_range, description, ranges, notes) for every airtime line.

    The single source of truth for what will be entered — the gather preview and
    `_create_crispin_contract` both walk this, so what Lee approves is exactly
    what gets written.
    """
    plan: list[tuple] = []
    for ln in order.lines:
        if ln.total_spots == 0:
            continue

        if ln.is_bonus:
            ros = CRISPIN_ROS_WINDOWS.get(ln.base_language)
            if ros:
                days, time_raw = ros['days'], ros['time']
            else:
                days, time_raw = 'M-Su', '6a-11:59p'
                print(f"  [WARN] No ROS window for '{ln.base_language}' — "
                      f"using {days} {time_raw}")
            desc = f"BNS {ln.base_language} ROS"
        else:
            days, time_raw = split_daypart(ln.daypart)
            days, _ = EtereClient.check_sunday_6_7a_rule(days, time_raw)
            desc = f"{ln.language_block.strip()} {ln.daypart.strip()}"[:60]

        time_from, time_to = EtereClient.parse_time_range(time_raw)
        line_end = ln.date_to or _parse_date(flight_end_str)
        consolidated = EtereClient.consolidate_weeks(
            ln.week_spots, [_WeekCol(d) for d in ln.week_dates],
            flight_end=_fmt_mmddyyyy(line_end),
        )
        ranges, notes = _plan_ranges(consolidated, days, start_from, line_end)
        plan.append((ln, days, f"{time_from}-{time_to}", desc, ranges, notes))
    return plan


# ─── Input Gather ─────────────────────────────────────────────────────────────

def gather_crispin_inputs(source_path: str) -> Optional[dict]:
    """Gather inputs for a Crispin order (IO PDF or proposal workbook).

    Returns dict or None to abort.
    """
    from browser_automation.etere_direct_client import AGENCY_IDS

    order = parse_crispin(source_path)
    agency_id = AGENCY_IDS["CRISPIN"]

    print(f"\n{'='*64}")
    src = "official IO (Brand Time Schedule)" if order.source_format == 'pdf' \
        else "proposal workbook"
    print(f"Source:     {src}")
    print(f"Agency:     {order.agency}  (fixed — Etere agency ID {agency_id})")
    print(f"Advertiser: {order.advertiser}")
    print(f"Market:     {order.market_code}   ({order.market_label})")
    if order.order_number:
        est = f", Est {order.estimate}" if order.estimate else ""
        rev = f", rev {order.revision}" if order.revision else ""
        print(f"Order:      #{order.order_number}{est}{rev}")
    if order.order_date:
        print(f"Revision:   {order.order_date:%B %d, %Y}")
    print(f"Flight:     {order.flight_start} → {order.flight_end}  ({len(order.week_dates)} weeks)")
    print("\n  Lines:")
    for ln in order.lines:
        tag = "BNS" if ln.is_bonus else "   "
        days, time = split_daypart(ln.daypart)
        if ln.is_bonus:
            ros = CRISPIN_ROS_WINDOWS.get(ln.base_language, {})
            days, time = ros.get('days', 'M-Su'), ros.get('time', 'ROS')
        rate = f"${ln.rate:.2f}" if ln.rate else "  bonus"
        flight = (f"  {_fmt_mmddyyyy(ln.date_from)}–{_fmt_mmddyyyy(ln.date_to)}"
                  if ln.date_from and ln.date_to else "")
        print(f"    {tag} :{ln.length_sec}s {ln.language_block:<26} {days} {time:<11} "
              f"{rate:>8}  {ln.total_spots} spots{flight}")

    paid_total = sum(ln.rate * ln.total_spots for ln in order.paid_lines)
    print(f"\n  Airtime: {sum(ln.total_spots for ln in order.paid_lines)} paid + "
          f"{sum(ln.total_spots for ln in order.bonus_lines)} bonus spots"
          f"   ${paid_total:,.2f}")

    # ── Production / non-airtime money ──
    # Goes in the line form's Production box, NOT a line of its own — backwrite
    # is not tuned to accept a production line. Etere stamps the charge with the
    # carrier line's flight start, so there is no date to ask about.
    if order.charges:
        print("\n  Production (→ Production box on the first paid line, not airtime):")
        for ch in order.charges:
            print(f"    {ch.description:<30} ${ch.amount:>10,.2f}")

    # ── Start-date sanity check (lesson #15) ──
    start_override = _confirm_start_date(order)
    if start_override is None:
        print("  ✗ No flight start date — aborting")
        return None

    # Show the actual Etere lines whenever the start date moved — the dates and
    # the max-per-day both change, and a truncated first week can split a line
    # in two. Lee approves the real plan, not the IO's shape.
    if start_override != _parse_date(order.flight_start):
        print(f"\n  Etere lines for a {_fmt_mmddyyyy(start_override)} start:")
        entered = 0
        all_notes: list[str] = []
        for _ln, days, time_range, desc, ranges, notes in _line_plan(
                order, start_override, order.flight_end):
            for rng in ranges:
                entered += rng['spots_per_week'] * rng['weeks']
                tag = f"   ← {rng['tag']}" if rng['tag'] else ""
                print(f"    {desc[:34]:<34} {days:<5} {time_range}  "
                      f"{_fmt_mmddyyyy(rng['date_from'])}–{_fmt_mmddyyyy(rng['date_to'])}"
                      f"  {rng['spots_per_week']}/wk×{rng['weeks']}w"
                      f"  max {rng['max_daily']}/day{tag}")
            all_notes.extend(f"{desc[:34]}: {n}" for n in notes)
        ordered = sum(ln.total_spots for ln in order.lines)
        if all_notes:
            print("\n  ⚠ The later start makes some spots undeliverable:")
            for n in all_notes:
                print(f"      {n}")
        print(f"\n  Spots: {entered} entered of {ordered} ordered"
              f"{'' if entered == ordered else '  ← SHORT'}")
        if entered != ordered:
            raw = input("  Enter the order short anyway? [y/N]: ").strip().lower()
            if raw not in ('y', 'yes'):
                print("  ✗ Aborted — pick an earlier start date or ask the agency "
                      "to revise the IO.")
                return None

    # ── Customer (advertiser) resolution: client + agency → customer id ──
    resolved = _resolve_customer(order.advertiser, agency_id)
    if resolved:
        print(f"\n[CUSTOMER] '{order.advertiser}' + agency {agency_id} → "
              f"ID {resolved['id']}  ({resolved['name']})")
        default_id = str(resolved['id'])
    else:
        print(f"\n[CUSTOMER] Could not auto-resolve '{order.advertiser}' via agency {agency_id}.")
        default_id = ""
    raw_id = input(f"  Customer (ANAGRAF) ID [{default_id}]: ").strip()
    customer_id = raw_id or default_id
    if not customer_id.isdigit():
        print("  ✗ Invalid customer ID — aborting")
        return None
    customer_id = int(customer_id)

    # ── Separation (customer DB default, else industry standard) ──
    separation = (15, 0, 0)
    billing_type = 'agency'   # Crispin is an agency; no commission attached
    cust = _lookup_customer_db(order.advertiser)
    if cust:
        billing_type = cust.billing_type or 'agency'
        separation = (cust.separation_customer, cust.separation_event, cust.separation_order)

    # ── Contract code + description (Lee-given defaults) ──
    start_yymm = _broadcast_yymm(start_override)
    end_yymm = _broadcast_yymm(order.week_dates[-1]) if order.week_dates else start_yymm
    default_code = f"Crispin BAAQMD {start_yymm}"
    default_desc = f"Bay Area AQMD {start_yymm}-{end_yymm}"

    print()
    raw = input(f"  Contract code [{default_code}]: ").strip()
    contract_code = raw or default_code
    raw = input(f"  Description [{default_desc}]: ").strip()
    description = raw or default_desc

    inputs = {
        'customer_id':         customer_id,
        'billing_type':        billing_type,
        'separation':          separation,
        'contract_code':       contract_code,
        'description':         description,
        'start_date_override': _fmt_mmddyyyy(start_override),
    }
    if order.order_number:
        est = f", Est {order.estimate}" if order.estimate else ""
        inputs['customer_ref'] = f"Order {order.order_number}{est}"
    return inputs


def _lookup_customer_db(name: str):
    try:
        import os

        from src.data_access.repositories.customer_repository import CustomerRepository
        if not os.path.exists(CUSTOMER_DB_PATH):
            return None
        return CustomerRepository(CUSTOMER_DB_PATH).find_by_name_any_type(name)
    except Exception:
        return None


# ─── Direct DB Entry ──────────────────────────────────────────────────────────

def _create_crispin_contract(order: CrispinOrder, inputs: dict) -> Optional[str]:
    from browser_automation.etere_direct_client import (
        AGENCY_IDS,
        EtereDirectClient,
        connect,
    )

    customer_id = inputs.get('customer_id')
    if customer_id is None:
        print("[CRISPIN] ✗ No customer_id")
        return None

    separation = inputs.get('separation', (15, 0, 0))
    billing_type = inputs.get('billing_type', 'agency')
    contract_code = inputs.get('contract_code', 'Crispin BAAQMD')
    description = inputs.get('description', '')

    override = inputs.get('start_date_override')
    flight_start_d = (_parse_date(override) if override
                      else _parse_date(order.flight_start) if order.flight_start
                      else None)
    flight_end_d = _parse_date(order.flight_end) if order.flight_end else None
    if not flight_start_d or not flight_end_d:
        print("[CRISPIN] ✗ Could not determine flight range")
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
            # Always query ANAGRAF for the client and use the agency it returns
            # (BAAQMD/Crispin → 446). No commission is attached to the record,
            # so none is applied. agency_id is only a fallback.
            agency_id=AGENCY_IDS["CRISPIN"],
            lookup_customer_defaults=True,
            contract_date=flight_start_d,
            contract_end_date=flight_end_d,
            contract_type=1,
            billing_type=billing_type,
            customer_order_ref=inputs.get('customer_ref', ''),
            allow_rename=True,
        )
        print(f"[CRISPIN] ✓ Contract header: ID={contract_id}  code='{contract_code}'")

        # Production / translation dollars go in the line form's **Production
        # box**, not a line of their own (Lee 2026-08-10): backwrite is not tuned
        # to accept a production line yet, and a zero-spot carrier line would
        # show up there as airtime. Etere turns the box into a CONTRATTISPESE
        # charge named 'Production', dated that line's flight start.
        production_pending = round(sum(ch.amount for ch in order.charges), 2)

        line_count = 0
        # Same planner the gather preview printed — the start-date override, the
        # per-range max-per-day, and any short-week split all come from there, so
        # what Lee approved is what gets written.
        for ln, days, time_range, desc, ranges, notes in _line_plan(
                order, flight_start_d, flight_end_str):
            is_bonus = ln.is_bonus
            booking_code = 10 if is_bonus else 2
            duration_str = str(ln.length_sec)
            for note in notes:
                print(f"  [NOTE] {desc}: {note}")

            for rng in ranges:
                total_spots = rng['spots_per_week'] * rng['weeks']
                date_from, date_to = rng['date_from'], rng['date_to']

                line_count += 1
                tag = f"  ← {rng['tag']}" if rng['tag'] else ""
                print(f"  [LINE {line_count}] {order.market_code} {desc}: "
                      f"{_fmt_mmddyyyy(date_from)}–{_fmt_mmddyyyy(date_to)} "
                      f"({rng['spots_per_week']}/wk×{rng['weeks']}w={total_spots}) "
                      f":{ln.length_sec}s rate={ln.rate} "
                      f"max {rng['max_daily']}/day{tag}")
                # Carried by the first PAID line so the charge sits on billable
                # airtime, then zeroed so it is written exactly once.
                production = production_pending if not is_bonus else 0.0
                production_pending = production_pending - production

                line_id = client.add_contract_line(
                    market=order.market_code,
                    days=days,
                    time_range=time_range,
                    description=desc,
                    rate=ln.rate,
                    total_spots=total_spots,
                    spots_per_week=rng['spots_per_week'],
                    max_daily_run=rng['max_daily'],
                    date_from=date_from,
                    date_to=date_to,
                    duration=duration_str,
                    is_bonus=is_bonus,
                    booking_code=booking_code,
                    separation_intervals=separation,
                    production_cost=production,
                )
                if production:
                    _verify_production_charge(conn.cursor(), line_id, production)
                    print(f"           ↳ Production ${production:,.2f} "
                          f"(→ CONTRATTISPESE 'Production', dated "
                          f"{_fmt_mmddyyyy(date_from)})")

        if production_pending:
            raise RuntimeError(
                f"Crispin: ${production_pending:,.2f} of production cost was never "
                f"attached — no paid airtime line was created. Rolling back."
            )

        conn.commit()
        conn.close()
        print(f"[CRISPIN] ✓ {line_count} lines committed.")
        return contract_code

    except Exception as exc:
        print(f"[CRISPIN] ✗ {exc}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


def _verify_production_charge(cursor, line_id: int, expected: float) -> None:
    """Confirm the SP really turned @production into a CONTRATTISPESE row."""
    _verify_production_charge_impl(cursor, line_id, expected, label="Crispin")


def run_crispin_order(order: CrispinOrder, inputs: dict) -> list[tuple[str, bool]]:
    """Process a Crispin order as a single contract. Returns [(code, success)]."""
    code = _create_crispin_contract(order, inputs)
    label = inputs.get('contract_code') or 'Crispin BAAQMD'
    return [(label, code is not None)]
