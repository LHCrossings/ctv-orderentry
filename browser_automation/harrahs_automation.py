"""
Harrah's Northern California direct-DB automation (house proposal, Central Valley).

Harrah's Northern California is a DIRECT client: ANAGRAF 485, no agency, 0%
commission, CENTROMEDIA 316 Broadcast. The sheet's PROMO rate is the entered
rate; with no commission gross == net.

**Monthly estimate → one Rotation line per row per BROADCAST MONTH** (Lee
2026-10-30; the 2023 Harrah's SoCal precedent, contract 833): every count on
the sheet is per month, the sheet names only "6 months contract", so the gather
asks for the start date and the month count. The first month runs from the
chosen start to the day before the next broadcast month; every later month is
a whole broadcast month (Monday of the week containing the 1st → the Sunday
before the next). 11/2/2026 + 6 months = Nov..Apr, ending 4/25/2027.

Lines: `spots_per_week=0` (month-only → Rotation, the universal rule),
`max_daily = ceil(monthly / active days in the month)`. A BONUS row enters at
$0 / booking code 10; a bonus row WITH its own daypart (Hmong Sa-Su 6p-8p)
keeps that daypart, a "ROS" bonus row takes the language's house ROS window
(`ROS_SCHEDULES`; Mandarin → the Chinese window).

Naming (Lee 2026-10-30): code `Harrahs NoCal <yymm>`, description
`Harrah's Northern CA <yymm>-<yymm>`. Separation 25/0/0 (Lee), the customer
DB row when present.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Optional

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
from browser_automation.etere_client import EtereClient
from browser_automation.line_planner import (
    active_days,
    broadcast_yymm,
    fmt_mmddyyyy,
    mon_of_week_with_first,
    parse_date,
)
from browser_automation.parsers.harrahs_parser import (
    CLIENT_NAME,
    HarrahsLine,
    HarrahsOrder,
    parse_harrahs,
)
from browser_automation.ros_definitions import ROS_SCHEDULES

DEFAULT_CUSTOMER_ID = 485  # ANAGRAF: Harrah's Northern California (direct, 0%)
DEFAULT_MARKET = "CVC"
DEFAULT_SEPARATION = (25, 0, 0)  # customer, order, event — Lee 2026-10-30
DEFAULT_CODE_PREFIX = "Harrahs NoCal"
DEFAULT_DESC_PREFIX = "Harrah's Northern CA"
DEFAULT_MONTHS = 6
MEDIA_CENTER = {"broadcast": 316, "calendar": 317}  # CONTRATTITESTATA.CENTROMEDIA
_MARKETS = ("CVC", "SFO", "LAX", "SEA", "HOU", "CMP", "WDC", "NYC", "MMT", "DAL")
_SHORT_LANG = {"Vietnamese": "Viet"}
_ROS_FALLBACK = {"Mandarin": "Chinese"}  # no house Mandarin ROS window
_LABEL = "[HARRAHS]"


# ─── Planning ─────────────────────────────────────────────────────────────────


def _short_time(hhmm: str) -> str:
    """'19:00' -> '7p', '23:59' -> '12a', '08:00' -> '8a', '13:30' -> '1:30p'."""
    h, m = (int(x) for x in hhmm.split(":"))
    if hhmm == "23:59":
        return "12a"
    suffix = "a" if h < 12 else "p"
    h12 = h % 12 or 12
    return f"{h12}{suffix}" if m == 0 else f"{h12}:{m:02d}{suffix}"


def next_broadcast_month_start(d: date) -> date:
    """Monday that opens the broadcast month AFTER the one containing `d`."""
    yymm = broadcast_yymm(d)
    y, m = 2000 + int(yymm[:2]), int(yymm[2:])
    ny, nm = (y, m + 1) if m < 12 else (y + 1, 1)
    return mon_of_week_with_first(ny, nm)


def month_ranges(start: date, months: int) -> list[tuple[date, date, str]]:
    """[(date_from, date_to, yymm)] — the first range opens on `start`, every
    later one on a broadcast-month Monday; each closes the day before the next
    broadcast month. 11/2/2026 × 6 → 11/2-11/29, 11/30-12/27, 12/28-1/31,
    2/1-2/28, 3/1-3/28, 3/29-4/25."""
    if months < 1:
        raise ValueError("months must be >= 1")
    out: list[tuple[date, date, str]] = []
    cur = start
    for _ in range(months):
        nxt = next_broadcast_month_start(cur)
        out.append((cur, nxt - timedelta(days=1), broadcast_yymm(cur)))
        cur = nxt
    return out


def line_window(ln: HarrahsLine) -> tuple[str, str, str]:
    """(days, 'HH:MM-HH:MM', compact label). A ROS bonus row takes the
    language's house ROS window; any row with its own daypart keeps it."""
    if ln.is_bonus and ln.is_ros:
        lang = ln.language or ""
        ros = ROS_SCHEDULES.get(lang) or ROS_SCHEDULES.get(_ROS_FALLBACK.get(lang, ""))
        if not ros:
            raise ValueError(f"{ln.language_block}: no house ROS window for {lang!r}")
        days, time_raw = ros["days"], ros["time"]
    else:
        days, time_raw = ln.days, ln.time_text
        days, _ = EtereClient.check_sunday_6_7a_rule(days, time_raw)
    t_from, t_to = EtereClient.parse_time_range(time_raw)
    return days, f"{t_from}-{t_to}", f"{_short_time(t_from)}-{_short_time(t_to)}"


