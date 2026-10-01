"""
Gauger + Associates — direct-DB entry for the agency's "Broadcast Order" PDF (NET rates).

Conventions (Lee 2026-10-01, oracle contract 823 `Gauger Shea 2304`, April 2023):
  * agency Gauger + Associates = ANAGRAF 262 at 15%; the client's ANAGRAF row names the
    agency and commission (Shea Homes 263 → 262), AGENCY_IDS["GAUGER"] is the fallback;
  * the IO quotes NET: every paid rate is grossed up by the agency commission at entry
    (net / (1 - fee), rounded to cents — $102 → $120, $85 → $100 on the oracle). The
    backwrite gets the NET rate + rates_are_net through parser_bridge;
  * market from the IO's "Market:" line (San Francisco → SFO), confirmed at gather;
  * code `Gauger <client> <yymm>` (customers.db code_name, default "Gauger Shea"),
    description `<client name> <Ad name> <yymm>`, Customer Order ref `Order <number>`;
  * ONE Etere line per IO row over the whole flight with the IO's spot total — the IO
    has no weekly breakdown, so spots_per_week=0 lets add_contract_line pick Rotation;
    max/day = ceil(spots / active days of the pattern inside the flight);
  * the bonus row keeps its table window (`BNS M-Su 1p-4p Hindi/Punjabi`), $0, code 10.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Optional

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
from browser_automation.etere_client import EtereClient
from browser_automation.etere_direct_client import AGENCY_IDS, parse_day_bits
from browser_automation.line_planner import (
    active_days,
    broadcast_yymm,
    confirm_start_date,
    fmt_mmddyyyy,
    parse_date,
)
from browser_automation.parsers.gauger_parser import (
    AGENCY_NAME,
    GaugerLine,
    GaugerOrder,
    parse_gauger,
)

# Client code on the IO → Etere customer (ANAGRAF). customers.db wins once a client has
# been entered through this parser; this is only the first-time default.
DEFAULT_CUSTOMER_BY_CLIENT = {"SHA": 263}  # Shea Homes
DEFAULT_CLIENT_SHORT = {"SHA": "Shea"}  # for the code: `Gauger Shea 2610`
DEFAULT_SEPARATION = (15, 0, 0)
DEFAULT_AGENCY_FEE = 15.0
DEFAULT_MARKET = "SFO"
_MARKETS = ("CVC", "SFO", "LAX", "SEA", "HOU", "CMP", "WDC", "NYC", "MMT", "DAL")
_LABEL = "[GAUGER]"


def gross_rate(net: float, fee_pct: float) -> float:
    """Net per-spot rate → the gross Etere stores (rounded to cents)."""
    if not net:
        return 0.0
    return round(float(net) / (1.0 - fee_pct / 100.0), 2)


# ─── Planner (single source of truth for preview AND writer) ─────────────────


def _max_daily(ln: GaugerLine, start: date, end: date) -> int:
    bits = parse_day_bits(ln.days)
    if not any(bits.values()):
        raise ValueError(f"day pattern {ln.days!r} selects no days")
    n = active_days(start, end, bits)
    if n <= 0:
        raise ValueError(
            f"{ln.description}: no {ln.days} days between {fmt_mmddyyyy(start)} and {fmt_mmddyyyy(end)}"
        )
    return max(1, math.ceil(ln.spots / n))


def line_plan(order: GaugerOrder, start_from: date, fee_pct: float) -> list[dict]:
    """IO row → one Etere line over the row's own dates (clipped to a later start)."""
    plan: list[dict] = []
    for ln in order.lines:
        if ln.spots == 0:
            continue
        days, _ = EtereClient.check_sunday_6_7a_rule(ln.days, ln.time)
        t_from, t_to = EtereClient.parse_time_range(ln.time)
        d_from = max(ln.date_from, start_from)
        d_to = ln.date_to
        if d_to < d_from:
            raise ValueError(
                f"{ln.description}: flight ends {fmt_mmddyyyy(d_to)} before the start {fmt_mmddyyyy(d_from)}"
            )
        plan.append(
            {
                "line": ln,
                "days": days,
                "time_range": f"{t_from}-{t_to}",
                "description": ln.description[:60],
                "date_from": d_from,
                "date_to": d_to,
                "total_spots": ln.spots,
                "max_daily": _max_daily(ln, d_from, d_to),
                "is_bonus": ln.is_bonus,
                "net_rate": ln.net_rate,
                "rate": 0.0 if ln.is_bonus else gross_rate(ln.net_rate, fee_pct),
                "length_sec": ln.length_sec,
            }
        )
    return plan


