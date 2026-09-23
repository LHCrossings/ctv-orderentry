"""IW Group / Covered California — gather + direct-DB entry.

Conventions (Lee, 2026-09-23, and the hand-entered oracle IW CCA 2510 C/V =
contracts 2167/2168):
  * customer = ANAGRAF 386 "Covered California"; agency = ANAGRAF 12 "IW Group, Inc."
    (Commissione 15%). ANAGRAF wins via lookup_customer_defaults; AGENCY_IDS["IW"]
    is only the fallback.
  * the IO is NET → every paid rate is grossed up by the agency commission before
    entry (intertrend pattern: net / (1 - fee), rounded to cents). The backwrite
    gets the NET rate + rates_are_net and grosses at full precision.
  * market is PROMPTED (default CVC; "KBTV" on the IO also means CVC) — Lee: the
    IOs do not reliably say which market they are for.
  * AV lines enter as BNS (booking code 10), never as AV.
  * separation (15, 0, 0).
  * OrderNo → Customer Order Ref; IO Campaign + Description → contract notes.
  * contract code 'IW CCA <yymm> <C|V|T>' (Chinese / Viet / Filipino), description
    'Covered CA Brand Awareness <yymm> <Chinese|Viet|Filipino>'.
  * paid lines Rotation like the 2510 oracle; equal consecutive weeks consolidate.
"""

from __future__ import annotations

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
)
from browser_automation.parsers.iwcca_parser import IWCCAOrder, parse_iwcca

DEFAULT_CUSTOMER_ID = 386
DEFAULT_CODE_PREFIX = "IW CCA"
DEFAULT_DESC_PREFIX = "Covered CA Brand Awareness"
DEFAULT_SEPARATION = (15, 0, 0)
DEFAULT_MARKET = "CVC"
DEFAULT_AGENCY_FEE = 15.0
ROTATION = 1
_MARKETS = ("CVC", "SFO", "LAX", "SEA", "HOU", "CMP", "WDC", "NYC", "MMT", "DAL")
_CODE_LETTER = {"Chinese": "C", "Vietnamese": "V", "Filipino": "T", "Korean": "K", "Hmong": "H"}
_DESC_LANG = {"Vietnamese": "Viet"}


def gross_rate(net: float, fee_pct: float) -> float:
    """Net per-spot rate → the gross Etere stores (rounded to cents, intertrend rule)."""
    if not net:
        return 0.0
    return round(float(net) / (1.0 - fee_pct / 100.0), 2)


# ─── Planner (single source of truth for preview AND writer) ─────────────────


def _line_plan(order: IWCCAOrder, start_from: date, fee_pct: float) -> list[dict]:
    """IO line → consolidated week ranges → Etere lines, with the late-start split
    (a truncated first week earns its own cap/line — `plan_ranges`)."""
    flight_end = parse_date(order.flight_end)
    week_cols = [WeekCol(parse_date(d)) for d in order.week_start_dates]
    plan: list[dict] = []
    for ln in order.lines:
        if ln.total_spots == 0:
            continue
        days, _ = EtereClient.check_sunday_6_7a_rule(ln.days, ln.time)
        time_from, time_to = EtereClient.parse_time_range(ln.time)
        consolidated = EtereClient.consolidate_weeks(
            ln.weekly_spots, week_cols, order.flight_end, order.flight_start
        )
        ranges, notes = plan_ranges(consolidated, days, start_from, flight_end)
        rate = 0.0 if ln.is_bonus else gross_rate(ln.net_rate, fee_pct)
        for rng in ranges:
            plan.append(
                {
                    "line": ln,
                    "days": days,
                    "time_range": f"{time_from}-{time_to}",
                    "description": ln.description[:60],
                    "date_from": rng["date_from"],
                    "date_to": rng["date_to"],
                    "spots_per_week": rng["spots_per_week"],
                    "weeks": rng["weeks"],
                    "total_spots": rng["spots_per_week"] * rng["weeks"],
                    "max_daily": rng["max_daily"],
                    "tag": rng["tag"],
                    "notes": notes,
                    "is_bonus": ln.is_bonus,
                    "rate": rate,
                }
            )
    return plan


def _print_plan(plan: list[dict]) -> None:
    seen_notes: set[str] = set()
    for p in plan:
        for n in p["notes"]:
            if n not in seen_notes:
                seen_notes.add(n)
                print(f"    [NOTE] {p['description']}: {n}")
        rate = f"${p['rate']:.2f} gross" if p["rate"] else "bonus"
        tag = f"  ← {p['tag']}" if p["tag"] else ""
        print(
            f"    {p['description']:<34} {p['time_range']}  "
            f"{fmt_mmddyyyy(p['date_from'])}–{fmt_mmddyyyy(p['date_to'])}  "
            f"{p['spots_per_week']:>2}/wk × {p['weeks']:>2}w = {p['total_spots']:>3}  "
            f"max {p['max_daily']}/day  {rate:>13}  Rotation{tag}"
        )