def line_description(ln: HarrahsLine, days: str, label: str) -> str:
    """'M-F 7p-9p Chinese News/Talk', 'Sa-Su 9p-12a Mandarin Entertainment',
    'BNS Sa-Su 6p-8p Hmong', 'BNS Viet ROS'."""
    lang = _SHORT_LANG.get(ln.language, ln.language)
    if ln.is_bonus and ln.is_ros:
        return f"BNS {lang} ROS"
    core = f"{days} {label} {lang} {ln.program_short}".strip()
    core = " ".join(core.split())
    return (f"BNS {core}" if ln.is_bonus else core)[:60]


def order_plan(order: HarrahsOrder, start: date, months: int) -> list[dict]:
    """One dict per Etere line (row × broadcast month) — the single source of
    truth the gather preview and the entry both walk."""
    from browser_automation.etere_direct_client import parse_day_bits

    plan: list[dict] = []
    ranges = month_ranges(start, months)
    for ln in order.lines:
        if ln.monthly_spots == 0:
            continue
        days, time_range, label = line_window(ln)
        desc = line_description(ln, days, label)
        bits = parse_day_bits(days)
        for date_from, date_to, yymm in ranges:
            n_days = active_days(date_from, date_to, bits)
            if n_days <= 0:
                raise ValueError(f"{desc}: no {days} days between {date_from} and {date_to}")
            plan.append(
                {
                    "line": ln,
                    "days": days,
                    "time_range": time_range,
                    "description": desc,
                    "date_from": date_from,
                    "date_to": date_to,
                    "yymm": yymm,
                    "total_spots": ln.monthly_spots,
                    "max_daily": max(1, math.ceil(ln.monthly_spots / n_days)),
                    "active_days": n_days,
                    "is_bonus": ln.is_bonus,
                }
            )
    return plan


def default_code_desc(start: date, months: int, cust=None) -> tuple[str, str, str]:
    """Lee 2026-10-30: 'Harrahs NoCal 2611' / "Harrah's Northern CA 2611-2704".
    Returns (code, description, customer_ref)."""
    first = broadcast_yymm(start)
    last = month_ranges(start, months)[-1][2]
    prefix = (
        cust.code_name if cust and getattr(cust, "code_name", "") else ""
    ) or DEFAULT_CODE_PREFIX
    desc_prefix = (
        cust.description_name if cust and getattr(cust, "description_name", "") else ""
    ) or DEFAULT_DESC_PREFIX
    return f"{prefix} {first}", f"{desc_prefix} {first}-{last}", ""


