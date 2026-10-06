"""
Health Plan of San Joaquin (HPSJ) — direct-DB entry for the house Media Proposal PDF.

Conventions (Lee 2026-09-30, oracle contract 2376 `HPSJ 2601`):
  * customer ANAGRAF 414, DIRECT, 0% — rates enter verbatim (Promo Unit Cost);
  * market CVC; Calendar billing (CENTROMEDIA 317) like January's contract;
  * code `HPSJ <yymm>`, description `Health Plan of San Joaquin <title> <yymm>-<yymm>`,
    Customer Order ref = the proposal title (DSNP);
  * a dual daypart enters as ONE line on the union of its days and times
    ("M-SUN 10A-11A & 12P-1P" → M-Su 10a-1p; "M-F 4p-7pm/ Sat-Sun 4p-6p" → M-Su 4p-7p);
  * bonus rows are ROS in their language block (ROS_SCHEDULES), booking code 10;
  * the Translation Cost row is production money → Production box on the first
    paid line (CONTRATTISPESE), verified in-transaction, never a line;
  * the start date is always asked (shared line_planner); the 10/26 hiatus week
    splits ranges through consolidate_weeks.
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
from browser_automation.parsers.hpsj_parser import (
    CLIENT_NAME,
    HPSJOrder,
    daypart_union,
    parse_hpsj,
)
from browser_automation.ros_definitions import ROS_SCHEDULES

DEFAULT_CUSTOMER_ID = 414
DEFAULT_MARKET = "CVC"
DEFAULT_CODE_PREFIX = "HPSJ"
DEFAULT_DESCRIPTION_PREFIX = CLIENT_NAME
MEDIA_CENTER_CALENDAR = 317  # CONTRATTITESTATA.CENTROMEDIA: 316 Broadcast / 317 Calendar
_MARKETS = ("CVC", "SFO", "LAX", "SEA", "HOU", "CMP", "WDC", "NYC", "MMT")
_LABEL = "[HPSJ]"


# House short forms on hand-entered lines (Lee 9/30: "short form makes it easier";
# `Viet` is the only abbreviation the CVC contracts actually use — 2376 `HPSJ 2601`).
_SHORT = {"Vietnamese": "Viet"}


def _pretty(text: str) -> str:
    """'FILIPINO' → 'Filipino', 'SOUTH ASIAN' → 'South Asian', 'Vietnamese News/Talk'
    → 'Viet News/Talk'."""
    out = text.title() if text.isupper() else text
    for long, short in _SHORT.items():
        out = re.sub(rf"\b{long}\b", short, out)
    return out


def _line_plan(order: HPSJOrder, start_from: date, flight_end_str: str) -> list[tuple]:
    """(line, days, time_range, description, ranges, notes) per airtime line."""
    plan: list[tuple] = []
    for ln in order.lines:
        if ln.total_spots == 0:
            continue
        if ln.is_bonus:
            ros = ROS_SCHEDULES.get(ln.base_language)
            if ros:
                days, time_raw = ros["days"], ros["time"]
            else:
                days, time_raw = "M-Su", "6a-11:59p"
                print(f"  [WARN] No ROS window for '{ln.base_language}' — using {days} {time_raw}")
            desc = f"BNS {_pretty(ln.base_language)} ROS"
        else:
            days, time_raw = daypart_union(ln.daypart)
            days, _ = EtereClient.check_sunday_6_7a_rule(days, time_raw)
        time_from, time_to = EtereClient.parse_time_range(time_raw)
        if not ln.is_bonus:
            desc = (
                f"{days} {_short_time(time_from)}-{_short_time(time_to)} {_pretty(ln.insertion)}"[
                    :60
                ]
            )
        line_end = parse_date(flight_end_str)
        consolidated = EtereClient.consolidate_weeks(
            ln.week_spots,
            [WeekCol(d) for d in ln.week_dates],
            flight_end=fmt_mmddyyyy(line_end),
        )
        ranges, notes = plan_ranges(consolidated, days, start_from, line_end)
        plan.append((ln, days, f"{time_from}-{time_to}", desc, ranges, notes))
    return plan


def _short_time(hhmm: str) -> str:
    """'11:00' → '11a', '13:00' → '1p', '23:59' → '12a', '16:30' → '4:30p'."""
    h, m = (int(x) for x in hhmm.split(":"))
    if hhmm == "23:59":
        return "12a"
    suffix = "a" if h < 12 else "p"
    h12 = h % 12 or 12
    return f"{h12}{suffix}" if m == 0 else f"{h12}:{m:02d}{suffix}"


def _print_plan(order: HPSJOrder, start: date) -> tuple[int, int, list[str]]:
    entered = 0
    all_notes: list[str] = []
    for _ln, days, time_range, desc, ranges, notes in _line_plan(order, start, order.flight_end):
        for rng in ranges:
            entered += rng["spots_per_week"] * rng["weeks"]
            tag = f"   ← {rng['tag']}" if rng["tag"] else ""
            print(
                f"    {desc[:36]:<36} {days:<6} {time_range}  "
                f"{fmt_mmddyyyy(rng['date_from'])}–{fmt_mmddyyyy(rng['date_to'])}"
                f"  {rng['spots_per_week']}/wk×{rng['weeks']}w  max {rng['max_daily']}/day{tag}"
            )
        all_notes.extend(f"{desc[:36]}: {n}" for n in notes)
    ordered = sum(ln.total_spots for ln in order.lines)
    return entered, ordered, all_notes


# ─── customers.db ─────────────────────────────────────────────────────────────


def _lookup_customer_db(customer_id: int):
    try:
        import os

        from src.data_access.repositories.customer_repository import CustomerRepository

        if not os.path.exists(CUSTOMER_DB_PATH):
            return None
        for c in CustomerRepository(CUSTOMER_DB_PATH).list_all():
            if str(c.customer_id) == str(customer_id):
                return c
        return None
    except Exception:
        return None


def _upsert_customer_db(
    name, customer_id, code_name, description_name, separation, billing_type
) -> None:
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        from src.domain.enums import OrderType

        CustomerRepository(CUSTOMER_DB_PATH).save(
            Customer(
                customer_id=str(customer_id),
                customer_name=name,
                order_type=OrderType.HPSJ,
                billing_type=billing_type,
                code_name=code_name,
                description_name=description_name,
                separation_customer=separation[0],
                separation_order=separation[1],
                separation_event=separation[2],
                default_market=DEFAULT_MARKET,
            )
        )
    except Exception as exc:
        print(f"[CUSTOMER] customers.db upsert failed (non-fatal): {exc}")


def _anagraf_name(customer_id: int) -> str:
    try:
        from browser_automation.etere_direct_client import connect

        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT RAG_SOCIAL FROM ANAGRAF WHERE ID_ANAGRAF = %s", (int(customer_id),))
        row = cur.fetchone()
        conn.close()
        return str(row[0]).strip() if row else ""
    except Exception as exc:
        print(f"[CUSTOMER] ANAGRAF lookup failed: {exc}")
        return ""


# ─── Gather ───────────────────────────────────────────────────────────────────


def default_description(order: HPSJOrder, start: date) -> str:
    end = parse_date(order.flight_end)
    return f"{DEFAULT_DESCRIPTION_PREFIX} {order.title} {broadcast_yymm(start)}-{broadcast_yymm(end)}".strip()


def gather_hpsj_inputs(source_path: str) -> Optional[dict]:
    """Gather inputs for a Health Plan of San Joaquin order. Returns dict or None to abort."""
    order = parse_hpsj(source_path)

    print(f"\n{'=' * 64}")
    print(f"Proposal:   {order.title}   (dated {order.proposal_date})")
    print(f"Client:     {order.client}   (contact {order.contact})")
    print(
        f"Flight:     {order.flight_start} → {order.flight_end}  ({len(order.week_dates)} weeks: "
        f"{', '.join(f'{d.month}/{d.day}' for d in order.week_dates)})"
    )
    print(
        "Money:      DIRECT customer, 0% commission — Promo Unit Cost enters verbatim as the rate"
    )
    print("\n  Lines:")
    for ln in order.lines:
        tag = "BNS" if ln.is_bonus else "   "
        if ln.is_bonus:
            ros = ROS_SCHEDULES.get(ln.base_language, {})
            days, time = ros.get("days", "M-Su"), ros.get("time", "ROS")
        else:
            days, time = daypart_union(ln.daypart)
        rate = f"${ln.rate:.2f}" if ln.rate else "  bonus"
        print(
            f"    {tag} :{ln.length_sec}s {_pretty(ln.insertion)[:26]:<26} {days:<6} {time:<22} {rate:>8}  {ln.total_spots} spots"
        )
    print(
        f"\n  Airtime: {sum(ln.total_spots for ln in order.paid_lines)} paid + "
        f"{sum(ln.total_spots for ln in order.bonus_lines)} bonus spots   ${order.paid_total:,.2f}"
    )
    if order.charges:
        print("\n  Production (→ Production box on the first paid line, not airtime):")
        for ch in order.charges:
            print(
                f"    {ch.description:<30} ${ch.amount:>10,.2f}   (retail value ${ch.value:,.2f})"
            )
    print(f"  Contract total: ${order.total_cost:,.2f}")

    raw = input(f"  Market [{DEFAULT_MARKET}]: ").strip().upper()
    market_code = DEFAULT_MARKET if not raw or raw in ("Y", "YES") else raw
    if market_code not in _MARKETS:
        print("  ✗ Unknown market — aborting")
        return None

    start_override = confirm_start_date(order.flight_start, order.flight_end, always_ask=True)
    if start_override is None:
        print("  ✗ No flight start date — aborting")
        return None

    print(f"\n  Etere lines for a {fmt_mmddyyyy(start_override)} start:")
    entered, ordered, notes = _print_plan(order, start_override)
    if notes:
        print("\n  ⚠ The start date makes some spots undeliverable:")
        for n in notes:
            print(f"      {n}")
    print(
        f"\n  Spots: {entered} entered of {ordered} ordered{'' if entered == ordered else '  ← SHORT'}"
    )
    if entered != ordered:
        raw = input("  Enter the order short anyway? [y/N]: ").strip().lower()
        if raw not in ("y", "yes"):
            print("  ✗ Aborted — pick an earlier start date or ask the client to revise.")
            return None

    from browser_automation.customer_defaults import prompt_customer_id

    raw_id = prompt_customer_id(str(DEFAULT_CUSTOMER_ID))
    if raw_id is None:
        print("  ✗ No customer ID — aborting")
        return None
    customer_id = int(raw_id)
    cust_name = _anagraf_name(customer_id) or CLIENT_NAME
    print(f"  [CUSTOMER] ANAGRAF {customer_id} = {cust_name}")

    separation = (15, 0, 0)
    billing_type = "direct"
    cust = _lookup_customer_db(customer_id)
    if cust:
        billing_type = cust.billing_type or billing_type
        separation = (cust.separation_customer, cust.separation_order, cust.separation_event)

    code_prefix = (
        cust.code_name if cust and getattr(cust, "code_name", "") else ""
    ) or DEFAULT_CODE_PREFIX
    default_code = f"{code_prefix} {broadcast_yymm(start_override)}"
    default_desc = default_description(order, start_override)
    print()
    raw = input(f"  Contract code [{default_code}]: ").strip()
    contract_code = raw or default_code
    raw = input(f"  Description [{default_desc}]: ").strip()
    description = raw or default_desc
    raw = input(f"  Customer Order ref [{order.title}]: ").strip()
    customer_ref = raw or order.title

    _upsert_customer_db(
        cust_name,
        customer_id,
        code_name=code_prefix,
        description_name=DEFAULT_DESCRIPTION_PREFIX,
        separation=separation,
        billing_type=billing_type,
    )

    return {
        "customer_id": customer_id,
        "billing_type": billing_type,
        "separation": separation,
        "contract_code": contract_code,
        "description": description,
        "customer_ref": customer_ref,
        "start_date_override": fmt_mmddyyyy(start_override),
        "market": market_code,
    }


# ─── Direct DB entry ──────────────────────────────────────────────────────────


def _create_hpsj_contract(order: HPSJOrder, inputs: dict, dry_run: bool = False) -> Optional[str]:
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = inputs.get("customer_id")
    if customer_id is None:
        print(f"{_LABEL} ✗ No customer_id")
        return None
    separation = inputs.get("separation", (15, 0, 0))
    billing_type = inputs.get("billing_type", "direct")
    contract_code = inputs.get("contract_code") or DEFAULT_CODE_PREFIX
    description = inputs.get("description") or DEFAULT_DESCRIPTION_PREFIX
    market_code = inputs.get("market") or order.market_code
    customer_ref = inputs.get("customer_ref", "")

    override = inputs.get("start_date_override")
    flight_start_d = parse_date(override) if override else parse_date(order.flight_start)
    flight_end_d = parse_date(order.flight_end)

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=contract_code,
            description=description,
            customer_id=int(customer_id),
            agency_id=None,  # direct — ANAGRAF (lookup) is still authoritative
            lookup_customer_defaults=True,
            contract_date=flight_start_d,
            contract_end_date=flight_end_d,
            contract_type=1,
            billing_type=billing_type,
            media_center_id=MEDIA_CENTER_CALENDAR,
            allow_rename=True,
            customer_order_ref=customer_ref,
        )
        print(
            f"{_LABEL} ✓ Contract header: ID={contract_id}  code='{contract_code}'  ref='{customer_ref}'"
        )

        production_pending = round(sum(ch.amount for ch in order.charges), 2)
        line_count = 0
        for ln, days, time_range, desc, ranges, notes in _line_plan(
            order, flight_start_d, order.flight_end
        ):
            is_bonus = ln.is_bonus
            for note in notes:
                print(f"  [NOTE] {desc}: {note}")
            for rng in ranges:
                total_spots = rng["spots_per_week"] * rng["weeks"]
                line_count += 1
                tag = f"  ← {rng['tag']}" if rng["tag"] else ""
                print(
                    f"  [LINE {line_count}] {market_code} {desc}: "
                    f"{fmt_mmddyyyy(rng['date_from'])}–{fmt_mmddyyyy(rng['date_to'])} "
                    f"({rng['spots_per_week']}/wk×{rng['weeks']}w={total_spots}) :{ln.length_sec}s "
                    f"rate={ln.rate} max {rng['max_daily']}/day{tag}"
                )
                production = production_pending if not is_bonus else 0.0
                production_pending = round(production_pending - production, 2)

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
                    is_bonus=is_bonus,
                    booking_code=10 if is_bonus else 2,
                    separation_intervals=separation,
                    production_cost=production,
                )
                if production:
                    verify_production_charge(conn.cursor(), line_id, production, label="HPSJ")
                    print(
                        f"           ↳ Production ${production:,.2f} (→ CONTRATTISPESE 'Production', dated {fmt_mmddyyyy(rng['date_from'])})"
                    )

        if production_pending:
            raise RuntimeError(
                f"HPSJ: ${production_pending:,.2f} of production cost was never attached — no paid airtime line was created. Rolling back."
            )

        if dry_run:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA = %s", (contract_id,)
            )
            n = cur.fetchone()[0]
            cur.execute(
                "SELECT CENTROMEDIA, CUSTOMERREF FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = %s",
                (contract_id,),
            )
            cm, ref = cur.fetchone()
            conn.rollback()
            conn.close()
            print(
                f"{_LABEL} DRY RUN: {n} lines in txn, CENTROMEDIA={cm}, ref={ref!r} — ROLLED BACK"
            )
            return contract_code

        conn.commit()
        conn.close()
        print(f"{_LABEL} ✓ {line_count} lines committed.")
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


def run_hpsj_order(order: HPSJOrder, inputs: dict) -> list[tuple[str, bool]]:
    """One contract per proposal. Returns [(contract_code, success)]."""
    code = _create_hpsj_contract(order, inputs)
    label = inputs.get("contract_code") or DEFAULT_CODE_PREFIX
    return [(label, code is not None)]


if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description="HPSJ proposal → Etere (dry run rolls back)")
    ap.add_argument("pdf")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--start", help="start date m/d/yyyy (default: the proposal's)")
    a = ap.parse_args()
    o = parse_hpsj(a.pdf)
    start = parse_date(a.start) if a.start else parse_date(o.flight_start)
    print(f"\n  Etere lines for a {fmt_mmddyyyy(start)} start:")
    e, n, notes = _print_plan(o, start)
    print(f"  Spots: {e} entered of {n} ordered; notes: {notes or 'none'}")
    if a.dry_run:
        inputs = {
            "customer_id": DEFAULT_CUSTOMER_ID,
            "billing_type": "direct",
            "separation": (15, 0, 0),
            "contract_code": f"{DEFAULT_CODE_PREFIX} {broadcast_yymm(start)} DRYRUN",
            "description": default_description(o, start),
            "customer_ref": o.title,
            "start_date_override": fmt_mmddyyyy(start),
            "market": DEFAULT_MARKET,
        }
        _create_hpsj_contract(o, inputs, dry_run=True)
