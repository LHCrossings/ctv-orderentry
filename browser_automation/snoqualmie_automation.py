"""
Snoqualmie Casino & Hotel direct-DB automation (house proposal, Seattle).

Snoqualmie is a DIRECT client: ANAGRAF 417, no agency, 0% commission,
CENTROMEDIA 316 Broadcast (precedent 2422 `Snoqualmie 2602` / `Snoqualmie
Casino LNY 2026`, CUSTOMERREF `LNY 2026`, :15, SEA). The sheet's PROMO rate is
the entered rate; with no commission gross == net.

**Late starts** go through the shared `line_planner`: the start date is ALWAYS
asked (these orders enter after the flight opens) and every line is planned
from its real first day, so a short first week gets its own max-per-day line.

**Dual daypart → ONE line on the union window** ("4p-5p, 6p-7p" → 4p-7p), the
Ntooitive / HPSJ / Vida precedent. Bonus ROS rows take the house ROS window for
their language (`ROS_SCHEDULES`).

**Production**: the sheet's Subtitle charge rows are combined into ONE amount
(Lee 2026-10-15) on the first paid line's Production box → CONTRATTISPESE,
verified inside the transaction.

**Traffic notes** printed under the grid ("ROTATE 3 SPOTS EVENLY", per-creative
end dates, the English-spot-in-Filipino note) become the line note on every
line so the traffic step sees them.

Separation: the customer DB row when present, else the house default 15/0/0
(2422 carried 25 on most lines and 10-15 on its make-goods; a tighter interval
is the operator's call at the orchestrator's separation prompt).
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
from browser_automation.parsers.snoqualmie_parser import (
    CLIENT_NAME,
    SnoqualmieLine,
    SnoqualmieOrder,
    parse_snoqualmie,
)
from browser_automation.ros_definitions import ROS_SCHEDULES

DEFAULT_CUSTOMER_ID = 417  # ANAGRAF: Snoqualmie Casino (direct, 0%)
DEFAULT_MARKET = "SEA"
DEFAULT_SEPARATION = (15, 0, 0)  # customer, order, event
PRECEDENT_SEPARATION = (25, 0, 0)  # 2422's paid lines (Feb 2026)
DEFAULT_CODE_PREFIX = "Snoqualmie"
MEDIA_CENTER = {"broadcast": 316, "calendar": 317}  # CONTRATTITESTATA.CENTROMEDIA
_MARKETS = ("CVC", "SFO", "LAX", "SEA", "HOU", "CMP", "WDC", "NYC", "MMT", "DAL")
_LABEL = "[SNOQ]"


# ─── Planning ─────────────────────────────────────────────────────────────────


def _short_time(hhmm: str) -> str:
    """'19:00' -> '7p', '23:59' -> '12a', '08:00' -> '8a', '13:30' -> '1:30p'."""
    h, m = (int(x) for x in hhmm.split(":"))
    if hhmm == "23:59":
        return "12a"
    suffix = "a" if h < 12 else "p"
    h12 = h % 12 or 12
    return f"{h12}{suffix}" if m == 0 else f"{h12}:{m:02d}{suffix}"


def line_window(ln: SnoqualmieLine) -> tuple[str, str, str]:
    """(days, 'HH:MM-HH:MM', 'compact label') for a line. A dual daypart is the
    union of its pieces (parse_time_range's semicolon rule: earliest start +
    latest end). A bonus row takes the language's house ROS window."""
    if ln.is_bonus:
        ros = ROS_SCHEDULES.get(ln.language or "")
        if not ros:
            raise ValueError(f"{ln.program}: no house ROS window for {ln.language!r}")
        days, time_raw = ros["days"], ros["time"]
    else:
        days, time_raw = ln.days, "; ".join(ln.time_ranges)
        days, _ = EtereClient.check_sunday_6_7a_rule(days, time_raw)
    t_from, t_to = EtereClient.parse_time_range(time_raw)
    return days, f"{t_from}-{t_to}", f"{_short_time(t_from)}-{_short_time(t_to)}"


def _short_lang(lang: str) -> str:
    return "Viet" if lang.lower() == "vietnamese" else lang


