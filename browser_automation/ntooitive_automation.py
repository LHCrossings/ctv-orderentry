"""
Ntooitive direct-DB automation (advertiser: L.A. Care).

Ntooitive is an AGENCY parser. The agency is fixed (Ntooitive → ANAGRAF agency
299, Commissione 15%). The advertiser is resolved in ANAGRAF via the agency
link (LA Care Health Plan = 300 / AGENZIA 299).

**Rates are GROSS** — the proposal's NET column is gross × 0.85 and exists only
for reconciliation. Rates enter verbatim and the ANAGRAF commission nets them
down (`lookup_customer_defaults=True`); never multiply in this file (Crispin
lesson — if the money basis looks wrong, fix ANAGRAF).

**The start date is always asked** (Lee 2026-08-14: these orders habitually
enter late). The proposal's own flight start can already sit mid-week (the CRC
2026 REV 2 grid opens Monday 8/17 but the flight starts Tuesday 8/18), so the
line plan runs the shared late-start planner unconditionally: a truncated first
week gets its own max-per-day, and its own Etere line when the cap differs from
the full weeks (order 212735 lesson).

**Dual-window dayparts enter as ONE line with the union window** (Lee
2026-08-14): "M-F 6a-7a & 8p-9p" → 6a-9p, matching how mixed patterns entered
on contract 1879. `EtereClient.parse_time_range`'s semicolon rule (earliest
start + latest end) does the union.

**Translation money never becomes a contract line.** It rides the first paid
line's Production box (→ CONTRATTISPESE 'Production'), verified inside the
transaction.

Prior contracts set the naming convention: code `Ntooitive LACHP <YYMM>`,
description `LA Care HP - <campaign> <startYYMM>-<endYYMM>`.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
from browser_automation.etere_client import EtereClient
from browser_automation.line_planner import (
    WeekCol,
    broadcast_yymm,
    confirm_start_date,
    fmt_mmddyyyy,
    parse_date,
    plan_ranges,
    verify_production_charge,
)
from browser_automation.parsers.ntooitive_parser import (
    NtooitiveOrder,
    find_time_ranges,
    list_ntooitive_options,
    parse_ntooitive,
)
from browser_automation.ros_definitions import ROS_SCHEDULES

# ─── Daypart handling ─────────────────────────────────────────────────────────

def split_daypart_union(daypart: str) -> tuple[str, str]:
    """'M-F 6a-7a & 8p-9p' → ('M-F', '6a-7a; 8p-9p').

    The days are everything before the first time range (normalised: 'M- F' →
    'M-F'). Multiple time ranges are joined with ';' so
    `EtereClient.parse_time_range` applies its union rule (earliest start +
    latest end) — one Etere line per IO line (Lee 2026-08-14).
    """
    dp = " ".join((daypart or "").split())
    ranges = find_time_ranges(dp)
    if not ranges:
        return dp, ""
    first = dp.find(ranges[0])
    days = re.sub(r"\s*([-–])\s*", r"\1", dp[:first]).strip()
    return days, "; ".join(ranges)


# ─── Input gather ─────────────────────────────────────────────────────────────

def _resolve_customer(advertiser: str, agency_id: int) -> Optional[dict]:
    """Advertiser → ANAGRAF customer id, disambiguated by the agency link."""
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

    stop = {"the", "of", "and", "inc", "llc", "co", "health", "plan"}
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
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t}


def _line_plan(order: NtooitiveOrder, start_from: date,
               flight_end_str: str) -> list[tuple]:
    """(line, days, time_range, description, ranges, notes) for every airtime
    line. The single source of truth for what will be entered — the gather
    preview and `_create_ntooitive_contract` both walk this, so what Lee
    approves is exactly what gets written."""
    plan: list[tuple] = []
    for ln in order.lines:
        if ln.total_spots == 0:
            continue

        if ln.is_bonus:
            ros = ROS_SCHEDULES.get(ln.base_language)
            if ros:
                days, time_raw = ros['days'], ros['time']
            else:
                days, time_raw = 'M-Su', '6a-11:59p'
                print(f"  [WARN] No ROS window for '{ln.base_language}' — "
                      f"using {days} {time_raw}")
            desc = f"BNS {ln.base_language} ROS"
        else:
            days, time_raw = split_daypart_union(ln.daypart)
            days, _ = EtereClient.check_sunday_6_7a_rule(days, time_raw)
            desc = f"{ln.language_block.strip()} {ln.daypart.strip()}"[:60]

        time_from, time_to = EtereClient.parse_time_range(time_raw)
        line_end = parse_date(flight_end_str)
        consolidated = EtereClient.consolidate_weeks(
            ln.week_spots, [WeekCol(d) for d in ln.week_dates],
            flight_end=fmt_mmddyyyy(line_end),
        )
        ranges, notes = plan_ranges(consolidated, days, start_from, line_end)
        plan.append((ln, days, f"{time_from}-{time_to}", desc, ranges, notes))
    return plan


def _pick_option(source_path: str) -> Optional[str]:
    """Prompt which Option sheet to enter when the workbook carries several.
    Default = the sheet with the latest revised/proposed date. Returns the
    sheet name (None for a PDF or a single-sheet workbook)."""
    if not source_path.lower().endswith((".xlsx", ".xlsm")):
        return None
    options = list_ntooitive_options(source_path)
    if len(options) <= 1:
        return options[0]["sheet"] if options else None

    newest = max(options, key=lambda o: o["date"] or date.min)
    print("\n  This workbook carries several proposal options:")
    for i, o in enumerate(options, 1):
        d = o["date"].strftime("%m/%d/%Y") if o["date"] else "no date"
        gross = f"${o['gross']:,.2f}" if o["gross"] is not None else "?"
        tag = "  ← newest" if o is newest else ""
        print(f"    {i}. {o['sheet']:<12} {d:<12} flight {o['flight']:<14} {gross}{tag}")
    default_i = options.index(newest) + 1
    while True:
        raw = input(f"  Which option is the order? [{default_i}]: ").strip()
        if not raw or raw.lower() in ('y', 'yes'):
            return newest["sheet"]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]["sheet"]
        print(f"    ✗ Enter a number 1–{len(options)}.")


def _default_code_desc(order: NtooitiveOrder, start: date,
                       cust) -> tuple[str, str, str, str]:
    """Prior-contract conventions: code 'Ntooitive LACHP 2608', description
    'LA Care HP - CRC Campaign 2608-2612'. Returns (code, desc, short,
    desc_prefix) — short/desc_prefix feed the customers.db upsert."""
    start_yymm = broadcast_yymm(start)
    end_yymm = broadcast_yymm(parse_date(order.flight_end)) if order.flight_end else start_yymm
    adv = order.advertiser.strip()
    is_la_care = "la care" in adv.lower() or "l.a. care" in adv.lower()

    short = (cust.code_name if cust and getattr(cust, "code_name", "") else "")
    if not short:
        short = "LACHP" if is_la_care else re.sub(r"[^A-Za-z0-9]", "", adv)[:8].upper()

    desc_prefix = (cust.description_name
                   if cust and getattr(cust, "description_name", "") else "")
    if not desc_prefix:
        if is_la_care:
            # 'L.A. Care- CRC' → campaign tail 'CRC'
            tail = adv.split("-")[-1].strip() if "-" in adv else ""
            desc_prefix = f"LA Care HP - {tail} Campaign" if tail else "LA Care HP"
        else:
            desc_prefix = adv

    code = f"Ntooitive {short} {start_yymm}"
    desc = f"{desc_prefix} {start_yymm}-{end_yymm}"
    return code, desc, short, desc_prefix


def _lookup_customer_db(name: str):
    try:
        import os

        from src.data_access.repositories.customer_repository import CustomerRepository
        if not os.path.exists(CUSTOMER_DB_PATH):
            return None
        return CustomerRepository(CUSTOMER_DB_PATH).find_by_name_any_type(name)
    except Exception:
        return None


def _upsert_customer_db(name: str, customer_id: int, code_name: str,
                        description_name: str, separation: tuple) -> None:
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        from src.domain.enums import OrderType
        CustomerRepository(CUSTOMER_DB_PATH).save(Customer(
            customer_id=str(customer_id),
            customer_name=name,
            order_type=OrderType.NTOOITIVE,
            billing_type='agency',
            code_name=code_name,
            description_name=description_name,
            separation_customer=separation[0],
            separation_event=separation[1],
            separation_order=separation[2],
        ))
    except Exception as exc:
        print(f"[CUSTOMER] customers.db upsert failed (non-fatal): {exc}")


def gather_ntooitive_inputs(source_path: str) -> Optional[dict]:
    """Gather inputs for an Ntooitive order (proposal workbook or PDF print).

    Returns dict or None to abort.
    """
    from browser_automation.etere_direct_client import AGENCY_IDS

    sheet_name = _pick_option(source_path)
    order = parse_ntooitive(source_path, sheet_name=sheet_name)
    agency_id = AGENCY_IDS["NTOOITIVE"]

    print(f"\n{'='*64}")
    src = "PDF print of the proposal" if order.source_format == 'pdf' \
        else f"proposal workbook (sheet '{order.option_label}')"
    print(f"Source:     {src}")
    print(f"Agency:     {order.agency}  (fixed — Etere agency ID {agency_id})")
    print(f"Advertiser: {order.advertiser}")

    # ── Market ──
    market_code = order.market_code
    if market_code:
        print(f"Market:     {market_code}   ({order.market_label})")
    else:
        raw = input(f"  Market code (CVC/SFO/LAX/SEA/HOU/CMP/WDC/NYC) "
                    f"for '{order.market_label}': ").strip().upper()
        if raw not in ("CVC", "SFO", "LAX", "SEA", "HOU", "CMP", "WDC", "NYC", "MMT"):
            print("  ✗ Unknown market — aborting")
            return None
        market_code = raw
    if order.order_date:
        print(f"Dated:      {order.order_date:%B %d, %Y}")
    print(f"Flight:     {order.flight_start} → {order.flight_end}  "
          f"({len(order.week_dates)} weeks)")
    print(f"Money:      GROSS rates; implied commission "
          f"{order.implied_commission:.1%} (ANAGRAF commission nets down at entry)")

    print("\n  Lines:")
    for ln in order.lines:
        tag = "BNS" if ln.is_bonus else "   "
        if ln.is_bonus:
            ros = ROS_SCHEDULES.get(ln.base_language, {})
            days, time = ros.get('days', 'M-Su'), ros.get('time', 'ROS')
        else:
            days, time = split_daypart_union(ln.daypart)
        rate = f"${ln.rate:.2f}" if ln.rate else "  bonus"
        print(f"    {tag} :{ln.length_sec}s {ln.language_block:<18} {days} {time:<16} "
              f"{rate:>8}  {ln.total_spots} spots")

    paid_total = sum(ln.rate * ln.total_spots for ln in order.paid_lines)
    print(f"\n  Airtime: {sum(ln.total_spots for ln in order.paid_lines)} paid + "
          f"{sum(ln.total_spots for ln in order.bonus_lines)} bonus spots"
          f"   ${paid_total:,.2f} gross")

    if order.charges:
        print("\n  Charges (→ Production box on the first paid line, not airtime):")
        for ch in order.charges:
            print(f"    {ch.description:<30} ${ch.amount:>10,.2f}")

    # ── Start date: ALWAYS asked for Ntooitive (habitually entered late) ──
    start_override = confirm_start_date(order.flight_start, order.flight_end,
                                        always_ask=True)
    if start_override is None:
        print("  ✗ No flight start date — aborting")
        return None

    # Show the actual Etere lines whenever the start sits past the first week's
    # Monday — the proposal's own flight can already open mid-week, and the
    # dates + max-per-day both change. Lee approves the real plan.
    first_monday = order.week_dates[0] if order.week_dates else start_override
    if start_override > first_monday:
        print(f"\n  Etere lines for a {fmt_mmddyyyy(start_override)} start:")
        entered = 0
        all_notes: list[str] = []
        for _ln, days, time_range, desc, ranges, notes in _line_plan(
                order, start_override, order.flight_end):
            for rng in ranges:
                entered += rng['spots_per_week'] * rng['weeks']
                tag = f"   ← {rng['tag']}" if rng['tag'] else ""
                print(f"    {desc[:34]:<34} {days:<5} {time_range}  "
                      f"{fmt_mmddyyyy(rng['date_from'])}–{fmt_mmddyyyy(rng['date_to'])}"
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
                      "to revise the order.")
                return None

    # ── Customer (advertiser) resolution: client + agency → customer id ──
    resolved = _resolve_customer(order.advertiser, agency_id)
    if resolved:
        print(f"\n[CUSTOMER] '{order.advertiser}' + agency {agency_id} → "
              f"ID {resolved['id']}  ({resolved['name']})")
        default_id = str(resolved['id'])
    else:
        print(f"\n[CUSTOMER] Could not auto-resolve '{order.advertiser}' via "
              f"agency {agency_id}.")
        default_id = ""
    raw_id = input(f"  Customer (ANAGRAF) ID [{default_id}]: ").strip()
    customer_id = raw_id or default_id
    if not customer_id.isdigit():
        print("  ✗ Invalid customer ID — aborting")
        return None
    customer_id = int(customer_id)

    # ── Separation (customer DB default, else industry standard) ──
    separation = (15, 0, 0)
    billing_type = 'agency'
    cust = _lookup_customer_db(order.advertiser) or _lookup_customer_db(
        resolved['name'] if resolved else "")
    if cust:
        billing_type = cust.billing_type or 'agency'
        separation = (cust.separation_customer, cust.separation_event,
                      cust.separation_order)

    # ── Contract code + description (prior-contract conventions) ──
    default_code, default_desc, short, desc_prefix = _default_code_desc(
        order, start_override, cust)
    print()
    raw = input(f"  Contract code [{default_code}]: ").strip()
    contract_code = raw or default_code
    raw = input(f"  Description [{default_desc}]: ").strip()
    description = raw or default_desc

    cust_name = resolved['name'] if resolved else order.advertiser
    _upsert_customer_db(cust_name, customer_id, code_name=short,
                        description_name=desc_prefix, separation=separation)

    return {
        'customer_id':         customer_id,
        'billing_type':        billing_type,
        'separation':          separation,
        'contract_code':       contract_code,
        'description':         description,
        'start_date_override': fmt_mmddyyyy(start_override),
        'market':              market_code,
        'sheet_name':          order.option_label if order.source_format == 'xlsx' else None,
    }


# ─── Direct DB entry ──────────────────────────────────────────────────────────

def _create_ntooitive_contract(order: NtooitiveOrder, inputs: dict) -> Optional[str]:
    from browser_automation.etere_direct_client import (
        AGENCY_IDS,
        EtereDirectClient,
        connect,
    )

    customer_id = inputs.get('customer_id')
    if customer_id is None:
        print("[NTOOITIVE] ✗ No customer_id")
        return None

    separation = inputs.get('separation', (15, 0, 0))
    billing_type = inputs.get('billing_type', 'agency')
    contract_code = inputs.get('contract_code', 'Ntooitive')
    description = inputs.get('description', '')
    market_code = inputs.get('market') or order.market_code
    if not market_code:
        print("[NTOOITIVE] ✗ No market code")
        return None

    override = inputs.get('start_date_override')
    flight_start_d = (parse_date(override) if override
                      else parse_date(order.flight_start) if order.flight_start
                      else None)
    flight_end_d = parse_date(order.flight_end) if order.flight_end else None
    if not flight_start_d or not flight_end_d:
        print("[NTOOITIVE] ✗ Could not determine flight range")
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
            # Always query ANAGRAF for the client and use the agency it returns
            # (LA Care → 299, Commissione 15% — this is what nets the GROSS
            # rates down). agency_id is only a fallback.
            agency_id=AGENCY_IDS["NTOOITIVE"],
            lookup_customer_defaults=True,
            contract_date=flight_start_d,
            contract_end_date=flight_end_d,
            contract_type=1,
            billing_type=billing_type,
            allow_rename=True,
        )
        print(f"[NTOOITIVE] ✓ Contract header: ID={contract_id}  "
              f"code='{contract_code}'")

        # Translation money rides the first PAID line's Production box so the
        # charge sits on billable airtime, written exactly once.
        production_pending = round(sum(ch.amount for ch in order.charges), 2)

        line_count = 0
        # Same planner the gather preview printed — what Lee approved is what
        # gets written.
        for ln, days, time_range, desc, ranges, notes in _line_plan(
                order, flight_start_d, order.flight_end):
            is_bonus = ln.is_bonus
            booking_code = 10 if is_bonus else 2
            for note in notes:
                print(f"  [NOTE] {desc}: {note}")

            for rng in ranges:
                total_spots = rng['spots_per_week'] * rng['weeks']
                date_from, date_to = rng['date_from'], rng['date_to']

                line_count += 1
                tag = f"  ← {rng['tag']}" if rng['tag'] else ""
                print(f"  [LINE {line_count}] {market_code} {desc}: "
                      f"{fmt_mmddyyyy(date_from)}–{fmt_mmddyyyy(date_to)} "
                      f"({rng['spots_per_week']}/wk×{rng['weeks']}w={total_spots}) "
                      f":{ln.length_sec}s rate={ln.rate} "
                      f"max {rng['max_daily']}/day{tag}")
                production = production_pending if not is_bonus else 0.0
                production_pending = production_pending - production

                line_id = client.add_contract_line(
                    market=market_code,
                    days=days,
                    time_range=time_range,
                    description=desc,
                    rate=ln.rate,
                    total_spots=total_spots,
                    spots_per_week=rng['spots_per_week'],
                    max_daily_run=rng['max_daily'],
                    date_from=date_from,
                    date_to=date_to,
                    duration=str(ln.length_sec),
                    is_bonus=is_bonus,
                    booking_code=booking_code,
                    separation_intervals=separation,
                    production_cost=production,
                )
                if production:
                    verify_production_charge(conn.cursor(), line_id, production,
                                             label="Ntooitive")
                    print(f"           ↳ Production ${production:,.2f} "
                          f"(→ CONTRATTISPESE 'Production', dated "
                          f"{fmt_mmddyyyy(date_from)})")

        if production_pending:
            raise RuntimeError(
                f"Ntooitive: ${production_pending:,.2f} of translation/production "
                f"cost was never attached — no paid airtime line was created. "
                f"Rolling back."
            )

        conn.commit()
        conn.close()
        print(f"[NTOOITIVE] ✓ {line_count} lines committed.")
        return contract_code

    except Exception as exc:
        print(f"[NTOOITIVE] ✗ {exc}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


def run_ntooitive_order(order: NtooitiveOrder, inputs: dict) -> list[tuple[str, bool]]:
    """Process an Ntooitive order as a single contract. Returns [(code, success)]."""
    code = _create_ntooitive_contract(order, inputs)
    label = inputs.get('contract_code') or 'Ntooitive'
    return [(label, code is not None)]