def _plan_totals(order: IWCCAOrder, plan: list[dict]) -> tuple[int, int]:
    return sum(p["total_spots"] for p in plan), order.total_spots


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
                order_type=OrderType.IWCCA,
                billing_type="agency",
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


def gather_iwcca_inputs(source_path: str) -> Optional[dict]:
    """Gather inputs for one IW Group / Covered California IO. Returns dict or None."""
    order = parse_iwcca(source_path)

    print(f"\n{'=' * 64}")
    print(
        f"IW Group TELEVISION ORDER  OrderNo {order.order_no}  dated {order.io_date}  → {order.language}"
    )
    print(
        f"Station:    {order.station}"
        + (f"   (→ {order.market_hint})" if order.market_hint else "   (market not stated)")
    )
    print(
        f"Flight:     {order.flight_start} → {order.flight_end}   ({order.weeks_stated} weeks stated, {len(order.week_start_dates)} week columns)"
    )
    print(f"Campaign:   {order.campaign}")
    print(f"Descr:      {order.description_io}")
    print(
        "Money:      NET rates — grossed up by the agency commission at entry; AV lines enter as BNS"
    )
    print("\n  IO lines (net):")
    for ln in order.lines:
        tag = "BNS" if ln.is_bonus else "   "
        rate = f"${ln.net_rate:.2f}" if ln.net_rate else "  bonus"
        print(
            f"    {tag} {ln.description:<34} {ln.weekly_spots}  = {ln.total_spots:>3} {rate:>8}  ${ln.net_total:,.2f}"
        )
    print(
        f"\n  Airtime: {sum(ln.total_spots for ln in order.paid_lines)} paid + "
        f"{sum(ln.total_spots for ln in order.bonus_lines)} bonus spots   ${order.total_net:,.2f} net"
    )

    # ── Market (always confirmed — Lee) ──
    default_market = order.market_hint or DEFAULT_MARKET
    raw = input(f"\n  Market [{default_market}]: ").strip().upper()
    market_code = default_market if not raw or raw in ("Y", "YES") else raw
    if market_code not in _MARKETS:
        print("  ✗ Unknown market — aborting")
        return None

    # ── Customer + agency fee from ANAGRAF ──
    from browser_automation.customer_defaults import prompt_customer_id

    raw_id = prompt_customer_id(str(DEFAULT_CUSTOMER_ID))
    if raw_id is None:
        print("  ✗ No customer ID — aborting")
        return None
    customer_id = int(raw_id)
    anag = _anagraf_defaults(customer_id)
    cust_name = anag.get("name") or "Covered California"
    print(
        f"  [CUSTOMER] ANAGRAF {customer_id} = {cust_name}; agency "
        f"{anag.get('agency_id') or '?'} {anag.get('agency_name') or '(none linked)'} @ {anag.get('agency_pct', 0):.0f}%"
    )
    fee_default = anag.get("agency_pct") or DEFAULT_AGENCY_FEE
    raw = input(f"  Agency commission % for the gross-up [{fee_default:g}]: ").strip()
    try:
        fee_pct = float(raw) if raw and raw.lower() not in ("y", "yes") else float(fee_default)
    except ValueError:
        print("  ✗ Commission must be a number — aborting")
        return None
    if not 0 <= fee_pct < 100:
        print("  ✗ Commission out of range — aborting")
        return None

    # ── Start date (asked only when the IO is late) ──
    start_override = confirm_start_date(order.flight_start, order.flight_end)
    if start_override is None:
        print("  ✗ No flight start date — aborting")
        return None

    plan = _line_plan(order, start_override, fee_pct)
    planned, ordered = _plan_totals(order, plan)
    print(
        f"\n  Etere lines for a {fmt_mmddyyyy(start_override)} start (gross = net ÷ {1 - fee_pct / 100:.2f}):"
    )
    _print_plan(plan)
    print(f"  {planned} of {ordered} ordered spots will be entered.")
    if planned != ordered:
        raw = input("  Enter anyway? [y/N]: ").strip().lower()
        if raw not in ("y", "yes"):
            print("  ✗ Aborting")
            return None

    # ── Separation from customers.db, else (15, 0, 0) ──
    separation = DEFAULT_SEPARATION
    cust = _lookup_customer_db(customer_id)
    if cust and cust.separation_customer is not None:
        separation = (cust.separation_customer, cust.separation_event, cust.separation_order)

    # ── Code + description (bracket defaults) ──
    yymm = broadcast_yymm(start_override)
    letter = _CODE_LETTER.get(order.language, (order.language[:1] or "X").upper())
    code_prefix = (
        cust.code_name if cust and getattr(cust, "code_name", "") else ""
    ) or DEFAULT_CODE_PREFIX
    desc_prefix = (
        cust.description_name if cust and getattr(cust, "description_name", "") else ""
    ) or DEFAULT_DESC_PREFIX
    default_code = f"{code_prefix} {yymm} {letter}"
    default_desc = f"{desc_prefix} {yymm} {_DESC_LANG.get(order.language, order.language)}"
    print()
    raw = input(f"  Contract code [{default_code}]: ").strip()
    contract_code = raw or default_code
    raw = input(f"  Description [{default_desc}]: ").strip()
    description = raw or default_desc
    print(f"  Customer Order Ref: {order.order_no}   Notes: {order.notes}")

    _upsert_customer_db(
        cust_name,
        customer_id,
        code_name=code_prefix,
        description_name=desc_prefix,
        separation=separation,
        market=market_code,
    )

    return {
        "customer_id": customer_id,
        "billing_type": "agency",
        "separation": separation,
        "contract_code": contract_code,
        "description": description,
        "start_date_override": fmt_mmddyyyy(start_override),
        "market": market_code,
        "agency_fee_pct": fee_pct,
        "language": order.language,
    }


