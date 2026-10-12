"""
The Vida Agency direct-DB automation (advertiser: Puget Sound Energy).

Vida is an AGENCY parser: the agency is fixed (The Vida Agency → ANAGRAF 260)
and the advertiser is resolved among the clients linked to it (Puget Sound
Energy = 344, WA Health Benefit Exchange = 360, WA HAF = 261) by the words of
the proposal's "Project" line.

**No agency commission on these contracts** (AE note 2026-10-12; precedent
Vida PSE 2410 / 2411 carry P_AGENZIA 0.00 while the agency row in ANAGRAF says
15%). The header is written with an explicit `agency_pct=0.0`, which
`create_contract_header` honours over the ANAGRAF lookup; rates are NET and
enter verbatim, so gross == net.

**One contract per flight** (Lee 2026-10-12): the proposal packs October and
December into one grid and March 2027 on page 2; each flight gets its own
header, code `Vida PSE <YYMM>` and description `Puget Sound Energy - <project
tail> <YYMM>` (YYMM = broadcast month of the flight's start).

**Late / mid-week starts** go through the shared `line_planner`: the start date
is ALWAYS asked for the first flight (these orders enter late — this one
opened Tuesday 10/13 on a Monday 10/12 grid), and every flight is planned from
its real first day (December opens Tue 12/1 on a 11/30 week), so a short first
or last week gets its own max-per-day line.

**Dual daypart → ONE line on the union window** ("10a-11a/12p-1p" → 10a-1p),
the Ntooitive / HPSJ precedent. Bonus ROS → the Vietnamese ROS window.

**Production** (the sheet's Net Amt, $1,000 here) rides the FIRST flight's first
paid line's Production box → CONTRATTISPESE, verified inside the transaction.

Separation precedent: 5 / 0 / 0 (Vida PSE 2410 / 2411) — needed to fit four
spots a day inside a one-hour show on the compressed weeks.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
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
from browser_automation.parsers.vida_parser import VidaFlight, VidaLine, VidaOrder, parse_vida
from browser_automation.ros_definitions import ROS_SCHEDULES

VIDA_DEFAULT_SEPARATION = (5, 0, 0)  # customer, order, event — Vida PSE 2410/2411
VIDA_AGENCY_PCT = 0.0  # AE 2026-10-12: no agency commission
_STOP = {"the", "of", "and", "inc", "llc", "co", "project", "flex", "campaign", "energy"}


# ─── Planning ─────────────────────────────────────────────────────────────────


def _ampm(hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    suffix = "a" if h < 12 else "p"
    h12 = h if 1 <= h <= 12 else (h - 12 if h > 12 else 12)
    return f"{h12}{'' if m == 0 else f':{m:02d}'}{suffix}"


def line_window(ln: VidaLine) -> tuple[str, str, str]:
    """(days, 'HH:MM-HH:MM', 'compact label') for a line. A dual daypart is the
    union of its pieces (parse_time_range's semicolon rule: earliest start +
    latest end). A bonus row takes the language's ROS window."""
    if ln.is_bonus:
        ros = ROS_SCHEDULES.get(ln.language or "")
        if ros:
            days, time_raw = ros["days"], ros["time"]
        else:
            days, time_raw = "M-Su", "6a-11:59p"
            print(f"  [WARN] No ROS window for '{ln.language}' — using {days} {time_raw}")
    else:
        days, time_raw = ln.days, "; ".join(ln.time_ranges)
        days, _ = EtereClient.check_sunday_6_7a_rule(days, time_raw)
    t_from, t_to = EtereClient.parse_time_range(time_raw)
    label = f"{_ampm(t_from)}-{_ampm(t_to)}"
    return days, f"{t_from}-{t_to}", label


def line_description(ln: VidaLine, days: str, label: str) -> str:
    """Precedent 'M-Su 11a-12n Viet' / 'BONUS M-Su Viet ROS' → 'M-Su 11a-12p Viet
    News/Variety', 'M-Su 10a-1p Viet Drama', 'BNS M-Su Viet ROS'."""
    lang = ln.language or "Viet"
    short_lang = "Viet" if lang.lower() == "vietnamese" else lang
    if ln.is_bonus:
        return f"BNS {days} {short_lang} ROS"[:60]
    return f"{days} {label} {short_lang} {ln.program_short}".strip()[:60]


