"""
Illinois Lottery (Flowers Communications Group) — direct-DB entry for the house
"Crossings TV Proposal" PDF.

Conventions (Lee 2026-10-01, oracles 1541 `Flowers ILLot 2410` and 2151 `Flowers ILLot 2510`):
  * customer Illinois Lottery = ANAGRAF 354, linked to agency Flowers 353 at 15%; the
    client's ANAGRAF row decides the agency + commission, AGENCY_IDS["FLOWERS"] is the
    fallback; rates are GROSS (the Discounted Rate) and enter verbatim - the header
    commission nets them; the sheet's Net must agree with that commission;
  * market CMP (Chicago-Twin Cities), confirmed at gather; :15 from the sheet;
  * code `Flowers ILLot <yymm>`, description `Illinois Lottery <yymm>-<yymm>` (broadcast
    months of the first and last week), Customer Order ref blank;
  * line names = days + time + the sheet row with the house short block names
    (Cantonese & Mandarin -> Chinese); a dual window ("4p-5p, 6p-7p") enters as ONE
    line on the union (4p-7p); bonus rows = `BNS <language> ROS` on the house ROS
    windows (ROS_SCHEDULES), $0, booking code 10;
  * EVERY line is Rotation (both prior contracts), week ranges consolidated
    (consolidate_weeks) and the start date always asked (shared line_planner).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
from browser_automation.etere_client import EtereClient
from browser_automation.etere_direct_client import AGENCY_IDS
from browser_automation.line_planner import (
    WeekCol,
    broadcast_yymm,
    confirm_start_date,
    fmt_mmddyyyy,
    parse_date,
    plan_ranges,
)
from browser_automation.parsers.hpsj_parser import base_language, daypart_union
from browser_automation.parsers.illottery_parser import (
    ADVERTISER_NAME,
    ILLotteryLine,
    ILLotteryOrder,
    parse_illottery,
)
from browser_automation.ros_definitions import ROS_SCHEDULES

DEFAULT_CUSTOMER_ID = 354
DEFAULT_MARKET = "CMP"
DEFAULT_CODE_PREFIX = "Flowers ILLot"
DEFAULT_SEPARATION = (15, 0, 0)
DEFAULT_AGENCY_FEE = 15.0
ROTATION = 1
_MARKETS = ("CVC", "SFO", "LAX", "SEA", "HOU", "CMP", "WDC", "NYC", "MMT", "DAL")
_LABEL = "[ILLOT]"

# House short forms for the sheet's block names (Lee 10/1).
_BLOCK_SHORT = [
    (re.compile(r"cantonese\s*&\s*mandarin", re.I), "Chinese"),
    (re.compile(r"mandarin\s*&\s*cantonese", re.I), "Chinese"),
]


def short_block(block: str) -> str:
    """'Cantonese & Mandarin News & Drama' -> 'Chinese News & Drama';
    'Korean News &Entertainment' -> 'Korean News & Entertainment';
    'Filipino News /Talk' -> 'Filipino News/Talk'."""
    out = " ".join(block.split())
    for rx, short in _BLOCK_SHORT:
        out = rx.sub(short, out)
    out = re.sub(r"\s*&\s*", " & ", out)
    out = re.sub(r"\s*/\s*", "/", out)
    return out.strip()


def line_description(ln: ILLotteryLine) -> str:
    if ln.is_bonus:
        return f"BNS {base_language(ln.block)} ROS"
    days, time = daypart_union(ln.daypart)
    t_from, t_to = EtereClient.parse_time_range(time)
    return f"{days} {_short_time(t_from)}-{_short_time(t_to)} {short_block(ln.block)}"[:60]


def _short_time(hhmm: str) -> str:
    """'19:00' -> '7p', '23:59' -> '12a', '08:00' -> '8a', '13:30' -> '1:30p'."""
    h, m = (int(x) for x in hhmm.split(":"))
    if hhmm == "23:59":
        return "12a"
    suffix = "a" if h < 12 else "p"
    h12 = h % 12 or 12
    return f"{h12}{suffix}" if m == 0 else f"{h12}:{m:02d}{suffix}"


# ─── Planner (single source of truth for preview AND writer) ─────────────────


def line_plan(order: ILLotteryOrder, start_from: date) -> list[dict]:
    """Sheet row -> consolidated week ranges -> Etere lines (late-start split via
    plan_ranges). Bonus rows take the house ROS window for their language."""
    flight_end = order.flight_end
    week_cols = [WeekCol(d) for d in order.week_dates]
    plan: list[dict] = []
    for ln in order.lines:
        if ln.total_spots == 0:
            continue
        if ln.is_bonus:
            lang = base_language(ln.block)
            ros = ROS_SCHEDULES.get(lang)
            if not ros:
                raise ValueError(f"{ln.block}: no house ROS window for {lang!r}")
            days, time = ros["days"], ros["time"]
        else:
            days, time = daypart_union(ln.daypart)
        days, _ = EtereClient.check_sunday_6_7a_rule(days, time)
        t_from, t_to = EtereClient.parse_time_range(time)
        consolidated = EtereClient.consolidate_weeks(
            ln.week_spots, week_cols, fmt_mmddyyyy(flight_end), fmt_mmddyyyy(order.flight_start)
        )
        ranges, notes = plan_ranges(consolidated, days, start_from, flight_end)
        desc = line_description(ln)
        for rng in ranges:
            plan.append(
                {
                    "line": ln,
                    "days": days,
                    "time_range": f"{t_from}-{t_to}",
                    "description": desc,
                    "date_from": rng["date_from"],
                    "date_to": rng["date_to"],
                    "spots_per_week": rng["spots_per_week"],
                    "weeks": rng["weeks"],
                    "total_spots": rng["spots_per_week"] * rng["weeks"],
                    "max_daily": rng["max_daily"],
                    "tag": rng["tag"],
                    "notes": notes,
                    "is_bonus": ln.is_bonus,
                    "rate": 0.0 if ln.is_bonus else ln.rate,
                    "length_sec": order.length_sec,
                }
            )
    return plan


def print_plan(plan: list[dict]) -> tuple[int, int]:
    seen: set[str] = set()
    for p in plan:
        for n in p["notes"]:
            if n not in seen:
                seen.add(n)
                print(f"    [NOTE] {p['description']}: {n}")
        rate = f"${p['rate']:>6.2f}" if p["rate"] else " bonus"
        tag = f"  <- {p['tag']}" if p["tag"] else ""
        print(
            f"    {p['description']:<42} {p['time_range']}  "
            f"{fmt_mmddyyyy(p['date_from'])}-{fmt_mmddyyyy(p['date_to'])}  "
            f"{p['spots_per_week']:>2}/wk x {p['weeks']:>2}w = {p['total_spots']:>3}  "
            f"max {p['max_daily']}/day  {rate}{tag}"
        )
    entered = sum(p["total_spots"] for p in plan)
    gross = sum(p["rate"] * p["total_spots"] for p in plan)
    print(f"    {'':<42} {len(plan)} lines, {entered} spots, gross ${gross:,.2f}")
    return entered, len(plan)


# ─── customers.db / ANAGRAF ───────────────────────────────────────────────────


def _lookup_customer_db(name: str):
    try:
        import os

        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.enums import OrderType

        if not os.path.exists(CUSTOMER_DB_PATH):
            return None
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        return repo.find_by_name(name, OrderType.ILLOTTERY) or repo.find_by_name_any_type(name)
    except Exception:
        return None


def _upsert_customer_db(
    name, customer_id, code_name, description_name, separation, billing_type, market
):
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        from src.domain.enums import OrderType

        CustomerRepository(CUSTOMER_DB_PATH).save(
            Customer(
                customer_id=str(customer_id),
                customer_name=name,
                order_type=OrderType.ILLOTTERY,
                billing_type=billing_type,
                code_name=code_name,
                description_name=description_name,
                separation_customer=separation[0],
                separation_event=separation[1],
                separation_order=separation[2],
                default_market=market,
            )
        )
    except Exception as exc:
        print(f"[CUSTOMER] customers.db upsert failed (non-fatal): {exc}")


def _anagraf_defaults(customer_id: int) -> dict:
    """{'name', 'agency_id', 'agency_name', 'agency_pct'} from ANAGRAF, or {}."""
    try:
        from browser_automation.etere_direct_client import connect

        conn = connect()
        cur = conn.cursor()
        cur.execute(
            """SELECT c.RAG_SOCIAL, c.AGENZIA, a.RAG_SOCIAL, ISNULL(a.Commissione, 0)
               FROM ANAGRAF c LEFT JOIN ANAGRAF a ON a.ID_ANAGRAF = c.AGENZIA
               WHERE c.ID_ANAGRAF = %s""",
            (int(customer_id),),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return {}
        return {
            "name": str(row[0] or "").strip(),
            "agency_id": int(row[1] or 0),
            "agency_name": str(row[2] or "").strip(),
            "agency_pct": float(row[3] or 0.0),
        }
    except Exception as exc:
        print(f"[CUSTOMER] ANAGRAF lookup failed: {exc}")
        return {}


# ─── Gather ───────────────────────────────────────────────────────────────────


def default_code(code_name: str, start: date) -> str:
    return f"{code_name} {broadcast_yymm(start)}".strip()


def default_description(description_name: str, start: date, end: date) -> str:
    return f"{description_name} {broadcast_yymm(start)}-{broadcast_yymm(end)}".strip()


def gather_illottery_inputs(source_path: str) -> Optional[dict]:
    """Gather inputs for an Illinois Lottery proposal. Returns dict or None to abort."""
    order = parse_illottery(source_path)

    print(f"\n{'=' * 64}")
    print(f"Advertiser: {order.advertiser}   via {order.agency}   (contact {order.contact})")
    print(f"Market:     {order.market_name} -> {order.market_code or '?'}   :{order.length_sec}")
    print(
        f"Flight:     {fmt_mmddyyyy(order.flight_start)} -> {fmt_mmddyyyy(order.flight_end)}  "
        f"({len(order.week_dates)} weeks)   {order.flight_estimate}"
    )
    print(
        f"Money:      GROSS rates (Discounted Rate) enter verbatim; sheet Net ${order.net_stated:,.2f} "
        f"= gross ${order.gross_stated:,.2f} less {order.implied_commission:g}%"
    )
    if order.holiday:
        print(f"Holiday:    {order.holiday}")
    print("\n  Sheet rows:")
    for ln in order.lines:
        tag = "BNS" if ln.is_bonus else "   "
        rate = f"${ln.rate:.2f}" if not ln.is_bonus else "  bonus"
        print(
            f"    {tag} {line_description(ln):<42} {rate:>8}  {ln.total_spots:>4} spots  ${ln.proposed:,.2f}"
        )
    print(
        f"\n  Spots: {sum(x.total_spots for x in order.paid_lines)} paid + "
        f"{sum(x.total_spots for x in order.bonus_lines)} bonus = {order.total_spots}   gross ${order.gross_total:,.2f}"
    )

    raw = input(f"  Market [{order.market_code or DEFAULT_MARKET}]: ").strip().upper()
    market_code = (order.market_code or DEFAULT_MARKET) if not raw or raw in ("Y", "YES") else raw
    if market_code not in _MARKETS:
        print("  ✗ Unknown market — aborting")
        return None

    start_override = confirm_start_date(
        fmt_mmddyyyy(order.flight_start), fmt_mmddyyyy(order.flight_end), always_ask=True
    )
    if start_override is None:
        print("  ✗ No flight start date — aborting")
        return None

    print(f"\n  Etere lines for a {fmt_mmddyyyy(start_override)} start (every line Rotation):")
    plan = line_plan(order, start_override)
    entered, _ = print_plan(plan)
    ordered = order.total_spots
    print(
        f"\n  Spots: {entered} entered of {ordered} ordered{'' if entered == ordered else '  <- SHORT'}"
    )
    if entered != ordered:
        raw = input("  Enter the order short anyway? [y/N]: ").strip().lower()
        if raw not in ("y", "yes"):
            print("  ✗ Aborted — pick an earlier start date or ask the client to revise.")
            return None

    from browser_automation.customer_defaults import prompt_customer_id

    cust = _lookup_customer_db(order.advertiser)
    default_id = str(cust.customer_id) if cust and cust.customer_id else str(DEFAULT_CUSTOMER_ID)
    raw_id = prompt_customer_id(default_id)
    if raw_id is None:
        print("  ✗ No customer ID — aborting")
        return None
    customer_id = int(raw_id)

    anag = _anagraf_defaults(customer_id)
    cust_name = anag.get("name") or (cust.customer_name if cust else "") or order.advertiser
    agency_id = anag.get("agency_id") or AGENCY_IDS["FLOWERS"]
    fee_pct = anag.get("agency_pct") if anag.get("agency_id") else DEFAULT_AGENCY_FEE
    print(
        f"  [CUSTOMER] ANAGRAF {customer_id} = {cust_name}   agency {agency_id} "
        f"{anag.get('agency_name') or order.agency} @ {fee_pct:g}%"
    )
    if abs(fee_pct - order.implied_commission) > 0.05:
        print(
            f"  ⚠ The sheet's Net implies {order.implied_commission:g}% but ANAGRAF nets at {fee_pct:g}% — "
            "the contract will bill at the ANAGRAF rate."
        )
        raw = input("  Continue? [y/N]: ").strip().lower()
        if raw not in ("y", "yes"):
            return None

    separation = DEFAULT_SEPARATION
    billing_type = "agency"
    if cust:
        billing_type = cust.billing_type or billing_type
        separation = (cust.separation_customer, cust.separation_event, cust.separation_order)
    code_name = (
        cust.code_name if cust and getattr(cust, "code_name", "") else ""
    ) or DEFAULT_CODE_PREFIX
    description_name = (
        cust.description_name if cust and getattr(cust, "description_name", "") else ""
    ) or cust_name

    d_code = default_code(code_name, start_override)
    d_desc = default_description(description_name, start_override, order.flight_end)
    print()
    raw = input(f"  Contract code [{d_code}]: ").strip()
    contract_code = raw or d_code
    raw = input(f"  Description [{d_desc}]: ").strip()
    description = raw or d_desc
    raw = input("  Customer Order ref [blank]: ").strip()
    customer_ref = raw

    _upsert_customer_db(
        order.advertiser,
        customer_id,
        code_name=code_name,
        description_name=description_name,
        separation=separation,
        billing_type=billing_type,
        market=market_code,
    )

    return {
        "customer_id": customer_id,
        "agency_id": agency_id,
        "agency_pct": fee_pct,
        "billing_type": billing_type,
        "separation": separation,
        "contract_code": contract_code,
        "description": description,
        "customer_ref": customer_ref,
        "start_date_override": fmt_mmddyyyy(start_override),
        "market": market_code,
    }


# ─── Direct DB entry ──────────────────────────────────────────────────────────


def _create_illottery_contract(
    order: ILLotteryOrder, inputs: dict, dry_run: bool = False
) -> Optional[str]:
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = inputs.get("customer_id")
    if customer_id is None:
        print(f"{_LABEL} ✗ No customer_id")
        return None
    separation = tuple(inputs.get("separation", DEFAULT_SEPARATION))
    billing_type = inputs.get("billing_type", "agency")
    contract_code = inputs.get("contract_code") or DEFAULT_CODE_PREFIX
    description = inputs.get("description") or ADVERTISER_NAME
    market_code = inputs.get("market") or order.market_code or DEFAULT_MARKET
    customer_ref = inputs.get("customer_ref") or ""

    override = inputs.get("start_date_override")
    flight_start_d = parse_date(override) if override else order.flight_start
    flight_end_d = order.flight_end

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=contract_code,
            description=description,
            customer_id=int(customer_id),
            agency_id=AGENCY_IDS["FLOWERS"],  # fallback; ANAGRAF (lookup) decides agency + %
            lookup_customer_defaults=True,
            contract_date=flight_start_d,
            contract_end_date=flight_end_d,
            contract_type=1,
            billing_type=billing_type,
            customer_order_ref=customer_ref,
            allow_rename=True,
        )
        print(f"{_LABEL} ✓ Contract header: ID={contract_id}  code='{contract_code}'")

        cur = conn.cursor()
        cur.execute(
            "SELECT P_AGENZIA, AGENZIA, CUSTOMERREF FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = %s",
            (contract_id,),
        )
        p_ag, ag, ref = cur.fetchone()
        if order.implied_commission and abs(float(p_ag or 0) - order.implied_commission) > 0.05:
            print(
                f"{_LABEL} ⚠ header commission {p_ag}% vs the sheet's implied {order.implied_commission}% "
                "(ANAGRAF wins; the gather confirmed this)"
            )

        plan = line_plan(order, flight_start_d)
        for i, p in enumerate(plan, 1):
            tag = f"  <- {p['tag']}" if p["tag"] else ""
            print(
                f"  [LINE {i}] {market_code} {p['description']}: "
                f"{fmt_mmddyyyy(p['date_from'])}-{fmt_mmddyyyy(p['date_to'])} "
                f"({p['spots_per_week']}/wk x {p['weeks']}w = {p['total_spots']}) :{p['length_sec']}s "
                f"rate={p['rate']} max {p['max_daily']}/day{tag}"
            )
            client.add_contract_line(
                market=market_code,
                days=p["days"],
                time_range=p["time_range"],
                description=p["description"],
                rate=p["rate"],
                total_spots=p["total_spots"],
                spots_per_week=p["spots_per_week"],
                max_daily_run=p["max_daily"],
                date_from=p["date_from"],
                date_to=p["date_to"],
                duration=str(p["length_sec"]),
                is_bonus=p["is_bonus"],
                booking_code=10 if p["is_bonus"] else 2,
                scheduling_type=ROTATION,
                separation_intervals=separation,
            )

        # Read back what was written where the user reads it.
        cur.execute(
            "SELECT ID_CONTRATTIRIGHE, IMPORTO, N_PASSAGGI, PRENOTAZIONE, ID_BOOKINGCODE, DURATA "
            "FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA = %s ORDER BY ID_CONTRATTIRIGHE",
            (contract_id,),
        )
        rows = cur.fetchall()
        if len(rows) != len(plan):
            raise RuntimeError(f"{len(rows)} lines in Etere vs {len(plan)} planned — rolling back")
        for p, row in zip(plan, rows):
            if abs(float(row[1] or 0) - p["rate"]) > 0.005 or int(row[2] or 0) != p["total_spots"]:
                raise RuntimeError(
                    f"line {row[0]} readback rate={row[1]} spots={row[2]} vs planned {p['rate']}/{p['total_spots']} — rolling back"
                )
            if int(row[3] or 0) != ROTATION:
                raise RuntimeError(
                    f"line {row[0]} PRENOTAZIONE={row[3]}, expected Rotation — rolling back"
                )
        spots = sum(int(r[2] or 0) for r in rows)
        gross = sum(float(r[1] or 0) * int(r[2] or 0) for r in rows)
        print(
            f"{_LABEL} readback OK: {len(rows)} lines, {spots} spots, gross ${gross:,.2f} "
            f"(sheet ${order.gross_total:,.2f}), commission {p_ag}%, ref {ref!r}"
        )

        if dry_run:
            conn.rollback()
            conn.close()
            print(f"{_LABEL} DRY RUN: {len(rows)} lines in txn — ROLLED BACK")
            return contract_code

        conn.commit()
        conn.close()
        print(f"{_LABEL} ✓ {len(rows)} lines committed.")
        return contract_code

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


def run_illottery_order(order: ILLotteryOrder, inputs: dict) -> list[tuple[str, bool]]:
    """One contract per proposal. Returns [(contract_code, success)]."""
    code = _create_illottery_contract(order, inputs)
    label = inputs.get("contract_code") or DEFAULT_CODE_PREFIX
    return [(label, code is not None)]


if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(
        description="Illinois Lottery proposal -> Etere (dry run rolls back)"
    )
    ap.add_argument("pdf")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--start", help="start date m/d/yyyy (default: the first week)")
    a = ap.parse_args()
    o = parse_illottery(a.pdf)
    start = parse_date(a.start) if a.start else o.flight_start
    print(f"\n  Etere lines for a {fmt_mmddyyyy(start)} start:")
    print_plan(line_plan(o, start))
    if a.dry_run:
        inputs = {
            "customer_id": DEFAULT_CUSTOMER_ID,
            "agency_id": AGENCY_IDS["FLOWERS"],
            "agency_pct": DEFAULT_AGENCY_FEE,
            "billing_type": "agency",
            "separation": DEFAULT_SEPARATION,
            "contract_code": f"{default_code(DEFAULT_CODE_PREFIX, start)} DRYRUN",
            "description": default_description(ADVERTISER_NAME, start, o.flight_end),
            "customer_ref": "",
            "start_date_override": fmt_mmddyyyy(start),
            "market": o.market_code or DEFAULT_MARKET,
        }
        _create_illottery_contract(o, inputs, dry_run=True)