def default_start(today: Optional[date] = None) -> date:
    """The next Monday on or after today — these contracts open on a week."""
    today = today or date.today()
    return today + timedelta(days=(7 - today.weekday()) % 7)


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
                order_type=OrderType.HARRAHS,
                billing_type=billing_type,
                code_name=DEFAULT_CODE_PREFIX,
                description_name=DEFAULT_DESC_PREFIX,
                separation_customer=separation[0],
                separation_order=separation[1],
                separation_event=separation[2],
                default_market=market,
            )
        )
    except Exception as exc:
        print(f"[CUSTOMER] customer DB upsert failed (non-fatal): {exc}")


def _anagraf_lookup() -> Optional[dict]:
    """ANAGRAF row for Harrah's NORTHERN California (223 is the SoCal resort)."""
    try:
        from browser_automation.etere_direct_client import connect

        conn = connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT ID_ANAGRAF, RAG_SOCIAL, AGENZIA, Commissione, CENTROMEDIA FROM ANAGRAF "
            "WHERE RAG_SOCIAL LIKE %s AND RAG_SOCIAL LIKE %s ORDER BY ID_ANAGRAF",
            ("%Harrah%", "%North%"),
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


def _print_plan(plan: list[dict]) -> int:
    entered = 0
    last_desc = None
    for p in plan:
        if p["description"] != last_desc:
            print(
                f"      {p['description']:<38} {p['days']:<5} {p['time_range']}  "
                f"{p['total_spots']}/mo"
            )
            last_desc = p["description"]
        entered += p["total_spots"]
        print(
            f"          {p['yymm']}  {fmt_mmddyyyy(p['date_from'])}–{fmt_mmddyyyy(p['date_to'])}"
            f"  {p['total_spots']} spots  max {p['max_daily']}/day  ({p['active_days']} days)"
        )
    return entered