def print_plan(plan: list[dict], fee_pct: float) -> None:
    print(f"\n  Etere lines (NET → gross at {fee_pct:g}%):")
    for p in plan:
        money = f"${p['net_rate']:>7.2f} → ${p['rate']:>7.2f}" if p["rate"] else "          bonus"
        print(
            f"    {p['description']:<40} {p['time_range']}  "
            f"{fmt_mmddyyyy(p['date_from'])}–{fmt_mmddyyyy(p['date_to'])}  "
            f"{p['total_spots']:>3} spots  max {p['max_daily']}/day  {money}"
        )
    gross = sum(p["rate"] * p["total_spots"] for p in plan)
    net = sum(p["net_rate"] * p["total_spots"] for p in plan)
    print(f"    {'':<40} gross ${gross:,.2f}  (net ${net:,.2f})")


# ─── customers.db / ANAGRAF ───────────────────────────────────────────────────


def _lookup_customer_db(name: str):
    try:
        import os

        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.enums import OrderType

        if not os.path.exists(CUSTOMER_DB_PATH):
            return None
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        return repo.find_by_name(name, OrderType.GAUGER) or repo.find_by_name_any_type(name)
    except Exception:
        return None


def _upsert_customer_db(
    name, customer_id, code_name, description_name, separation, billing_type, market
) -> None:
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        from src.domain.enums import OrderType

        CustomerRepository(CUSTOMER_DB_PATH).save(
            Customer(
                customer_id=str(customer_id),
                customer_name=name,
                order_type=OrderType.GAUGER,
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


def default_description(description_name: str, order: GaugerOrder, start: date) -> str:
    return f"{description_name} {order.ad_name} {broadcast_yymm(start)}".strip()


def gather_gauger_inputs(source_path: str) -> Optional[dict]:
    """Gather inputs for a Gauger + Associates order. Returns dict or None to abort."""
    order = parse_gauger(source_path)

    print(f"\n{'=' * 64}")
    print(
        f"Agency:     {AGENCY_NAME}   Order {order.order_number} ({order.revision or 'Original'})"
    )
    print(f"Client:     {order.client_code}   Job {order.job}   Ad name: {order.ad_name}")
    print(f"Market:     {order.market_name} → {order.market_code or '?'}")
    print(
        f"Flight:     {fmt_mmddyyyy(order.flight_start)} → {fmt_mmddyyyy(order.flight_end)}   {order.broadcast_month}"
    )
    print("Money:      NET rates — grossed up by the agency commission at entry")
    print("\n  IO lines (net):")
    for ln in order.lines:
        rate = f"${ln.net_rate:.2f}" if not ln.is_bonus else "  bonus"
        print(
            f"    {ln.description:<40} :{ln.length_sec}  {ln.spots:>3} spots  {rate:>8}  ${ln.net_total:,.2f}"
        )
    print(
        f"\n  Spots: {sum(x.spots for x in order.paid_lines)} paid + "
        f"{sum(x.spots for x in order.bonus_lines)} bonus = {order.total_spots}   NET ${order.net_total:,.2f}"
    )

    raw = input(f"  Market [{order.market_code or DEFAULT_MARKET}]: ").strip().upper()
    market_code = (order.market_code or DEFAULT_MARKET) if not raw or raw in ("Y", "YES") else raw
    if market_code not in _MARKETS:
        print("  ✗ Unknown market — aborting")
        return None

    start_override = confirm_start_date(order.flight_start, order.flight_end)
    if start_override is None:
        print("  ✗ No flight start date — aborting")
        return None

    from browser_automation.customer_defaults import prompt_customer_id

    cust = _lookup_customer_db(order.client_code)
    default_id = (
        str(cust.customer_id)
        if cust and cust.customer_id
        else str(DEFAULT_CUSTOMER_BY_CLIENT.get(order.client_code, ""))
    )
    raw_id = prompt_customer_id(default_id)
    if raw_id is None:
        print("  ✗ No customer ID — aborting")
        return None
    customer_id = int(raw_id)

    anag = _anagraf_defaults(customer_id)
    cust_name = anag.get("name") or (cust.customer_name if cust else "") or order.client_code
    agency_id = anag.get("agency_id") or AGENCY_IDS["GAUGER"]
    fee_pct = anag.get("agency_pct") if anag.get("agency_id") else DEFAULT_AGENCY_FEE
    print(
        f"  [CUSTOMER] ANAGRAF {customer_id} = {cust_name}   agency {agency_id} "
        f"{anag.get('agency_name') or AGENCY_NAME} @ {fee_pct:g}%"
    )
    if agency_id != AGENCY_IDS["GAUGER"]:
        print(
            f"  ⚠ ANAGRAF links this client to agency {agency_id}, not Gauger ({AGENCY_IDS['GAUGER']}) — ANAGRAF wins."
        )

    separation = DEFAULT_SEPARATION
    billing_type = "agency"
    if cust:
        billing_type = cust.billing_type or billing_type
        separation = (cust.separation_customer, cust.separation_event, cust.separation_order)

    short = DEFAULT_CLIENT_SHORT.get(
        order.client_code, cust_name.split()[0] if cust_name else order.client_code
    )
    code_name = (
        cust.code_name if cust and getattr(cust, "code_name", "") else ""
    ) or f"Gauger {short}"
    description_name = (
        cust.description_name if cust and getattr(cust, "description_name", "") else ""
    ) or cust_name

    plan = line_plan(order, start_override, fee_pct)
    print_plan(plan, fee_pct)

    d_code = default_code(code_name, start_override)
    d_desc = default_description(description_name, order, start_override)
    print()
    raw = input(f"  Contract code [{d_code}]: ").strip()
    contract_code = raw or d_code
    raw = input(f"  Description [{d_desc}]: ").strip()
    description = raw or d_desc
    raw = input(f"  Customer Order ref [{order.customer_ref}]: ").strip()
    customer_ref = raw or order.customer_ref

    _upsert_customer_db(
        order.client_code,
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


def _create_gauger_contract(
    order: GaugerOrder, inputs: dict, dry_run: bool = False
) -> Optional[str]:
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = inputs.get("customer_id")
    if customer_id is None:
        print(f"{_LABEL} ✗ No customer_id")
        return None
    separation = tuple(inputs.get("separation", DEFAULT_SEPARATION))
    billing_type = inputs.get("billing_type", "agency")
    contract_code = inputs.get("contract_code") or f"Gauger {order.client_code}"
    description = inputs.get("description") or f"{order.client_code} {order.ad_name}"
    market_code = inputs.get("market") or order.market_code or DEFAULT_MARKET
    customer_ref = inputs.get("customer_ref") or order.customer_ref
    fee_pct = float(inputs.get("agency_pct", DEFAULT_AGENCY_FEE))

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
            agency_id=AGENCY_IDS["GAUGER"],  # fallback; ANAGRAF (lookup) decides agency + %
            lookup_customer_defaults=True,
            contract_date=flight_start_d,
            contract_end_date=flight_end_d,
            contract_type=1,
            billing_type=billing_type,
            customer_order_ref=customer_ref,
            allow_rename=True,
        )
        print(
            f"{_LABEL} ✓ Contract header: ID={contract_id}  code='{contract_code}'  ref='{customer_ref}'"
        )

        # The header's commission is what Etere will net the gross rates by; it must be
        # the same percentage the rates were grossed up with.
        cur = conn.cursor()
        cur.execute(
            "SELECT P_AGENZIA, AGENZIA, CUSTOMERREF FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = %s",
            (contract_id,),
        )
        p_ag, ag, ref = cur.fetchone()
        if abs(float(p_ag or 0) - fee_pct) > 0.001:
            raise RuntimeError(
                f"header commission {p_ag}% != {fee_pct}% used for the gross-up — rolling back"
            )
        if (ref or "").strip() != customer_ref:
            raise RuntimeError(f"CUSTOMERREF readback {ref!r} != {customer_ref!r} — rolling back")

        plan = line_plan(order, flight_start_d, fee_pct)
        line_ids: list[int] = []
        for i, p in enumerate(plan, 1):
            print(
                f"  [LINE {i}] {market_code} {p['description']}: "
                f"{fmt_mmddyyyy(p['date_from'])}–{fmt_mmddyyyy(p['date_to'])} "
                f"{p['total_spots']} spots :{p['length_sec']}s rate={p['rate']} max {p['max_daily']}/day"
            )
            line_id = client.add_contract_line(
                market=market_code,
                days=p["days"],
                time_range=p["time_range"],
                description=p["description"],
                rate=p["rate"],
                total_spots=p["total_spots"],
                spots_per_week=0,  # no week columns on the IO → Rotation over the flight
                max_daily_run=p["max_daily"],
                date_from=p["date_from"],
                date_to=p["date_to"],
                duration=str(p["length_sec"]),
                is_bonus=p["is_bonus"],
                booking_code=10 if p["is_bonus"] else 2,
                separation_intervals=separation,
            )
            line_ids.append(line_id)

        # Read back what was written where the user reads it.
        cur.execute(
            "SELECT ID_CONTRATTIRIGHE, IMPORTO, N_PASSAGGI, PRENOTAZIONE, ID_BOOKINGCODE, COD_USER "
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
        gross = sum(p["rate"] * p["total_spots"] for p in plan)
        print(
            f"{_LABEL} readback OK: {len(rows)} lines, gross ${gross:,.2f} "
            f"(net ${order.net_total:,.2f} at {fee_pct:g}%), ref {ref!r}, commission {p_ag}%"
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


def run_gauger_order(order: GaugerOrder, inputs: dict) -> list[tuple[str, bool]]:
    """One contract per IO. Returns [(contract_code, success)]."""
    code = _create_gauger_contract(order, inputs)
    label = inputs.get("contract_code") or f"Gauger {order.client_code}"
    return [(label, code is not None)]


if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description="Gauger Broadcast Order → Etere (dry run rolls back)")
    ap.add_argument("pdf")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--start", help="start date m/d/yyyy (default: the IO's)")
    a = ap.parse_args()
    o = parse_gauger(a.pdf)
    start = parse_date(a.start) if a.start else o.flight_start
    cid = DEFAULT_CUSTOMER_BY_CLIENT.get(o.client_code, 0)
    anag = _anagraf_defaults(cid) if cid else {}
    fee = anag.get("agency_pct") or DEFAULT_AGENCY_FEE
    print_plan(line_plan(o, start, fee), fee)
    if a.dry_run:
        short = DEFAULT_CLIENT_SHORT.get(o.client_code, o.client_code)
        inputs = {
            "customer_id": cid,
            "agency_id": anag.get("agency_id") or AGENCY_IDS["GAUGER"],
            "agency_pct": fee,
            "billing_type": "agency",
            "separation": DEFAULT_SEPARATION,
            "contract_code": f"{default_code(f'Gauger {short}', start)} DRYRUN",
            "description": default_description(anag.get("name") or o.client_code, o, start),
            "customer_ref": o.customer_ref,
            "start_date_override": fmt_mmddyyyy(start),
            "market": o.market_code or DEFAULT_MARKET,
        }
        _create_gauger_contract(o, inputs, dry_run=True)
