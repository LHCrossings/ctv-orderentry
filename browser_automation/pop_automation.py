"""Prince of Peace (POP) — gather + direct-DB entry.

Conventions (from the contracts already in Etere — POP KLO 2502 … 2602 — and
Lee, 2026-08-28: "direct, no commission"):
  * customer = ANAGRAF 90 "Prince of Peace Enterprises, Inc." — DIRECT, no
    agency, 0% (ANAGRAF wins via lookup_customer_defaults; agency_id=None).
    ANAGRAF 91 "Kwan Loong Oil" is the ADVERTISER record, not the payer.
  * market from the confirmation ("CV California" → CVC), confirmable
  * contract code 'POP KLO <yymm>', description '<Advertiser> <yymm>'
    (e.g. 'POP KLO 2609' / 'Kwan Loong Oil 2609')
  * month-only lines → Rotation (spots_per_week=0, the universal rule);
    max/day = ceil(spots / active days of the line's pattern in its flight)
  * line descriptions mirror history: 'M-F Vietnamese', 'Sa-Su Vietnamese',
    'BONUS Viet ROS' (bonus = ROS in the language block, ROS_SCHEDULES)
  * separation: customers.db, else (25, 0, 0) — what every prior POP contract
    carries (Interv_Committente 44955 frames = 25 min)
  * start date always asked (late confirmations are the norm for this client)
"""

from __future__ import annotations

import math
from datetime import date
from typing import Optional

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
from browser_automation.etere_client import EtereClient
from browser_automation.line_planner import (
    active_days,
    broadcast_yymm,
    confirm_start_date,
    fmt_mmddyyyy,
    parse_date,
)
from browser_automation.parsers.pop_parser import POPOrder, parse_pop
from browser_automation.ros_definitions import ROS_SCHEDULES

DEFAULT_CUSTOMER_ID = 90
ADVERTISER_ANAGRAF_ID = 91
DEFAULT_CODE_PREFIX = "POP KLO"
DEFAULT_SEPARATION = (25, 0, 0)
_MARKETS = ("CVC", "SFO", "LAX", "SEA", "HOU", "CMP", "WDC", "NYC", "MMT", "DAL")
_SHORT_LANG = {"Vietnamese": "Viet"}


# ─── Planner (single source of truth for preview AND writer) ─────────────────


def _line_plan(order: POPOrder, start_from: Optional[date]) -> list[dict]:
    """One Etere line per confirmation line: days, HH:MM window, description,
    dates (a start override moves lines that open on the original flight start),
    total spots, max/day, rotation."""
    from browser_automation.etere_direct_client import parse_day_bits

    flight_start = parse_date(order.flight_start)
    plan: list[dict] = []
    for ln in order.lines:
        if ln.total_spots == 0:
            continue
        if ln.is_bonus:
            ros = ROS_SCHEDULES.get(ln.language)
            if ros:
                days, time_raw = ros["days"], ros["time"]
            else:
                days, time_raw = "M-Su", "6a-11:59p"
                print(f"  [WARN] No ROS window for '{ln.language}' — using {days} {time_raw}")
            desc = f"BONUS {_SHORT_LANG.get(ln.language, ln.language)} ROS"
        else:
            days, time_raw = ln.days, ln.time
            days, _ = EtereClient.check_sunday_6_7a_rule(days, time_raw)
            desc = f"{days} {ln.language}"
        time_from, time_to = EtereClient.parse_time_range(time_raw)
        date_from = parse_date(ln.start_date)
        date_to = parse_date(ln.end_date)
        if start_from and date_from == flight_start and start_from != flight_start:
            date_from = start_from
        n_days = active_days(date_from, date_to, parse_day_bits(days))
        if n_days <= 0:
            raise ValueError(f"{desc}: no {days} days between {date_from} and {date_to}")
        plan.append(
            {
                "line": ln,
                "days": days,
                "time_range": f"{time_from}-{time_to}",
                "description": desc[:60],
                "date_from": date_from,
                "date_to": date_to,
                "total_spots": ln.total_spots,
                "max_daily": max(1, math.ceil(ln.total_spots / n_days)),
                "active_days": n_days,
                "is_bonus": ln.is_bonus,
            }
        )
    return plan