def _ask_int(prompt: str, default: int) -> Optional[int]:
    raw = input(f"{prompt} [{default}]: ").strip()
    if not raw or raw.lower() in ("y", "yes"):
        return default
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def gather_harrahs_inputs(source_path: str) -> Optional[dict]:
    """Gather inputs for a Harrah's Northern California proposal. Returns dict or None."""
    order = parse_harrahs(source_path)

    print(f"\n{'=' * 64}")
    print(f"Client:     {order.client}   (direct — no agency, 0% commission)   {order.option}")
    print(f"Contact:    {order.contact}  {order.email}")
    print(f"Market:     {order.market_code or '?'}   ({order.market_label})")
    print(
        f"Flight:     {order.flight_label} — {order.months or '?'} months per the sheet"
        f"   :{order.length_sec}s   proposal date {order.proposal_date}"
    )
    print("Money:      PROMO rates entered verbatim (direct 0% ⇒ gross == net)")
    print("\n  Rows (per month):")
    for ln in order.lines:
        days, _tr, label = line_window(ln)
        tag = "BNS" if ln.is_bonus else "   "
        rate = f"${ln.rate:.2f}" if ln.rate else "  bonus"
        print(
            f"    {tag} {line_description(ln, days, label):<38} {days:<5} {label:<10}"
            f" {rate:>8}  {ln.monthly_spots:>3}/mo"
        )
    print(
        f"\n  Per month: {order.paid_spots_per_month} paid + {order.bonus_spots_per_month} bonus"
        f"   ${order.net_per_month:,.2f} net"
    )
    for n in order.notes:
        print(f"    {n}")

    market_code = order.market_code or DEFAULT_MARKET
    raw = input(f"\n  Market [{market_code}]: ").strip().upper()
    market_code = raw or market_code
    if market_code not in _MARKETS:
        print("  ✗ Unknown market — aborting")
        return None

    # ── Customer ──
    resolved = _anagraf_lookup()
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

    # ── Separation / billing (customer DB, else Lee's 25/0/0) ──
    separation = DEFAULT_SEPARATION
    billing_type = "direct"
    cust = _lookup_customer_db(cust_name) or _lookup_customer_db(CLIENT_NAME)
    if cust:
        billing_type = cust.billing_type or billing_type
        separation = (cust.separation_customer, cust.separation_order, cust.separation_event)
    else:
        print(f"\n  Separation default {DEFAULT_SEPARATION} (Lee 10/30; customer DB has no row)")

    anagraf_cycle = "Calendar" if resolved and resolved["media_center"] == 317 else "Broadcast"
    raw = input(f"  Billing cycle (Broadcast/Calendar) [{anagraf_cycle}]: ").strip().title()
    cycle = raw or anagraf_cycle
    if cycle.lower() not in MEDIA_CENTER:
        print("  ✗ Billing cycle must be Broadcast or Calendar — aborting")
        return None
    media_center_id = MEDIA_CENTER[cycle.lower()]

    # ── Flight: start date + month count (the sheet states neither date) ──
    start_default = default_start()
    while True:
        raw = input(f"  Flight start date [{fmt_mmddyyyy(start_default)}]: ").strip()
        if not raw or raw.lower() in ("y", "yes"):
            start = start_default
            break
        try:
            start = parse_date(raw)
            break
        except ValueError:
            print(f"    ✗ Could not read '{raw}' — use M/D/YY or MM/DD/YYYY.")
    months = _ask_int("  Number of months", order.months or DEFAULT_MONTHS)
    if months is None:
        print("  ✗ Months must be a positive whole number — aborting")
        return None
    plan = order_plan(order, start, months)
    ranges = month_ranges(start, months)
    print(
        f"\n    Etere lines: {len(plan)} ({len(order.lines)} rows × {months} broadcast months,"
        f" {fmt_mmddyyyy(ranges[0][0])}–{fmt_mmddyyyy(ranges[-1][1])}), all Rotation:"
    )
    entered = _print_plan(plan)
    ordered = (order.paid_spots_per_month + order.bonus_spots_per_month) * months
    print(
        f"    Spots: {entered} entered of {ordered} ordered"
        f"   ${order.net_per_month * months:,.2f} net over {months} months"
        f"{'' if entered == ordered else '  ← MISMATCH'}"
    )
    if entered != ordered:
        print("  ✗ Plan does not match the sheet — aborting")
        return None

    default_code, default_desc, default_ref = default_code_desc(start, months, cust)
    raw = input(f"  Contract code [{default_code}]: ").strip()
    code = raw or default_code
    raw = input(f"  Description [{default_desc}]: ").strip()
    desc = raw or default_desc
    raw = input(f"  Customer order ref [{default_ref or '-'}] ('-' = none): ").strip()
    customer_ref = "" if raw == "-" else (raw or default_ref)

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
        "months": months,
    }


# ─── Direct DB entry ──────────────────────────────────────────────────────────