def line_description(ln: SnoqualmieLine, days: str, label: str) -> str:
    """'M-F 7p-9p Chinese News/Talk', 'Sa-Su 9p-12a Mandarin Entertainment',
    'M-F 4p-7p Filipino News/Talk', 'BNS M-Su Viet ROS'."""
    lang = _short_lang(ln.language or ln.program)
    if ln.is_bonus:
        return f"BNS {days} {lang} ROS"[:60]
    return f"{days} {label} {lang} {ln.program_short}".strip()[:60]


def order_plan(order: SnoqualmieOrder, start_from: date) -> list[tuple]:
    """(line, days, time_range, description, ranges, notes) for every airtime
    line — the single source of truth the gather preview and the entry both walk,
    so what Lee approves is what gets written."""
    plan: list[tuple] = []
    for ln in order.lines:
        if ln.total_spots == 0:
            continue
        days, time_range, label = line_window(ln)
        desc = line_description(ln, days, label)
        consolidated = EtereClient.consolidate_weeks(
            ln.week_spots,
            [WeekCol(d) for d in ln.week_dates],
            flight_end=fmt_mmddyyyy(order.end),
        )
        ranges, notes = plan_ranges(consolidated, days, start_from, order.end)
        plan.append((ln, days, time_range, desc, ranges, notes))
    return plan


def default_code_desc(order: SnoqualmieOrder, start: date, cust=None) -> tuple[str, str, str]:
    """Precedent 2422: code 'Snoqualmie 2602', description 'Snoqualmie Casino LNY
    2026', ref 'LNY 2026'. Returns (code, description, customer_ref)."""
    yymm = broadcast_yymm(start)
    prefix = (
        cust.code_name if cust and getattr(cust, "code_name", "") else ""
    ) or DEFAULT_CODE_PREFIX
    desc_prefix = (
        cust.description_name if cust and getattr(cust, "description_name", "") else ""
    ) or CLIENT_NAME
    campaign = order.campaign or order.estimate
    tail = f"{campaign} {start.year}".strip() if campaign else f"{start.year}"
    ref = order.estimate or (f"{campaign} {start.year}" if campaign else "")
    return f"{prefix} {yymm}", f"{desc_prefix} {tail}", ref


def default_line_note(order: SnoqualmieOrder) -> str:
    """The sheet's traffic notes, one line: 'ROTATE 3 SPOTS EVENLY; Sacred Riana
    should end 10/30; ...; English spot runs during Filipino programming'."""
    parts: list[str] = []
    for n in order.notes:
        t = n.strip().lstrip("*").strip()
        m = re.match(r"(?i)traffic information\s*\(?(.*?)\)?\s*$", t)
        if m:
            t = m.group(1).strip()
        if t:
            parts.append(t)
    return "; ".join(parts)[:399]


# ─── Customer DB ──────────────────────────────────────────────────────────────


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
    name: str, customer_id: int, separation: tuple, billing_type: str, market: str
) -> None:
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        from src.domain.enums import OrderType

        CustomerRepository(CUSTOMER_DB_PATH).save(
            Customer(
                customer_id=str(customer_id),
                customer_name=name,
                order_type=OrderType.SNOQUALMIE,
                billing_type=billing_type,
                code_name=DEFAULT_CODE_PREFIX,
                description_name=CLIENT_NAME,
                separation_customer=separation[0],
                separation_order=separation[1],
                separation_event=separation[2],
                default_market=market,
            )
        )
    except Exception as exc:
        print(f"[CUSTOMER] customer DB upsert failed (non-fatal): {exc}")


