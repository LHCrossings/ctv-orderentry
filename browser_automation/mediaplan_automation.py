"""
Crossings TV Media Plan → Etere (direct DB). Marquice Wong's house template, any
advertiser except Sierra Donor Services (which keeps its own order type).

One contract per sheet. Each airtime row is a TOTAL for its own dates (no weekly
columns), so each row is one Rotation line (``spots_per_week=0``), with the daily
cap sized from the days the line can air on. A ``ROS BONUS`` row airs in the
language's house ROS window.

Customer: the Advertiser line is looked up in ANAGRAF. ANAGRAF decides the agency
and commission (``lookup_customer_defaults=True``) and the billing cycle
(``ANAGRAF.CENTROMEDIA``, Lee 2026-11-25); the gather asks for the cycle only when
ANAGRAF holds none.

Production: the Translation Services fee goes in the FIRST PAID line's Production
box (→ CONTRATTISPESE 'Production'), verified inside the transaction, never a line.

Defaults offered at the gather (Lee 2026-11-25): code ``<advertiser> <yymm>``
('Soroudi 2612'), description ``<Etere customer name> <Mon YYYY>``
('Soroudi Vision Dec 2026').
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Optional

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
from browser_automation.etere_client import EtereClient
from browser_automation.line_planner import (
    broadcast_yymm,
    confirm_start_date,
    fmt_mmddyyyy,
    parse_date,
    verify_production_charge,
)
from browser_automation.parsers.mediaplan_parser import (
    MediaPlanLine,
    MediaPlanOrder,
    parse_mediaplan,
)

DEFAULT_SEPARATION = (15, 0, 0)  # customer, order, event
MEDIA_CENTER = {"broadcast": 316, "calendar": 317}  # CONTRATTITESTATA.CENTROMEDIA
_CYCLE = {316: "Broadcast", 317: "Calendar"}
_MARKETS = ("CVC", "SFO", "LAX", "SEA", "HOU", "CMP", "WDC", "NYC", "MMT", "DAL")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_DAY_KEYS = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
_LABEL = "[MEDIA PLAN]"


# ─── Planning ─────────────────────────────────────────────────────────────────


def _active_days(days: str, d_from: date, d_to: date) -> int:
    from browser_automation.etere_direct_client import parse_day_bits

    bits = parse_day_bits(days)
    n, d = 0, d_from
    while d <= d_to:
        if bits.get(_DAY_KEYS[d.weekday()]):
            n += 1
        d += timedelta(days=1)
    return n


def line_description(ln: MediaPlanLine, days: str) -> str:
    """'M-F 2p-4p Punjabi' / 'BNS M-F Punjabi ROS'."""
    if ln.is_bonus:
        return f"BNS {days} {ln.language} ROS"[:60]
    return f"{days} {ln.time_str} {ln.language}"[:60]


def order_plan(order: MediaPlanOrder, start_from: date) -> list[dict]:
    """One dict per Etere line: what the gather shows is what the entry writes.

    A start later than a row's own Start Date moves that row's start; the count is
    a flight total, so it stays, and the daily cap is sized from the days left.
    A row that can no longer hold its spots raises.
    """
    plan: list[dict] = []
    for ln in order.lines:
        days, _ = EtereClient.check_sunday_6_7a_rule(ln.days, ln.time_str)
        t_from, t_to = EtereClient.parse_time_range(ln.time_str)
        d_from = max(ln.start_date, start_from)
        active = _active_days(days, d_from, ln.end_date) if d_from <= ln.end_date else 0
        if active == 0:
            raise ValueError(
                f"{ln.language} {ln.daypart_raw}: no {days} day between "
                f"{fmt_mmddyyyy(d_from)} and {fmt_mmddyyyy(ln.end_date)}"
            )
        plan.append(
            {
                "line": ln,
                "days": days,
                "time_range": f"{t_from}-{t_to}",
                "description": line_description(ln, days),
                "date_from": d_from,
                "date_to": ln.end_date,
                "total_spots": ln.total_spots,
                "active_days": active,
                "max_daily": math.ceil(ln.total_spots / active),
            }
        )
    return plan


def default_code_desc(
    order: MediaPlanOrder, start: date, cycle: str, cust_name: str = "", code_name: str = ""
) -> tuple[str, str]:
    """('Soroudi 2612', 'Soroudi Vision Dec 2026'). The month is the broadcast month
    for a Broadcast contract and the calendar month for a Calendar one."""
    if cycle.lower() == "broadcast":
        yymm = broadcast_yymm(start)
    else:
        yymm = f"{start.year % 100:02d}{start.month:02d}"
    label = f"{_MONTHS[int(yymm[2:]) - 1]} 20{yymm[:2]}"
    return f"{code_name or order.advertiser} {yymm}", f"{cust_name or order.advertiser} {label}"


# ─── Customer ─────────────────────────────────────────────────────────────────


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
    name: str, customer_id: int, separation: tuple, billing_type: str, market: str, code_name: str
) -> None:
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        from src.domain.enums import OrderType

        CustomerRepository(CUSTOMER_DB_PATH).save(
            Customer(
                customer_id=str(customer_id),
                customer_name=name,
                order_type=OrderType.MEDIAPLAN,
                billing_type=billing_type,
                code_name=code_name,
                description_name=name,
                separation_customer=separation[0],
                separation_order=separation[1],
                separation_event=separation[2],
                default_market=market,
            )
        )
    except Exception as exc:
        print(f"[CUSTOMER] customer DB upsert failed (non-fatal): {exc}")


_ANAGRAF_SQL = "SELECT ID_ANAGRAF, RAG_SOCIAL, AGENZIA, Commissione, CENTROMEDIA FROM ANAGRAF "


def _anagraf_row(r) -> dict:
    return {
        "id": int(r[0]),
        "name": str(r[1] or "").strip(),
        "agency": int(r[2] or 0),
        "pct": float(r[3] or 0),
        "media_center": int(r[4] or 0),
    }


def anagraf_candidates(advertiser: str) -> list[dict]:
    """ANAGRAF rows whose name starts with the advertiser as the sheet prints it."""
    from browser_automation.etere_direct_client import connect

    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(
            _ANAGRAF_SQL + "WHERE RAG_SOCIAL LIKE %s ORDER BY ID_ANAGRAF",
            (f"{advertiser.strip()}%",),
        )
        return [_anagraf_row(r) for r in cur.fetchall()]
    finally:
        conn.close()


def anagraf_by_id(customer_id: int) -> Optional[dict]:
    from browser_automation.etere_direct_client import connect

    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(_ANAGRAF_SQL + "WHERE ID_ANAGRAF = %s", (int(customer_id),))
        r = cur.fetchone()
        return _anagraf_row(r) if r else None
    finally:
        conn.close()


# ─── Gather ───────────────────────────────────────────────────────────────────


def _print_plan(plan: list[dict]) -> None:
    for p in plan:
        ln = p["line"]
        rate = f"${ln.rate:.2f}" if ln.rate else "bonus"
        print(
            f"      {p['description']:<28} {p['days']:<5} {p['time_range']}  "
            f"{fmt_mmddyyyy(p['date_from'])}–{fmt_mmddyyyy(p['date_to'])}  "
            f"{p['total_spots']} spots, max {p['max_daily']}/day over {p['active_days']} days  {rate}"
        )


def gather_mediaplan_inputs(source_path: str) -> Optional[dict]:
    """Gather inputs for a Crossings TV Media Plan. Returns dict or None to abort."""
    order = parse_mediaplan(source_path)

    print(f"\n{'=' * 64}")
    print(f"Advertiser: {order.advertiser}   {order.option}")
    print(f"Contact:    {order.client_email}  {order.client_phone}")
    print(f"Acct Rep:   {order.acct_rep}")
    print(f"Market:     {order.market or '?'}   ({order.market_label})")
    print(f"Flight:     {fmt_mmddyyyy(order.start)}–{fmt_mmddyyyy(order.end)}")
    print("\n  Rows:")
    for ln in order.lines:
        rate = f"${ln.rate:.2f}" if ln.rate else "bonus"
        print(
            f"    {'BNS' if ln.is_bonus else '   '} {ln.language:<12} {ln.daypart_raw:<22} "
            f"{ln.days:<5} {ln.time_str:<10} {rate:>8}  {ln.total_spots:>3} spots  "
            f":{ln.duration_seconds}s"
        )
    paid = sum(ln.total_spots for ln in order.paid_lines)
    bonus = sum(ln.total_spots for ln in order.bonus_lines)
    print(
        f"\n  Order: {paid} paid + {bonus} bonus spots   ${order.airtime_total:,.2f} airtime",
        end="",
    )
    if order.production:
        print(
            f" + ${order.production:,.2f} production ({order.production_note})"
            f" = ${order.airtime_total + order.production:,.2f}"
        )
        print("  Production goes in the first paid line's Production box, not on a line.")
    else:
        print()

    market = order.market
    raw = input(f"  Market [{market}]: ").strip().upper()
    market = raw or market
    if market not in _MARKETS:
        print("  ✗ Unknown market — aborting")
        return None

    # ── Customer (ANAGRAF by the Advertiser line) ──
    try:
        found = anagraf_candidates(order.advertiser)
    except Exception as exc:
        print(f"[CUSTOMER] ANAGRAF lookup failed: {exc}")
        found = []
    default_id = ""
    if len(found) == 1:
        default_id = str(found[0]["id"])
        print(f"\n[CUSTOMER] ANAGRAF '{found[0]['name']}' → ID {found[0]['id']}")
    elif found:
        print(f"\n[CUSTOMER] ANAGRAF has {len(found)} names starting with '{order.advertiser}':")
        for f in found:
            print(f"    {f['id']:>5}  {f['name']}")
    else:
        print(f"\n[CUSTOMER] ANAGRAF has no name starting with '{order.advertiser}'.")
    customer = None
    while customer is None:
        raw = input(f"  Customer (ANAGRAF) ID [{default_id}]: ").strip() or default_id
        if raw.lower() in ("y", "yes"):
            raw = default_id
        if not raw.isdigit():
            print("    ✗ Enter the numeric ANAGRAF ID.")
            continue
        customer = anagraf_by_id(int(raw))
        if customer is None:
            print(f"    ✗ ANAGRAF has no ID {raw}.")
    billing_type = "agency" if customer["agency"] else "direct"
    print(
        f"  ✓ {customer['name']} (ID {customer['id']}): "
        + (f"agency {customer['agency']}" if customer["agency"] else "direct, no agency")
        + f", {customer['pct']:.0f}% commission"
    )

    # ── Billing cycle: ANAGRAF, asked only when ANAGRAF holds none ──
    cycle = _CYCLE.get(customer["media_center"])
    if cycle:
        print(f"  Billing cycle: {cycle} (ANAGRAF)")
    while not cycle:
        raw = input("  ANAGRAF holds no billing cycle. Broadcast or Calendar? ").strip().lower()
        cycle = {
            "b": "Broadcast",
            "broadcast": "Broadcast",
            "c": "Calendar",
            "calendar": "Calendar",
        }.get(raw)
    media_center_id = MEDIA_CENTER[cycle.lower()]

    # ── Separation (customer DB, else the house default; the orchestrator confirms) ──
    cust = _lookup_customer_db(customer["name"]) or _lookup_customer_db(order.advertiser)
    separation = DEFAULT_SEPARATION
    if cust:
        separation = (cust.separation_customer, cust.separation_order, cust.separation_event)

    # ── Start date + line plan ──
    start = confirm_start_date(fmt_mmddyyyy(order.start), fmt_mmddyyyy(order.end))
    if start is None:
        print("  ✗ No flight start date — aborting")
        return None
    try:
        plan = order_plan(order, start)
    except ValueError as exc:
        print(f"  ✗ {exc} — aborting")
        return None
    print(f"\n    Etere lines for a {fmt_mmddyyyy(start)} start (all Rotation):")
    _print_plan(plan)

    code_name = (cust.code_name if cust and cust.code_name else "") or order.advertiser
    default_code, default_desc = default_code_desc(order, start, cycle, customer["name"], code_name)
    raw = input(f"  Contract code [{default_code}]: ").strip()
    code = raw or default_code
    raw = input(f"  Description [{default_desc}]: ").strip()
    desc = raw or default_desc

    _upsert_customer_db(
        customer["name"], customer["id"], separation, billing_type, market, code_name
    )

    return {
        "customer_id": customer["id"],
        "billing_type": billing_type,
        "media_center_id": media_center_id,
        "separation": separation,
        "market": market,
        "contract_code": code,
        "description": desc,
        "start_date_override": fmt_mmddyyyy(start),
    }


# ─── Direct DB entry ──────────────────────────────────────────────────────────


def _create_mediaplan_contract(
    order: MediaPlanOrder, inputs: dict, dry_run: bool = False
) -> Optional[str]:
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = inputs.get("customer_id")
    market = inputs.get("market") or order.market
    if customer_id is None or not market:
        print(f"{_LABEL} ✗ No customer_id / market")
        return None
    separation = tuple(inputs.get("separation") or DEFAULT_SEPARATION)
    media_center_id = int(inputs["media_center_id"])
    code = inputs["contract_code"]
    start = (
        parse_date(inputs["start_date_override"])
        if inputs.get("start_date_override")
        else order.start
    )

    conn = None
    try:
        plan = order_plan(order, start)
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=code,
            description=inputs["description"],
            customer_id=int(customer_id),
            agency_id=None,  # ANAGRAF (lookup) is authoritative for agency + commission
            lookup_customer_defaults=True,
            contract_date=min(p["date_from"] for p in plan),
            contract_end_date=max(p["date_to"] for p in plan),
            contract_type=1,
            billing_type=inputs.get("billing_type", "direct"),
            media_center_id=media_center_id,
            allow_rename=True,
            customer_order_ref="",
        )
        print(f"{_LABEL} ✓ Contract header: ID={contract_id}  code='{code}'")

        production_pending = round(order.production, 2)
        for n, p in enumerate(plan, 1):
            ln = p["line"]
            print(
                f"  [LINE {n}] {market} {p['description']}: "
                f"{fmt_mmddyyyy(p['date_from'])}–{fmt_mmddyyyy(p['date_to'])} "
                f"{p['total_spots']} spots :{ln.duration_seconds}s rate={ln.rate} "
                f"max {p['max_daily']}/day"
            )
            prod = production_pending if not ln.is_bonus else 0.0
            production_pending = round(production_pending - prod, 2)
            line_id = client.add_contract_line(
                market=market,
                days=p["days"],
                time_range=p["time_range"],
                description=p["description"],
                rate=ln.rate,
                total_spots=p["total_spots"],
                spots_per_week=0,  # a flight total, no weekly columns → Rotation
                max_daily_run=p["max_daily"],
                date_from=p["date_from"],
                date_to=p["date_to"],
                duration=str(ln.duration_seconds),
                is_bonus=ln.is_bonus,
                booking_code=10 if ln.is_bonus else 2,
                separation_intervals=separation,
                production_cost=prod,
            )
            if prod:
                verify_production_charge(conn.cursor(), line_id, prod, label="Media Plan")
                print(f"           ↳ Production ${prod:,.2f} (→ CONTRATTISPESE 'Production')")

        if production_pending:
            raise RuntimeError(
                f"Media Plan: ${production_pending:,.2f} production was never attached "
                "— no paid line. Rolling back."
            )

        # Read back what the order must hold before committing.
        cur = conn.cursor()
        cur.execute(
            "SELECT CENTROMEDIA, P_AGENZIA, AGENZIA FROM CONTRATTITESTATA "
            "WHERE ID_CONTRATTITESTATA = %s",
            (int(contract_id),),
        )
        row = cur.fetchone()
        if row is None or int(row[0] or 0) != media_center_id:
            raise RuntimeError(
                f"Media Plan: header CENTROMEDIA={row and row[0]} but {media_center_id} "
                "was expected; rolling back"
            )
        cur.execute(
            "SELECT COUNT(*), ISNULL(SUM(N_PASSAGGI), 0), ISNULL(SUM(N_PASSAGGI * IMPORTO), 0), "
            "SUM(CASE WHEN PRENOTAZIONE = 1 THEN 1 ELSE 0 END) "
            "FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA = %s",
            (int(contract_id),),
        )
        n_lines, n_spots, money, n_rotation = cur.fetchone()
        want_spots = sum(p["total_spots"] for p in plan)
        if (
            int(n_lines) != len(plan)
            or int(n_spots) != want_spots
            or abs(float(money) - order.airtime_total) > 0.01
            or int(n_rotation or 0) != len(plan)
        ):
            raise RuntimeError(
                f"Media Plan: readback {n_lines} lines / {n_spots} spots / ${float(money):,.2f} / "
                f"{n_rotation} Rotation, expected {len(plan)} / {want_spots} / "
                f"${order.airtime_total:,.2f} / {len(plan)}; rolling back"
            )
        summary = (
            f"{n_lines} lines, {n_spots} spots, ${float(money):,.2f} airtime, "
            f"${order.production:,.2f} production, CENTROMEDIA {row[0]}, "
            f"commission {float(row[1] or 0):g}%, agency {int(row[2] or 0) or 'none'}"
        )
        if dry_run:
            conn.rollback()
            conn.close()
            print(f"{_LABEL} DRY RUN: {summary} — ROLLED BACK")
            return code
        conn.commit()
        conn.close()
        print(f"{_LABEL} ✓ committed on {code}: {summary}")
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


def run_mediaplan_order(order: MediaPlanOrder, inputs: dict) -> list[tuple[str, bool]]:
    """One contract per sheet. Returns [(code, success)]."""
    code = _create_mediaplan_contract(order, inputs)
    return [(inputs.get("contract_code") or code or "", code is not None)]


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Crossings TV Media Plan → Etere (direct DB)")
    ap.add_argument("pdf")
    ap.add_argument("--dry-run", action="store_true", help="write in a transaction, roll back")
    ap.add_argument("--customer", type=int, help="ANAGRAF ID (dry run)")
    ap.add_argument("--start", help="start date M/D/YYYY (default: the sheet's flight start)")
    a = ap.parse_args()
    o = parse_mediaplan(a.pdf)
    if a.dry_run:
        cands = [anagraf_by_id(a.customer)] if a.customer else anagraf_candidates(o.advertiser)
        if len(cands) != 1 or cands[0] is None:
            raise SystemExit(f"dry run needs one customer; pass --customer ({cands})")
        c = cands[0]
        cyc = _CYCLE.get(c["media_center"], "Broadcast")
        st = parse_date(a.start) if a.start else o.start
        code, desc = default_code_desc(o, st, cyc, c["name"])
        _create_mediaplan_contract(
            o,
            {
                "customer_id": c["id"],
                "billing_type": "agency" if c["agency"] else "direct",
                "media_center_id": MEDIA_CENTER[cyc.lower()],
                "separation": DEFAULT_SEPARATION,
                "market": o.market,
                "contract_code": code,
                "description": desc,
                "start_date_override": fmt_mmddyyyy(st),
            },
            dry_run=True,
        )
    else:
        inp = gather_mediaplan_inputs(a.pdf)
        if inp:
            print(run_mediaplan_order(o, inp))