def flight_plan(flight: VidaFlight, start_from: date) -> list[tuple]:
    """(line, days, time_range, description, ranges, notes) for every airtime line
    of one flight — the single source of truth the gather preview and the entry
    both walk, so what Lee approves is what gets written."""
    plan: list[tuple] = []
    for ln in flight.lines:
        if ln.total_spots == 0:
            continue
        days, time_range, label = line_window(ln)
        desc = line_description(ln, days, label)
        consolidated = EtereClient.consolidate_weeks(
            ln.week_spots,
            [WeekCol(d) for d in ln.week_dates],
            flight_end=fmt_mmddyyyy(flight.end),
        )
        ranges, notes = plan_ranges(consolidated, days, start_from, flight.end)
        plan.append((ln, days, time_range, desc, ranges, notes))
    return plan


def default_code_desc(
    order: VidaOrder, flight: VidaFlight, start: date, cust=None
) -> tuple[str, str, str, str]:
    """Precedent: code 'Vida PSE 2411', description 'Puget Sound Energy - <campaign>
    2411'. Returns (code, desc, short, desc_prefix)."""
    yymm = broadcast_yymm(start)
    short = (cust.code_name if cust and getattr(cust, "code_name", "") else "") or "PSE"
    desc_prefix = cust.description_name if cust and getattr(cust, "description_name", "") else ""
    if not desc_prefix:
        tail = re.sub(r"(?i)^puget\s+sound\s*", "", order.project).strip() or order.project
        desc_prefix = f"Puget Sound Energy - {tail}"
    return f"Vida {short} {yymm}", f"{desc_prefix} {yymm}", short, desc_prefix


# ─── Customer resolution ──────────────────────────────────────────────────────


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t}


def _resolve_customer(project: str, agency_id: int) -> Optional[dict]:
    """Project words → the ANAGRAF client linked to the agency with the best
    token overlap ('Puget Sound Flex' → Puget Sound Energy)."""
    try:
        from browser_automation.etere_direct_client import connect

        conn = connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT ID_ANAGRAF, RAG_SOCIAL FROM ANAGRAF WHERE AGENZIA = %s", (int(agency_id),)
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as exc:
        print(f"[CUSTOMER] ANAGRAF lookup failed: {exc}")
        return None
    want = _tokens(project) - _STOP
    best, best_score = None, 0
    for anid, name in rows:
        score = len(want & (_tokens(name) - _STOP))
        if score > best_score:
            best, best_score = (int(anid), str(name).strip()), score
    return {"id": best[0], "name": best[1]} if best else None


def _lookup_customer_db(name: str):
    try:
        import os

        from src.data_access.repositories.customer_repository import CustomerRepository

        if not os.path.exists(CUSTOMER_DB_PATH):
            return None
        return CustomerRepository(CUSTOMER_DB_PATH).find_by_name_any_type(name)
    except Exception:
        return None


def _upsert_customer_db(
    name: str, customer_id: int, code_name: str, description_name: str, separation: tuple
) -> None:
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        from src.domain.enums import OrderType

        CustomerRepository(CUSTOMER_DB_PATH).save(
            Customer(
                customer_id=str(customer_id),
                customer_name=name,
                order_type=OrderType.VIDA,
                billing_type="agency",
                code_name=code_name,
                description_name=description_name,
                separation_customer=separation[0],
                separation_order=separation[1],
                separation_event=separation[2],
                default_market="SEA",
            )
        )
    except Exception as exc:
        print(f"[CUSTOMER] customer DB upsert failed (non-fatal): {exc}")


# ─── Gather ───────────────────────────────────────────────────────────────────


def _print_plan(plan: list[tuple]) -> tuple[int, list[str]]:
    entered = 0
    all_notes: list[str] = []
    for _ln, days, time_range, desc, ranges, notes in plan:
        for rng in ranges:
            entered += rng["spots_per_week"] * rng["weeks"]
            tag = f"   ← {rng['tag']}" if rng["tag"] else ""
            print(
                f"      {desc[:30]:<30} {days:<5} {time_range}  "
                f"{fmt_mmddyyyy(rng['date_from'])}–{fmt_mmddyyyy(rng['date_to'])}"
                f"  {rng['spots_per_week']}/wk×{rng['weeks']}w"
                f"  max {rng['max_daily']}/day{tag}"
            )
        all_notes.extend(f"{desc[:30]}: {n}" for n in notes)
    return entered, all_notes