# ─── Direct DB entry ──────────────────────────────────────────────────────────


def _create_iwcca_contract(order: IWCCAOrder, inputs: dict) -> Optional[str]:
    from browser_automation.etere_direct_client import AGENCY_IDS, EtereDirectClient, connect

    customer_id = inputs.get("customer_id")
    if customer_id is None:
        print("[IWCCA] ✗ No customer_id")
        return None
    separation = tuple(inputs.get("separation", DEFAULT_SEPARATION))
    billing_type = inputs.get("billing_type", "agency")
    contract_code = inputs.get("contract_code") or f"{DEFAULT_CODE_PREFIX} {order.order_no}"
    description = inputs.get("description") or order.campaign
    market_code = inputs.get("market") or order.market_hint or DEFAULT_MARKET
    fee_pct = float(inputs.get("agency_fee_pct", DEFAULT_AGENCY_FEE))
    dry_run = bool(inputs.get("dry_run"))

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
            # ANAGRAF for the client decides the agency + commission (386 → IW Group 12,
            # 15%); AGENCY_IDS is the fallback for a client with no agency linked.
            agency_id=AGENCY_IDS["IW"],
            lookup_customer_defaults=True,
            contract_date=flight_start_d,
            contract_end_date=flight_end_d,
            contract_type=1,
            billing_type=billing_type,
            note=order.notes,
            customer_order_ref=order.order_no,
            allow_rename=True,
        )
        print(
            f"[IWCCA] ✓ Contract header: ID={contract_id}  code='{contract_code}'  "
            f"ref={order.order_no}  notes='{order.notes[:60]}'"
        )

        line_count = 0
        for p in _line_plan(order, flight_start_d, fee_pct):
            ln = p["line"]
            line_count += 1
            print(
                f"  [LINE {line_count}] {market_code} {p['description']}: {p['days']} {p['time_range']} "
                f"{fmt_mmddyyyy(p['date_from'])}–{fmt_mmddyyyy(p['date_to'])} "
                f"({p['spots_per_week']}/wk×{p['weeks']}w={p['total_spots']}) :{ln.length_sec}s "
                f"rate={p['rate']} max {p['max_daily']}/day"
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
                duration=str(ln.length_sec),
                is_bonus=p["is_bonus"],
                booking_code=10 if p["is_bonus"] else 2,
                separation_intervals=separation,
                # Rotation on the paid lines too (Lee 9/23, matches the 2510 oracle);
                # bonus lines are Rotation by the central rule regardless.
                scheduling_type=None if p["is_bonus"] else ROTATION,
            )

        if dry_run:
            conn.rollback()
            conn.close()
            print(f"[IWCCA] DRY RUN — {line_count} lines written and rolled back.")
            return contract_code
        conn.commit()
        conn.close()
        print(f"[IWCCA] ✓ {line_count} lines committed.")
        return contract_code

    except Exception as exc:
        print(f"[IWCCA] ✗ {exc}")
        import traceback

        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


def run_iwcca_order(order: IWCCAOrder, inputs: dict) -> list[tuple[str, bool]]:
    """One contract per IO. Returns [(contract_code, success)]."""
    code = _create_iwcca_contract(order, inputs)
    label = inputs.get("contract_code") or f"{DEFAULT_CODE_PREFIX} {order.order_no}"
    return [(label, code is not None)]