def _anagraf_lookup(name: str) -> Optional[dict]:
    """First ANAGRAF row whose name starts with the client's first word."""
    try:
        from browser_automation.etere_direct_client import connect

        conn = connect()
        cur = conn.cursor()
        first = (name or "").split()[0] if name else CLIENT_NAME
        cur.execute(
            "SELECT ID_ANAGRAF, RAG_SOCIAL, AGENZIA, Commissione, CENTROMEDIA FROM ANAGRAF "
            "WHERE RAG_SOCIAL LIKE %s ORDER BY ID_ANAGRAF",
            (f"{first}%",),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as exc:
        print(f"[CUSTOMER] ANAGRAF lookup failed: {exc}")
        return None
    if not rows:
        return None
    r = rows[0]
    return {
        "id": int(r[0]),
        "name": str(r[1]).strip(),
        "agency": int(r[2] or 0),
        "pct": float(r[3] or 0),
        "media_center": int(r[4] or 0),
    }


# ─── Gather ───────────────────────────────────────────────────────────────────


def _print_plan(plan: list[tuple]) -> tuple[int, list[str]]:
    entered = 0
    all_notes: list[str] = []
    for _ln, days, time_range, desc, ranges, notes in plan:
        for rng in ranges:
            entered += rng["spots_per_week"] * rng["weeks"]
            tag = f"   ← {rng['tag']}" if rng["tag"] else ""
            print(
                f"      {desc[:32]:<32} {days:<5} {time_range}  "
                f"{fmt_mmddyyyy(rng['date_from'])}–{fmt_mmddyyyy(rng['date_to'])}"
                f"  {rng['spots_per_week']}/wk×{rng['weeks']}w"
                f"  max {rng['max_daily']}/day{tag}"
            )
        all_notes.extend(f"{desc[:30]}: {n}" for n in notes)
    return entered, all_notes


def gather_snoqualmie_inputs(source_path: str) -> Optional[dict]:
    """Gather inputs for a Snoqualmie Casino proposal. Returns dict or None to abort."""
    order = parse_snoqualmie(source_path)

    print(f"\n{'=' * 64}")
    print(f"Client:     {order.client}   (direct — no agency, 0% commission)")
    print(f"Contact:    {order.contact}  {order.email}")
    print(f"Market:     {order.market_code or '?'}   ({order.market_label})")
    print(f"Flight:     {order.label}   ({len(order.week_dates)} weeks)   :{order.length_sec}s")
    if order.order_date:
        print(f"Revised:    {order.order_date:%B %d, %Y}")
    print(f"Billing:    {order.billing_cycle or '?'} (sheet)")
    print("Money:      PROMO rates entered verbatim (direct 0% ⇒ gross == net)")
    print("\n  Lines:")
    for ln in order.lines:
        days, _tr, label = line_window(ln)
        tag = "BNS" if ln.is_bonus else "   "
        rate = f"${ln.rate:.2f}" if ln.rate else "  bonus"
        print(
            f"    {tag} {line_description(ln, days, label):<34} {days:<5} {label:<10}"
            f" {rate:>8}  {ln.total_spots:>3} spots  {ln.week_spots}"
        )
    if order.charges:
        print("\n  Charges (→ ONE Production amount on the first paid line, not airtime):")
        for ch in order.charges:
            lst = f"  (list ${ch.list_amount:,.2f})" if ch.list_amount else ""
            print(f"    {ch.description:<40} x{ch.quantity}  ${ch.amount:>9,.2f}{lst}")
        print(f"    {'Production total':<40}      ${order.charges_total:>9,.2f}")
    if order.notes:
        print("\n  Sheet notes:")
        for n in order.notes:
            print(f"    {n}")
    print(
        f"\n  Order: {sum(ln.total_spots for ln in order.paid_lines)} paid"
        f" + {sum(ln.total_spots for ln in order.bonus_lines)} bonus spots"
        f"   ${order.net_airtime:,.2f} net airtime + ${order.charges_total:,.2f} production"
        f" = ${order.net_airtime + order.charges_total:,.2f}"
    )

    market_code = order.market_code or DEFAULT_MARKET
    raw = input(f"  Market [{market_code}]: ").strip().upper()
    market_code = raw or market_code
    if market_code not in _MARKETS:
        print("  ✗ Unknown market — aborting")
        return None

    # ── Customer ──
    resolved = _anagraf_lookup(order.client)
    default_id = str(resolved["id"]) if resolved else str(DEFAULT_CUSTOMER_ID)
    if resolved:
        print(
            f"\n[CUSTOMER] ANAGRAF '{resolved['name']}' → ID {resolved['id']}  "
            f"(agency {resolved['agency'] or 'none'}, {resolved['pct']:.0f}%, "
            f"CENTROMEDIA {resolved['media_center']})"
        )
        if resolved["agency"]:
            print("  ⚠ ANAGRAF links an agency to this client — the header will follow ANAGRAF.")
    raw_id = input(f"  Customer (ANAGRAF) ID [{default_id}]: ").strip()
    customer_id = raw_id or default_id
    if not customer_id.isdigit():
        print("  ✗ Invalid customer ID — aborting")
        return None
    customer_id = int(customer_id)
    cust_name = resolved["name"] if resolved else CLIENT_NAME

    # ── Separation / billing (customer DB, else the house default) ──
    separation = DEFAULT_SEPARATION
    billing_type = "direct"
    cust = _lookup_customer_db(cust_name) or _lookup_customer_db(CLIENT_NAME)
    if cust:
        billing_type = cust.billing_type or billing_type
        separation = (cust.separation_customer, cust.separation_order, cust.separation_event)
    else:
        print(
            f"\n  Separation default {DEFAULT_SEPARATION} (customer DB has no row; "
            f"2422's paid lines carried {PRECEDENT_SEPARATION})"
        )

    # Billing cycle: the sheet names it; ANAGRAF's default is shown, the operator decides.
    sheet_cycle = (order.billing_cycle or "Broadcast").strip().title()
    raw = input(f"  Billing cycle (Broadcast/Calendar) [{sheet_cycle}]: ").strip().title()
    cycle = raw or sheet_cycle
    if cycle.lower() not in MEDIA_CENTER:
        print("  ✗ Billing cycle must be Broadcast or Calendar — aborting")
        return None
    media_center_id = MEDIA_CENTER[cycle.lower()]

    # ── Start date (always asked — these orders enter late) + line plan ──
    start = confirm_start_date(fmt_mmddyyyy(order.start), fmt_mmddyyyy(order.end), always_ask=True)
    if start is None:
        print("  ✗ No flight start date — aborting")
        return None
    plan = order_plan(order, start)
    print(f"\n    Etere lines for a {fmt_mmddyyyy(start)} start:")
    entered, notes = _print_plan(plan)
    ordered = sum(ln.total_spots for ln in order.lines)
    if notes:
        print("\n    ⚠ The start date makes some spots undeliverable:")
        for n in notes:
            print(f"        {n}")
    print(
        f"    Spots: {entered} entered of {ordered} ordered"
        f"{'' if entered == ordered else '  ← SHORT'}"
    )
    if entered != ordered:
        raw = input("    Enter this order short anyway? [y/N]: ").strip().lower()
        if raw not in ("y", "yes"):
            print("  ✗ Aborted — pick an earlier start date or ask the client to revise.")
            return None

    default_code, default_desc, default_ref = default_code_desc(order, start, cust)
    raw = input(f"  Contract code [{default_code}]: ").strip()
    code = raw or default_code
    raw = input(f"  Description [{default_desc}]: ").strip()
    desc = raw or default_desc
    raw = input(f"  Customer order ref [{default_ref}] ('-' = none): ").strip()
    customer_ref = "" if raw == "-" else (raw or default_ref)

    default_note = default_line_note(order)
    note = ""
    if default_note:
        raw = input(f"  Line note [{default_note}] ('-' = none): ").strip()
        note = "" if raw == "-" else (raw or default_note)

    _upsert_customer_db(cust_name, customer_id, separation, billing_type, market_code)

    return {
        "customer_id": customer_id,
        "billing_type": billing_type,
        "media_center_id": media_center_id,
        "separation": separation,
        "market": market_code,
        "contract_code": code,
        "description": desc,
        "customer_ref": customer_ref,
        "start_date_override": fmt_mmddyyyy(start),
        "note": note[:399],
    }


# ─── Direct DB entry ──────────────────────────────────────────────────────────


def _create_snoqualmie_contract(
    order: SnoqualmieOrder, inputs: dict, dry_run: bool = False
) -> Optional[str]:
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = inputs.get("customer_id")
    market_code = inputs.get("market") or order.market_code or DEFAULT_MARKET
    if customer_id is None or not market_code:
        print(f"{_LABEL} ✗ No customer_id / market")
        return None
    separation = tuple(inputs.get("separation") or DEFAULT_SEPARATION)
    billing_type = inputs.get("billing_type", "direct")
    media_center_id = int(inputs.get("media_center_id") or MEDIA_CENTER["broadcast"])
    code = inputs["contract_code"]
    description = inputs["description"]
    customer_ref = inputs.get("customer_ref", "")
    note = inputs.get("note", "")
    start = (
        parse_date(inputs["start_date_override"])
        if inputs.get("start_date_override")
        else order.start
    )
    end = order.end
    production_total = order.charges_total

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=code,
            description=description,
            customer_id=int(customer_id),
            agency_id=None,  # direct — ANAGRAF (lookup) is authoritative
            lookup_customer_defaults=True,
            contract_date=start,
            contract_end_date=end,
            contract_type=1,
            billing_type=billing_type,
            media_center_id=media_center_id,
            allow_rename=True,
            customer_order_ref=customer_ref,
        )
        print(
            f"{_LABEL} ✓ Contract header: ID={contract_id}  code='{code}'  "
            f"{fmt_mmddyyyy(start)}–{fmt_mmddyyyy(end)}  ref={customer_ref!r}"
        )

        production_pending = round(production_total, 2)
        line_count = 0
        for ln, days, time_range, desc, ranges, notes in order_plan(order, start):
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
                    verify_production_charge(conn.cursor(), line_id, prod, label="Snoqualmie")
                    print(f"           ↳ Production ${prod:,.2f} (→ CONTRATTISPESE 'Production')")

        if production_pending:
            raise RuntimeError(
                f"Snoqualmie: ${production_pending:,.2f} production was never "
                "attached — no paid line. Rolling back."
            )

        # Read back the header money basis before committing.
        cur = conn.cursor()
        cur.execute(
            "SELECT P_AGENZIA, AGENZIA, CENTROMEDIA, CUSTOMERREF FROM CONTRATTITESTATA "
            "WHERE ID_CONTRATTITESTATA = %s",
            (int(contract_id),),
        )
        row = cur.fetchone()
        if row is None or abs(float(row[0] or 0)) > 0.001 or int(row[1] or 0) != 0:
            raise RuntimeError(
                f"Snoqualmie: header readback P_AGENZIA={row and row[0]} "
                f"AGENZIA={row and row[1]} — expected 0 / none (direct); rolling back"
            )
        if int(row[2] or 0) != media_center_id:
            raise RuntimeError(
                f"Snoqualmie: header CENTROMEDIA={row[2]} but {media_center_id} was "
                "chosen at gather; rolling back"
            )
        if dry_run:
            conn.rollback()
            conn.close()
            print(
                f"{_LABEL} DRY RUN: {line_count} lines in txn, CENTROMEDIA={row[2]}, "
                f"ref={row[3]!r}, production ${production_total:,.2f} — ROLLED BACK"
            )
            return code
        conn.commit()
        conn.close()
        print(f"{_LABEL} ✓ {line_count} lines committed on {code} (CENTROMEDIA {row[2]}).")
        return code

    except Exception as exc:
        print(f"{_LABEL} ✗ {exc}")
        import traceback

        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