def gather_vida_inputs(source_path: str) -> Optional[dict]:
    """Gather inputs for a Vida Agency proposal. Returns dict or None to abort."""
    from browser_automation.etere_direct_client import AGENCY_IDS

    order = parse_vida(source_path)
    agency_id = AGENCY_IDS["VIDA"]

    print(f"\n{'=' * 64}")
    print(
        f"Agency:     {order.agency}  (fixed — Etere agency ID {agency_id}, "
        f"commission {VIDA_AGENCY_PCT:.0f}% on this client)"
    )
    print(f"Project:    {order.project}")
    print(f"Market:     {order.market_code or '?'}   ({order.market_label})")
    if order.order_date:
        print(f"Dated:      {order.order_date:%B %d, %Y}")
    print("Money:      NET rates, entered verbatim (0% commission ⇒ gross == net)")
    print(f"Flights:    {len(order.flights)} → one contract each")

    market_code = order.market_code
    if not market_code:
        raw = input("  Market code (CVC/SFO/LAX/SEA/HOU/CMP/WDC/NYC): ").strip().upper()
        if raw not in ("CVC", "SFO", "LAX", "SEA", "HOU", "CMP", "WDC", "NYC", "MMT"):
            print("  ✗ Unknown market — aborting")
            return None
        market_code = raw

    for i, fl in enumerate(order.flights, 1):
        print(f"\n  Flight {i}: {fl.label}  ({len(fl.week_dates)} weeks)  ${fl.net_total:,.2f} net")
        for ln in fl.lines:
            days, _tr, label = line_window(ln)
            tag = "BNS" if ln.is_bonus else "   "
            rate = f"${ln.rate:.2f}" if ln.rate else "  bonus"
            print(
                f"    {tag} :{ln.length_sec}s {ln.program:<26} {days:<5} {label:<10}"
                f" {rate:>8}  {ln.total_spots} spots  {ln.week_spots}"
            )
    if order.charges:
        print("\n  Charges (→ Production box on flight 1's first paid line, not airtime):")
        for ch in order.charges:
            lst = f"  (list ${ch.list_amount:,.2f})" if ch.list_amount else ""
            print(f"    {ch.description:<20} ${ch.amount:>10,.2f}{lst}")
    print(
        f"\n  Order: {sum(ln.total_spots for fl in order.flights for ln in fl.paid_lines)} paid"
        f" + {sum(ln.total_spots for fl in order.flights for ln in fl.bonus_lines)} bonus"
        f" spots   ${order.net_airtime:,.2f} net airtime"
    )

    # ── Customer: project words → client linked to the agency ──
    resolved = _resolve_customer(order.project, agency_id)
    if resolved:
        print(
            f"\n[CUSTOMER] '{order.project}' + agency {agency_id} → "
            f"ID {resolved['id']}  ({resolved['name']})"
        )
        default_id = str(resolved["id"])
    else:
        print(f"\n[CUSTOMER] Could not auto-resolve '{order.project}' via agency {agency_id}.")
        default_id = ""
    raw_id = input(f"  Customer (ANAGRAF) ID [{default_id}]: ").strip()
    customer_id = raw_id or default_id
    if not customer_id.isdigit():
        print("  ✗ Invalid customer ID — aborting")
        return None
    customer_id = int(customer_id)
    cust_name = resolved["name"] if resolved else order.project

    # ── Separation / billing (customer DB, else the Vida PSE precedent) ──
    separation = VIDA_DEFAULT_SEPARATION
    billing_type = "agency"
    cust = _lookup_customer_db(cust_name)
    if cust:
        billing_type = cust.billing_type or "agency"
        separation = (cust.separation_customer, cust.separation_order, cust.separation_event)

    # ── Per flight: start date, line plan, code / description ──
    flights_in: list[dict] = []
    today = date.today()
    for i, fl in enumerate(order.flights, 1):
        print(f"\n  ── Flight {i}: {fl.label} ──")
        # Flight 1 is always asked (these orders enter late); later flights only when late.
        start = confirm_start_date(
            fmt_mmddyyyy(fl.start), fmt_mmddyyyy(fl.end), always_ask=(i == 1)
        )
        if start is None:
            print("  ✗ No flight start date — aborting")
            return None
        plan = flight_plan(fl, start)
        print(f"\n    Etere lines for a {fmt_mmddyyyy(start)} start:")
        entered, notes = _print_plan(plan)
        ordered = sum(ln.total_spots for ln in fl.lines)
        if notes:
            print("\n    ⚠ The start date makes some spots undeliverable:")
            for n in notes:
                print(f"        {n}")
        print(
            f"    Spots: {entered} entered of {ordered} ordered"
            f"{'' if entered == ordered else '  ← SHORT'}"
        )
        if entered != ordered:
            raw = input("    Enter this flight short anyway? [y/N]: ").strip().lower()
            if raw not in ("y", "yes"):
                print("  ✗ Aborted — pick an earlier start date or ask the agency to revise.")
                return None

        default_code, default_desc, short, desc_prefix = default_code_desc(order, fl, start, cust)
        raw = input(f"    Contract code [{default_code}]: ").strip()
        code = raw or default_code
        raw = input(f"    Description [{default_desc}]: ").strip()
        desc = raw or default_desc

        # A flight that opens weeks out usually has no creative yet — leave a note
        # on its lines so traffic is checked before it airs (AE 2026-10-12: "not
        # sure which traffic to use for December").
        note = ""
        if i > 1 and start > today + timedelta(days=21):
            default_note = (
                f"Creative/traffic for this flight not confirmed at entry "
                f"({today.month}/{today.day}/{today:%y}) - check before "
                f"{start.month}/{start.day}/{start:%y}"
            )
            raw = input(f"    Line note [{default_note}] ('-' = none): ").strip()
            note = "" if raw == "-" else (raw or default_note)

        flights_in.append(
            {
                "index": i - 1,
                "contract_code": code,
                "description": desc,
                "start_date_override": fmt_mmddyyyy(start),
                "note": note[:399],
            }
        )

    _upsert_customer_db(
        cust_name, customer_id, code_name=short, description_name=desc_prefix, separation=separation
    )

    return {
        "customer_id": customer_id,
        "billing_type": billing_type,
        "separation": separation,
        "market": market_code,
        "agency_pct": VIDA_AGENCY_PCT,
        "flights": flights_in,
        # Batch-summary label: the first contract's code (lesson: the gathered code,
        # never the DB id). Every flight's code is returned by run_vida_order.
        "contract_code": flights_in[0]["contract_code"],
        "description": flights_in[0]["description"],
        "start_date_override": flights_in[0]["start_date_override"],
    }