def _print_plan(order: POPOrder, start: Optional[date]) -> None:
    for p in _line_plan(order, start):
        ln = p["line"]
        rate = f"${ln.rate:.2f}" if ln.rate else "  bonus"
        print(
            f"    {p['description']:<22} {p['days']:<6} {p['time_range']}  "
            f"{fmt_mmddyyyy(p['date_from'])}–{fmt_mmddyyyy(p['date_to'])}  "
            f"{p['total_spots']:>3} spots  max {p['max_daily']}/day ({p['active_days']} days)  "
            f":{ln.length_sec}s {rate:>8}  Rotation"
        )


# ─── customers.db / ANAGRAF ──────────────────────────────────────────────────


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
    name: str,
    customer_id: int,
    code_name: str,
    description_name: str,
    separation: tuple,
    billing_type: str,
    market: str,
) -> None:
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        from src.domain.enums import OrderType

        CustomerRepository(CUSTOMER_DB_PATH).save(
            Customer(
                customer_id=str(customer_id),
                customer_name=name,
                order_type=OrderType.POP,
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


def gather_pop_inputs(source_path: str) -> Optional[dict]:
    """Gather inputs for a Prince of Peace sales confirmation. Returns dict or None."""
    order = parse_pop(source_path)

    print(f"\n{'=' * 64}")
    print(f"Sales Confirmation: {order.client}  →  advertiser {order.advertiser}")
    print(
        f"Estimate:   {order.estimate}   written {order.date_written}   rev {order.revision}   rep {order.station_rep}"
    )
    print(f"Market:     {order.market} ({order.market_text})")
    print(f"Flight:     {order.flight_start} → {order.flight_end}   (month-only lines → Rotation)")
    print("Money:      DIRECT customer, 0% commission — rates enter verbatim")
    if order.notes:
        print(f"Notes:      {order.notes}")
    print("\n  Lines:")
    for ln in order.lines:
        tag = "BNS" if ln.is_bonus else "   "
        rate = f"${ln.rate:.2f}" if ln.rate else "  bonus"
        print(
            f"    {tag} :{ln.length_sec}s {ln.ordered_text:<26} {ln.start_date}–{ln.end_date}  "
            f"{ln.total_spots:>3} spots {rate:>8}  ${ln.line_total:,.2f}"
        )
    print(
        f"\n  Airtime: {sum(ln.total_spots for ln in order.paid_lines)} paid + "
        f"{sum(ln.total_spots for ln in order.bonus_lines)} bonus spots   ${order.net_total:,.2f}"
    )

    # ── Market ──
    raw = input(f"\n  Market [{order.market}]: ").strip().upper()
    market_code = order.market if not raw or raw in ("Y", "YES") else raw
    if market_code not in _MARKETS:
        print("  ✗ Unknown market — aborting")
        return None

    # ── Start date (always asked) ──
    start_override = confirm_start_date(order.flight_start, order.flight_end, always_ask=True)
    if start_override is None:
        print("  ✗ No flight start date — aborting")
        return None

    print(f"\n  Etere lines for a {fmt_mmddyyyy(start_override)} start:")
    _print_plan(order, start_override)

    # ── Customer ──
    from browser_automation.customer_defaults import prompt_customer_id

    print(
        f"\n  Customer is the CLIENT (payer): ANAGRAF {DEFAULT_CUSTOMER_ID} {order.client}; "
        f"the advertiser ({order.advertiser}) is ANAGRAF {ADVERTISER_ANAGRAF_ID} and is NOT the payer."
    )
    raw_id = prompt_customer_id(str(DEFAULT_CUSTOMER_ID))
    if raw_id is None:
        print("  ✗ No customer ID — aborting")
        return None
    customer_id = int(raw_id)
    cust_name = _anagraf_name(customer_id) or order.client
    print(f"  [CUSTOMER] ANAGRAF {customer_id} = {cust_name}")

    # ── Separation / billing from customers.db, else POP history ──
    separation = DEFAULT_SEPARATION
    billing_type = "direct"
    cust = _lookup_customer_db(customer_id)
    if cust:
        billing_type = cust.billing_type or billing_type
        separation = (cust.separation_customer, cust.separation_event, cust.separation_order)

    # ── Code + description (bracket defaults, POP history: 'POP KLO 2609' / 'Kwan Loong Oil 2609') ──
    yymm = broadcast_yymm(start_override)
    code_prefix = (
        cust.code_name if cust and getattr(cust, "code_name", "") else ""
    ) or DEFAULT_CODE_PREFIX
    default_code = f"{code_prefix} {yymm}"
    desc_prefix = (
        cust.description_name if cust and getattr(cust, "description_name", "") else ""
    ) or order.advertiser
    default_desc = f"{desc_prefix} {yymm}"
    print()
    raw = input(f"  Contract code [{default_code}]: ").strip()
    contract_code = raw or default_code
    raw = input(f"  Description [{default_desc}]: ").strip()
    description = raw or default_desc

    _upsert_customer_db(
        cust_name,
        customer_id,
        code_name=code_prefix,
        description_name=desc_prefix,
        separation=separation,
        billing_type=billing_type,
        market=market_code,
    )

    return {
        "customer_id": customer_id,
        "billing_type": billing_type,
        "separation": separation,
        "contract_code": contract_code,
        "description": description,
        "start_date_override": fmt_mmddyyyy(start_override),
        "market": market_code,
    }


# ─── Direct DB entry ──────────────────────────────────────────────────────────


def _create_pop_contract(order: POPOrder, inputs: dict) -> Optional[str]:
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = inputs.get("customer_id")
    if customer_id is None:
        print("[POP] ✗ No customer_id")
        return None
    separation = tuple(inputs.get("separation", DEFAULT_SEPARATION))
    billing_type = inputs.get("billing_type", "direct")
    contract_code = inputs.get("contract_code") or DEFAULT_CODE_PREFIX
    description = inputs.get("description") or order.description
    market_code = inputs.get("market") or order.market
    dry_run = bool(inputs.get("dry_run"))

    override = inputs.get("start_date_override")
    flight_start_d = parse_date(override) if override else parse_date(order.flight_start)
    flight_end_d = parse_date(order.flight_end)

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(
            conn, owner=order.station_rep or "Charmaine Lane", autocommit=False
        )
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
            allow_rename=True,
        )
        print(f"[POP] ✓ Contract header: ID={contract_id}  code='{contract_code}'")

        line_count = 0
        for p in _line_plan(order, flight_start_d):
            ln = p["line"]
            line_count += 1
            print(
                f"  [LINE {line_count}] {market_code} {p['description']}: {p['days']} {p['time_range']} "
                f"{fmt_mmddyyyy(p['date_from'])}–{fmt_mmddyyyy(p['date_to'])} "
                f"{p['total_spots']} spots :{ln.length_sec}s rate={ln.rate} max {p['max_daily']}/day  Rotation"
            )
            client.add_contract_line(
                market=market_code,
                days=p["days"],
                time_range=p["time_range"],
                description=p["description"],
                rate=ln.rate,
                total_spots=p["total_spots"],
                spots_per_week=0,  # month-only → Rotation (universal rule)
                max_daily_run=p["max_daily"],
                date_from=p["date_from"],
                date_to=p["date_to"],
                duration=str(ln.length_sec),
                is_bonus=p["is_bonus"],
                booking_code=10 if p["is_bonus"] else 2,
                separation_intervals=separation,
            )

        if dry_run:
            conn.rollback()
            conn.close()
            print(f"[POP] DRY RUN — {line_count} lines written and rolled back.")
            return contract_code
        conn.commit()
        conn.close()
        print(f"[POP] ✓ {line_count} lines committed.")
        return contract_code

    except Exception as exc:
        print(f"[POP] ✗ {exc}")
        import traceback

        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


def run_pop_order(order: POPOrder, inputs: dict) -> list[tuple[str, bool]]:
    """One contract per confirmation. Returns [(contract_code, success)]."""
    code = _create_pop_contract(order, inputs)
    label = inputs.get("contract_code") or DEFAULT_CODE_PREFIX
    return [(label, code is not None)]