def run_snoqualmie_order(order: SnoqualmieOrder, inputs: dict) -> list[tuple[str, bool]]:
    """One contract per proposal. Returns [(code, success)]."""
    code = _create_snoqualmie_contract(order, inputs)
    return [(inputs.get("contract_code") or code or "", code is not None)]


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Snoqualmie proposal → Etere (direct DB)")
    ap.add_argument("pdf")
    ap.add_argument("--dry-run", action="store_true", help="write in a transaction, roll back")
    ap.add_argument("--start", help="start date M/D/YYYY (default: the sheet's flight start)")
    ap.add_argument("--separation", default="15,0,0")
    a = ap.parse_args()
    o = parse_snoqualmie(a.pdf)
    st = parse_date(a.start) if a.start else o.start
    if a.dry_run:
        c, d, r = default_code_desc(o, st)
        inputs = {
            "customer_id": DEFAULT_CUSTOMER_ID,
            "billing_type": "direct",
            "media_center_id": MEDIA_CENTER[(o.billing_cycle or "broadcast").lower()],
            "separation": tuple(int(x) for x in a.separation.split(",")),
            "market": o.market_code or DEFAULT_MARKET,
            "contract_code": c,
            "description": d,
            "customer_ref": r,
            "start_date_override": fmt_mmddyyyy(st),
            "note": default_line_note(o),
        }
        _create_snoqualmie_contract(o, inputs, dry_run=True)
    else:
        inp = gather_snoqualmie_inputs(a.pdf)
        if inp:
            print(run_snoqualmie_order(o, inp))