def _create_harrahs_contract(
    order: HarrahsOrder, inputs: dict, dry_run: bool = False
) -> Optional[str]:
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = inputs.get("customer_id")
    market_code = inputs.get("market") or order.market_code or DEFAULT_MARKET
    if customer_id is None or not market_code or not inputs.get("start_date_override"):
        print(f"{_LABEL} ✗ No customer_id / market / start date")
        return None
    separation = tuple(inputs.get("separation") or DEFAULT_SEPARATION)
    billing_type = inputs.get("billing_type", "direct")
    media_center_id = int(inputs.get("media_center_id") or MEDIA_CENTER["broadcast"])
    code = inputs["contract_code"]
    description = inputs["description"]
    customer_ref = inputs.get("customer_ref", "")
    start = parse_date(inputs["start_date_override"])
    months = int(inputs.get("months") or order.months or DEFAULT_MONTHS)
    plan = order_plan(order, start, months)
    end = month_ranges(start, months)[-1][1]
    expected_spots = sum(p["total_spots"] for p in plan)

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

        line_count = 0
        for p in plan:
            ln = p["line"]
            line_count += 1
            print(
                f"  [LINE {line_count}] {market_code} {p['description']}: {p['days']} "
                f"{p['time_range']} {fmt_mmddyyyy(p['date_from'])}–{fmt_mmddyyyy(p['date_to'])} "
                f"{p['total_spots']} spots :{ln.length_sec}s rate={ln.rate} "
                f"max {p['max_daily']}/day  Rotation"
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

        # Read back header + lines inside the transaction before committing.
        cur = conn.cursor()
        cur.execute(
            "SELECT P_AGENZIA, AGENZIA, CENTROMEDIA, CUSTOMERREF FROM CONTRATTITESTATA "
            "WHERE ID_CONTRATTITESTATA = %s",
            (int(contract_id),),
        )
        row = cur.fetchone()
        if row is None or abs(float(row[0] or 0)) > 0.001 or int(row[1] or 0) != 0:
            raise RuntimeError(
                f"header readback P_AGENZIA={row and row[0]} AGENZIA={row and row[1]} "
                "— expected 0 / none (direct); rolling back"
            )
        if int(row[2] or 0) != media_center_id:
            raise RuntimeError(
                f"header CENTROMEDIA={row[2]} but {media_center_id} was chosen; rolling back"
            )
        cur.execute(
            "SELECT COUNT(*), ISNULL(SUM(N_PASSAGGI),0), "
            "SUM(CASE WHEN PRENOTAZIONE=1 THEN 1 ELSE 0 END), "
            "ISNULL(SUM(N_PASSAGGI*IMPORTO),0) "
            "FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA = %s",
            (int(contract_id),),
        )
        n, spots, rot, money = cur.fetchone()
        if int(n) != line_count or int(spots) != expected_spots or int(rot) != line_count:
            raise RuntimeError(
                f"line readback {n} lines / {spots} spots / {rot} rotation, expected "
                f"{line_count} / {expected_spots} / {line_count}; rolling back"
            )
        expected_money = round(order.net_per_month * months, 2)
        if abs(float(money) - expected_money) > 0.011:
            raise RuntimeError(
                f"line readback Σ N_PASSAGGI×IMPORTO {float(money):,.2f} != "
                f"{expected_money:,.2f}; rolling back"
            )
        if dry_run:
            conn.rollback()
            conn.close()
            print(
                f"{_LABEL} DRY RUN: {line_count} lines, {spots} spots, ${float(money):,.2f} "
                f"in txn, CENTROMEDIA={row[2]}, ref={row[3]!r} — ROLLED BACK"
            )
            return code
        conn.commit()
        conn.close()
        print(
            f"{_LABEL} ✓ {line_count} lines / {spots} spots / ${float(money):,.2f} "
            f"committed on {code} (ID {contract_id}, CENTROMEDIA {row[2]})."
        )
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


def run_harrahs_order(order: HarrahsOrder, inputs: dict) -> list[tuple[str, bool]]:
    """One contract per proposal. Returns [(code, success)]."""
    code = _create_harrahs_contract(order, inputs)
    return [(inputs.get("contract_code") or code or "", code is not None)]


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Harrah's NoCal proposal → Etere (direct DB)")
    ap.add_argument("xlsx")
    ap.add_argument("--dry-run", action="store_true", help="write in a transaction, roll back")
    ap.add_argument("--start", help="flight start M/D/YYYY (default: next Monday)")
    ap.add_argument("--months", type=int, help="month count (default: the sheet's note)")
    ap.add_argument("--separation", default="25,0,0")
    a = ap.parse_args()
    o = parse_harrahs(a.xlsx)
    st = parse_date(a.start) if a.start else default_start()
    mo = a.months or o.months or DEFAULT_MONTHS
    if a.dry_run:
        c, d, r = default_code_desc(st, mo)
        inputs = {
            "customer_id": DEFAULT_CUSTOMER_ID,
            "billing_type": "direct",
            "media_center_id": MEDIA_CENTER["broadcast"],
            "separation": tuple(int(x) for x in a.separation.split(",")),
            "market": o.market_code or DEFAULT_MARKET,
            "contract_code": c,
            "description": d,
            "customer_ref": r,
            "start_date_override": fmt_mmddyyyy(st),
            "months": mo,
        }
        _create_harrahs_contract(o, inputs, dry_run=True)
    else:
        inp = gather_harrahs_inputs(a.xlsx)
        if inp:
            print(run_harrahs_order(o, inp))