# ─── Direct DB entry ──────────────────────────────────────────────────────────


def _create_vida_contract(
    order: VidaOrder, flight: VidaFlight, fin: dict, inputs: dict, production: float
) -> Optional[str]:
    from browser_automation.etere_direct_client import (
        AGENCY_IDS,
        EtereDirectClient,
        connect,
    )

    customer_id = inputs.get("customer_id")
    market_code = inputs.get("market") or order.market_code
    if customer_id is None or not market_code:
        print("[VIDA] ✗ No customer_id / market")
        return None
    separation = tuple(inputs.get("separation") or VIDA_DEFAULT_SEPARATION)
    billing_type = inputs.get("billing_type", "agency")
    agency_pct = float(inputs.get("agency_pct", VIDA_AGENCY_PCT))
    code = fin["contract_code"]
    description = fin["description"]
    note = fin.get("note", "")
    start = (
        parse_date(fin["start_date_override"]) if fin.get("start_date_override") else flight.start
    )
    end = flight.end

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=code,
            description=description,
            customer_id=int(customer_id),
            agency_id=AGENCY_IDS["VIDA"],
            agency_pct=agency_pct,  # explicit 0% beats the ANAGRAF 15% (precedent)
            lookup_customer_defaults=True,  # media center / payment terms from ANAGRAF
            contract_date=start,
            contract_end_date=end,
            contract_type=1,
            billing_type=billing_type,
            allow_rename=True,
        )
        print(
            f"[VIDA] ✓ Contract header: ID={contract_id}  code='{code}'  "
            f"{fmt_mmddyyyy(start)}–{fmt_mmddyyyy(end)}  commission {agency_pct:.0f}%"
        )

        production_pending = round(production, 2)
        line_count = 0
        for ln, days, time_range, desc, ranges, notes in flight_plan(flight, start):
            booking_code = 10 if ln.is_bonus else 2
            for n in notes:
                print(f"  [NOTE] {desc}: {n}")
            for rng in ranges:
                total_spots = rng["spots_per_week"] * rng["weeks"]
                line_count += 1
                tag = f"  ← {rng['tag']}" if rng["tag"] else ""
                print(
                    f"  [LINE {line_count}] {market_code} {desc}: "
                    f"{fmt_mmddyyyy(rng['date_from'])}–{fmt_mmddyyyy(rng['date_to'])} "
                    f"({rng['spots_per_week']}/wk×{rng['weeks']}w={total_spots}) "
                    f":{ln.length_sec}s rate={ln.rate} max {rng['max_daily']}/day{tag}"
                )
                prod = production_pending if not ln.is_bonus else 0.0
                production_pending = round(production_pending - prod, 2)
                line_id = client.add_contract_line(
                    market=market_code,
                    days=days,
                    time_range=time_range,
                    description=desc,
                    rate=ln.rate,
                    total_spots=total_spots,
                    spots_per_week=rng["spots_per_week"],
                    max_daily_run=rng["max_daily"],
                    date_from=rng["date_from"],
                    date_to=rng["date_to"],
                    duration=str(ln.length_sec),
                    is_bonus=ln.is_bonus,
                    booking_code=booking_code,
                    separation_intervals=separation,
                    production_cost=prod,
                    note=note,
                )
                if prod:
                    verify_production_charge(conn.cursor(), line_id, prod, label="Vida")
                    print(f"           ↳ Production ${prod:,.2f} (→ CONTRATTISPESE 'Production')")

        if production_pending:
            raise RuntimeError(
                f"Vida: ${production_pending:,.2f} production was never "
                "attached — no paid line on this flight. Rolling back."
            )

        # Read back the header money basis before committing.
        cur = conn.cursor()
        cur.execute(
            "SELECT P_AGENZIA, AGENZIA, CENTROMEDIA FROM CONTRATTITESTATA "
            "WHERE ID_CONTRATTITESTATA = %s",
            (int(contract_id),),
        )
        row = cur.fetchone()
        if (
            row is None
            or abs(float(row[0] or 0) - agency_pct) > 0.001
            or int(row[1] or 0) != AGENCY_IDS["VIDA"]
        ):
            raise RuntimeError(
                f"Vida: header readback P_AGENZIA={row and row[0]} "
                f"AGENZIA={row and row[1]} — expected {agency_pct} / "
                f"{AGENCY_IDS['VIDA']}; rolling back"
            )
        conn.commit()
        conn.close()
        print(f"[VIDA] ✓ {line_count} lines committed on {code} (CENTROMEDIA {row[2]}).")
        return code

    except Exception as exc:
        print(f"[VIDA] ✗ {exc}")
        import traceback

        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


def run_vida_order(order: VidaOrder, inputs: dict) -> list[tuple[str, bool]]:
    """One contract per flight. Returns [(code, success)] — the codes actually
    created (autocommit per contract, so a partial batch is reported as such)."""
    results: list[tuple[str, bool]] = []
    production_total = round(sum(ch.amount for ch in order.charges), 2)
    flights_in = inputs.get("flights") or []
    if not flights_in:
        print("[VIDA] ✗ gather returned no flights")
        return []
    for fin in flights_in:
        flight = order.flights[fin["index"]]
        production = production_total if fin["index"] == 0 else 0.0
        code = _create_vida_contract(order, flight, fin, inputs, production)
        results.append((fin["contract_code"], code is not None))
        if code is None:
            print(
                f"[VIDA] ✗ stopping after flight {fin['index'] + 1} failed; "
                f"{len(results) - 1} contract(s) already committed"
            )
            break
    return results
