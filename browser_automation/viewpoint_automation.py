"""
Viewpoint Ads direct-DB automation (political option sheet, SF).

Viewpoint Ads is ANAGRAF 486 (15% commission, Broadcast). The first client is
ANAGRAF 487 "Mayor Lurie Proposition" (AGENZIA 486), created by Lee before the
committee's finished name was known — the sheet prints "Advertiser TBD ( Mayor
Lurie Proposition)". The gather resolves the customer by the sheet's name and
falls back to 487, printing ANAGRAF's CURRENT name so a renamed record is seen.

Money: GROSS rates entered verbatim; `lookup_customer_defaults=True` takes the
agency and commission from ANAGRAF (487 → 486 → 15%). The sheet's own net/gross
ratio is compared with ANAGRAF at gather and the header is read back before
commit (P_AGENZIA == sheet %, AGENZIA set, CENTROMEDIA as chosen) — a mismatch
rolls back rather than billing a different commission than the proposal.

Lines: the sheet has no week columns, so every row is a flight total → ONE
Rotation line per row on the flight window (`scheduling_type=1`, the Polaris
"Yes on 50" 2025 precedent, contract 2198) with max-per-day = ceil(units /
active days of the pattern inside the line's window). "ends at 7am" trims every
line whose window ends after the cutoff to the day before (only the 6a-7a news
airs on election day). The start date is ALWAYS asked (these arrive day-of); a
later start recomputes the caps from the days that remain.

Naming (Polaris political shape, 3132 `Polaris ASC 260929` / `Affordable Santa
Clara 260929-261005`): code `<code prefix> <yymmdd start>`, description
`<description prefix> <yymmdd>-<yymmdd>`; the prefixes come from customers.db
when present (learned from what the operator types) else `Viewpoint Lurie` /
the sheet's advertiser.

Separation: the customer DB row when present, else the house 15/0/0. Option 1
prints "*Note: No separation requirement" and the 2198 precedent carried 25;
both are shown at the orchestrator's separation prompt, the operator decides.
"""

from __future__ import annotations

import re
from datetime import date
from math import ceil
from typing import Optional

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
from browser_automation.etere_client import EtereClient
from browser_automation.line_planner import (
    active_days,
    confirm_start_date,
    fmt_mmddyyyy,
    parse_date,
)
from browser_automation.parsers.viewpoint_parser import (
    AGENCY_NAME,
    ViewpointLine,
    ViewpointOrder,
    parse_viewpoint,
)

AGENCY_ID = 486  # ANAGRAF: Viewpoint Ads (15%)
DEFAULT_CUSTOMER_ID = 487  # ANAGRAF: Mayor Lurie Proposition (AGENZIA 486) — Lee 10/21
DEFAULT_MARKET = "SFO"
DEFAULT_SEPARATION = (15, 0, 0)  # customer, order, event
PRECEDENT_SEPARATION = (25, 0, 0)  # Polaris political lines (2198 Yes on 50, 3132 ASC)
DEFAULT_CODE_PREFIX = "Viewpoint Lurie"
SCHEDULING_ROTATION = 1
MEDIA_CENTER = {"broadcast": 316, "calendar": 317}  # CONTRATTITESTATA.CENTROMEDIA
CODE_WIDTH, DESC_WIDTH = 32, 80  # Etere column widths (Polaris prompt rule)
_MARKETS = ("CVC", "SFO", "LAX", "SEA", "HOU", "CMP", "WDC", "NYC", "MMT", "DAL")
_LABEL = "[VIEWPOINT]"
_CODE_TAIL = re.compile(r"\s*\d{6}\s*$")
_DESC_TAIL = re.compile(r"\s*\d{6}\s*-\s*\d{6}\s*$")


# ─── Planning ─────────────────────────────────────────────────────────────────


def _short_time(hhmm: str) -> str:
    """'19:00' -> '7p', '23:59' -> '12a', '19:30' -> '7:30p'."""
    h, m = (int(x) for x in hhmm.split(":"))
    if hhmm == "23:59":
        return "12a"
    suffix = "a" if h < 12 else "p"
    h12 = h % 12 or 12
    return f"{h12}{suffix}" if m == 0 else f"{h12}:{m:02d}{suffix}"


def line_window(ln: ViewpointLine) -> tuple[str, str, str]:
    """(days, 'HH:MM-HH:MM', compact label) for a line."""
    days, _ = EtereClient.check_sunday_6_7a_rule(ln.days, ln.time_text)
    t_from, t_to = EtereClient.parse_time_range(ln.time_text)
    return days, f"{t_from}-{t_to}", f"{_short_time(t_from)}-{_short_time(t_to)}"


def line_description(ln: ViewpointLine, days: str, label: str) -> str:
    """'M-F 6a-7a Mandarin News', 'Sa-Su 9p-12a Mandarin Entertainment'."""
    tag = "BNS " if ln.is_bonus else ""
    return f"{tag}{days} {label} {ln.program}".strip()[:60]


def _window_minutes(time_range: str) -> int:
    a, b = time_range.split("-")
    ha, ma = (int(x) for x in a.split(":"))
    hb, mb = (int(x) for x in b.split(":"))
    return (hb * 60 + mb) - (ha * 60 + ma)


def order_plan(order: ViewpointOrder, start_from: date) -> list[dict]:
    """One planned Etere line per sheet row — the single source of truth the
    gather preview and the entry both walk, so what is approved is written.

    Each entry: line, days, time_range, description, date_from, date_to,
    active_days, max_daily, spots, notes."""
    from browser_automation.etere_direct_client import parse_day_bits

    plan: list[dict] = []
    for ln in order.lines:
        if ln.total_spots == 0:
            continue
        days, time_range, label = line_window(ln)
        d_from = max(start_from, ln.flight_start)
        d_to = ln.flight_end
        notes: list[str] = []
        n_active = active_days(d_from, d_to, parse_day_bits(days)) if d_from <= d_to else 0
        if n_active == 0:
            notes.append(
                f"{ln.total_spots} spot(s) undeliverable: no {days} day between "
                f"{fmt_mmddyyyy(d_from)} and {fmt_mmddyyyy(d_to)}"
            )
            cap, spots = 0, 0
        else:
            cap = max(1, ceil(ln.total_spots / n_active))
            spots = ln.total_spots
        plan.append(
            {
                "line": ln,
                "days": days,
                "time_range": time_range,
                "description": line_description(ln, days, label),
                "date_from": d_from,
                "date_to": d_to,
                "active_days": n_active,
                "max_daily": cap,
                "spots": spots,
                "notes": notes,
            }
        )
    return plan


def fit_hints(plan: list[dict], separation_customer: int) -> list[str]:
    """Lines whose max-per-day cannot sit inside the window at the customer
    separation ((cap - 1) × minutes > window) — a hint for the separation
    prompt, never a refusal."""
    out: list[str] = []
    for p in plan:
        if p["max_daily"] <= 1 or separation_customer <= 0:
            continue
        need = (p["max_daily"] - 1) * separation_customer
        have = _window_minutes(p["time_range"])
        if need > have:
            out.append(
                f"{p['description']}: {p['max_daily']}/day needs {need} min at "
                f"{separation_customer} min separation, window is {have} min"
            )
    return out


def _yymmdd(d: date) -> str:
    return d.strftime("%y%m%d")


def default_code_desc(order: ViewpointOrder, start: date, cust=None) -> tuple[str, str, str]:
    """Polaris political shape: code '<prefix> <yymmdd>', description
    '<prefix> <yymmdd>-<yymmdd>'. Returns (code, description, customer_ref)."""
    prefix = (
        cust.code_name if cust and getattr(cust, "code_name", "") else ""
    ) or DEFAULT_CODE_PREFIX
    desc_prefix = (
        cust.description_name if cust and getattr(cust, "description_name", "") else ""
    ) or order.advertiser
    code = f"{prefix} {_yymmdd(start)}"
    desc = f"{desc_prefix} {_yymmdd(start)}-{_yymmdd(order.end)}"
    return code[:CODE_WIDTH], desc[:DESC_WIDTH], ""


def learn_prefixes(code: str, description: str) -> tuple[str, str]:
    """Back out the prefixes from what the operator typed, so a corrected name
    becomes the next order's default (Polaris 9/28 lesson)."""
    return _CODE_TAIL.sub("", code.strip()).strip(), _DESC_TAIL.sub("", description.strip()).strip()


def default_header_note(order: ViewpointOrder) -> str:
    parts = [f"{order.option} proposal" if order.option else "Proposal"]
    if order.advertiser_tbd:
        parts.append(f"advertiser printed as TBD ({order.advertiser})")
    if order.end_cutoff:
        parts.append(f"flight ends {order.end_cutoff} on {fmt_mmddyyyy(order.end)}")
    parts.extend(n.lstrip("*").strip() for n in order.notes)
    return "; ".join(parts)[:399]


# ─── Customer DB / ANAGRAF ────────────────────────────────────────────────────


def _lookup_customer_db(name: str):
    try:
        import os

        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.enums import OrderType

        if not os.path.exists(CUSTOMER_DB_PATH):
            return None
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        return repo.find_by_name(name, OrderType.VIEWPOINT) or repo.find_by_name_any_type(name)
    except Exception:
        return None


def _upsert_customer_db(
    name: str,
    customer_id: int,
    separation: tuple,
    billing_type: str,
    market: str,
    code_prefix: str,
    desc_prefix: str,
) -> None:
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        from src.domain.enums import OrderType

        CustomerRepository(CUSTOMER_DB_PATH).save(
            Customer(
                customer_id=str(customer_id),
                customer_name=name,
                order_type=OrderType.VIEWPOINT,
                billing_type=billing_type,
                code_name=code_prefix,
                description_name=desc_prefix,
                separation_customer=separation[0],
                separation_order=separation[1],
                separation_event=separation[2],
                default_market=market,
            )
        )
    except Exception as exc:
        print(f"[CUSTOMER] customer DB upsert failed (non-fatal): {exc}")


def _anagraf_rows(where: str, params: tuple) -> list[dict]:
    try:
        from browser_automation.etere_direct_client import connect

        conn = connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT ID_ANAGRAF, RAG_SOCIAL, AGENZIA, Commissione, CENTROMEDIA FROM ANAGRAF "
            f"WHERE {where} ORDER BY ID_ANAGRAF",
            params,
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as exc:
        print(f"[CUSTOMER] ANAGRAF lookup failed: {exc}")
        return []
    return [
        {
            "id": int(r[0]),
            "name": str(r[1]).strip(),
            "agency": int(r[2] or 0),
            "pct": float(r[3] or 0),
            "media_center": int(r[4] or 0),
        }
        for r in rows
    ]


def _anagraf_by_name(name: str) -> Optional[dict]:
    """First ANAGRAF row whose name starts with the advertiser's first word."""
    first = (name or "").split()[0] if name else ""
    if not first:
        return None
    rows = _anagraf_rows("RAG_SOCIAL LIKE %s", (f"{first}%",))
    exact = [r for r in rows if r["name"].lower() == " ".join(name.split()).lower()]
    return (exact or rows or [None])[0]


def _anagraf_by_id(anagraf_id: int) -> Optional[dict]:
    rows = _anagraf_rows("ID_ANAGRAF = %s", (int(anagraf_id),))
    return rows[0] if rows else None


# ─── Gather ───────────────────────────────────────────────────────────────────


def _print_plan(plan: list[dict]) -> tuple[int, list[str]]:
    entered = 0
    all_notes: list[str] = []
    for p in plan:
        entered += p["spots"]
        print(
            f"      {p['description'][:34]:<34} {p['days']:<5} {p['time_range']}  "
            f"{fmt_mmddyyyy(p['date_from'])}–{fmt_mmddyyyy(p['date_to'])}  "
            f"{p['spots']:>3} spots / {p['active_days']} days  max {p['max_daily']}/day"
        )
        all_notes.extend(f"{p['description'][:30]}: {n}" for n in p["notes"])
    return entered, all_notes


def gather_viewpoint_inputs(source_path: str) -> Optional[dict]:
    """Gather inputs for a Viewpoint Ads option sheet. Returns dict or None to abort."""
    order = parse_viewpoint(source_path)

    print(f"\n{'=' * 64}")
    print(f"Agency:     {order.agency}   (ANAGRAF {AGENCY_ID}, commission from ANAGRAF)")
    adv_tag = "   ⚠ printed as TBD on the sheet" if order.advertiser_tbd else ""
    print(f"Advertiser: {order.advertiser}{adv_tag}")
    print(f"Contact:    {order.contact}  {order.email}  {order.phone}")
    print(f"Sheet:      {order.option or 'proposal'}, prepared by {order.prepared_by or '?'}")
    print(f"Market:     {order.market_code or '?'}")
    cutoff = f"   ends {order.end_cutoff} on the last day" if order.end_cutoff else ""
    print(
        f"Flight:     {fmt_mmddyyyy(order.start)}–{fmt_mmddyyyy(order.end)}{cutoff}   :{order.length_sec}s"
    )
    print(f"            (year from {order.year_source})")
    print(f"Billing:    {order.billing or '— not on the sheet'}")
    print(
        f"Money:      GROSS rates entered verbatim; sheet nets at {order.commission_pct:.2f}%"
        " (ANAGRAF commission applies at entry)"
    )
    print("\n  Lines (flight totals, no week columns → Rotation):")
    for ln in order.lines:
        days, _tr, label = line_window(ln)
        tag = "BNS" if ln.is_bonus else "   "
        print(
            f"    {tag} {line_description(ln, days, label):<38} ${ln.rate:>7.2f} x {ln.total_spots:>3}"
            f" = ${ln.gross_total:>9,.2f}   {fmt_mmddyyyy(ln.flight_start)}–{fmt_mmddyyyy(ln.flight_end)}"
        )
    for w in order.warnings:
        print(f"    ⚠ {w}")
    if order.notes:
        print("\n  Sheet notes:")
        for n in order.notes:
            print(f"    {n}")
    print(
        f"\n  Order: {order.total_spots} spots   ${order.gross_airtime:,.2f} gross"
        f" / ${order.net_airtime:,.2f} net"
    )

    market_code = order.market_code or DEFAULT_MARKET
    raw = input(f"  Market [{market_code}]: ").strip().upper()
    market_code = raw or market_code
    if market_code not in _MARKETS:
        print("  ✗ Unknown market — aborting")
        return None

    # ── Customer: the sheet's name, else the record Lee created (printed by its CURRENT name) ──
    resolved = _anagraf_by_name(order.advertiser) or _anagraf_by_id(DEFAULT_CUSTOMER_ID)
    default_id = str(resolved["id"]) if resolved else str(DEFAULT_CUSTOMER_ID)
    if resolved:
        print(
            f"\n[CUSTOMER] ANAGRAF '{resolved['name']}' → ID {resolved['id']}  "
            f"(agency {resolved['agency'] or 'none'}, CENTROMEDIA {resolved['media_center']})"
        )
        if resolved["name"].lower() != order.advertiser.lower():
            print(
                f"  ⚠ The sheet says '{order.advertiser}'; ANAGRAF {resolved['id']} is named "
                f"'{resolved['name']}' — confirm this is the same committee."
            )
    raw_id = input(f"  Customer (ANAGRAF) ID [{default_id}]: ").strip()
    customer_id = raw_id or default_id
    if not customer_id.isdigit():
        print("  ✗ Invalid customer ID — aborting")
        return None
    customer_id = int(customer_id)
    if resolved and customer_id != resolved["id"]:
        resolved = _anagraf_by_id(customer_id)
    cust_name = resolved["name"] if resolved else order.advertiser

    agency_id = (resolved or {}).get("agency") or AGENCY_ID
    agency = _anagraf_by_id(agency_id)
    if agency:
        print(
            f"[AGENCY]   ANAGRAF '{agency['name']}' → ID {agency['id']}  {agency['pct']:.0f}% commission"
        )
        if abs(agency["pct"] - order.commission_pct) > 0.01:
            print(
                f"  ⚠ The sheet nets at {order.commission_pct:.2f}% but ANAGRAF {agency['id']} "
                f"carries {agency['pct']:.2f}% — entry will roll back unless they agree. "
                "Fix the ANAGRAF commission (or the sheet) first."
            )
    if not (resolved or {}).get("agency"):
        print(
            f"  ⚠ ANAGRAF {customer_id} links no agency — the header will fall back to "
            f"{AGENCY_NAME} ({AGENCY_ID}); link it in Etere so the lookup owns it."
        )

    # ── Separation / billing (customer DB, else the house default) ──
    separation = DEFAULT_SEPARATION
    billing_type = "agency"
    cust = _lookup_customer_db(cust_name) or _lookup_customer_db(order.advertiser)
    if cust:
        billing_type = cust.billing_type or billing_type
        separation = (cust.separation_customer, cust.separation_order, cust.separation_event)
    else:
        print(
            f"\n  Separation default {DEFAULT_SEPARATION} (customer DB has no row; Polaris "
            f"political lines carried {PRECEDENT_SEPARATION}"
            f"{'; the sheet says no separation requirement' if any('separation' in n.lower() for n in order.notes) else ''})"
        )

    sheet_cycle = ""
    if order.billing:
        sheet_cycle = "Calendar" if "calendar" in order.billing.lower() else "Broadcast"
    anagraf_cycle = {316: "Broadcast", 317: "Calendar"}.get(
        (resolved or {}).get("media_center", 0), ""
    )
    default_cycle = sheet_cycle or anagraf_cycle or "Broadcast"
    src = "sheet" if sheet_cycle else ("ANAGRAF" if anagraf_cycle else "house default")
    raw = input(f"  Billing cycle (Broadcast/Calendar) [{default_cycle}] ({src}): ").strip().title()
    cycle = raw or default_cycle
    if cycle.lower() not in MEDIA_CENTER:
        print("  ✗ Billing cycle must be Broadcast or Calendar — aborting")
        return None
    media_center_id = MEDIA_CENTER[cycle.lower()]

    # ── Start date (always asked — these orders arrive day-of) + line plan ──
    start = confirm_start_date(fmt_mmddyyyy(order.start), fmt_mmddyyyy(order.end), always_ask=True)
    if start is None:
        print("  ✗ No flight start date — aborting")
        return None
    plan = order_plan(order, start)
    print(
        f"\n    Etere lines for a {fmt_mmddyyyy(start)} start (Rotation, cap = spots / active days):"
    )
    entered, notes = _print_plan(plan)
    ordered = order.total_spots
    if notes:
        print("\n    ⚠ The start date makes some spots undeliverable:")
        for n in notes:
            print(f"        {n}")
    hints = fit_hints(plan, separation[0])
    if hints:
        print(
            f"\n    ⚠ At {separation[0]} min customer separation these caps do not fit their window:"
        )
        for h in hints:
            print(f"        {h}")
        print("      (lower the customer separation at the next prompt, or accept make-goods)")
    print(
        f"    Spots: {entered} entered of {ordered} ordered"
        f"{'' if entered == ordered else '  ← SHORT'}"
    )
    if entered != ordered:
        raw = input("    Enter this order short anyway? [y/N]: ").strip().lower()
        if raw not in ("y", "yes"):
            print("  ✗ Aborted — pick an earlier start date or ask the agency to revise.")
            return None

    default_code, default_desc, default_ref = default_code_desc(order, start, cust)
    while True:
        raw = input(f"  Contract code [{default_code}]: ").strip()
        code = raw or default_code
        if len(code) <= CODE_WIDTH:
            break
        print(f"    ✗ {len(code)} chars — Etere allows {CODE_WIDTH}")
    while True:
        raw = input(f"  Description [{default_desc}]: ").strip()
        desc = raw or default_desc
        if len(desc) <= DESC_WIDTH:
            break
        print(f"    ✗ {len(desc)} chars — Etere allows {DESC_WIDTH}")
    raw = input(f"  Customer order ref [{default_ref or '-'}] ('-' = none): ").strip()
    customer_ref = "" if raw in ("", "-") and not default_ref else (raw or default_ref)
    if raw == "-":
        customer_ref = ""

    default_note = default_header_note(order)
    raw = input(f"  Header note [{default_note}] ('-' = none): ").strip()
    header_note = "" if raw == "-" else (raw or default_note)

    code_prefix, desc_prefix = learn_prefixes(code, desc)
    _upsert_customer_db(
        cust_name, customer_id, separation, billing_type, market_code, code_prefix, desc_prefix
    )

    return {
        "customer_id": customer_id,
        "agency_id": agency_id,
        "expected_commission_pct": order.commission_pct,
        "billing_type": billing_type,
        "media_center_id": media_center_id,
        "separation": separation,
        "market": market_code,
        "contract_code": code,
        "description": desc,
        "customer_ref": customer_ref,
        "start_date_override": fmt_mmddyyyy(start),
        "header_note": header_note[:399],
    }


# ─── Direct DB entry ──────────────────────────────────────────────────────────


def _create_viewpoint_contract(
    order: ViewpointOrder, inputs: dict, dry_run: bool = False
) -> Optional[str]:
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = inputs.get("customer_id")
    market_code = inputs.get("market") or order.market_code or DEFAULT_MARKET
    if customer_id is None or not market_code:
        print(f"{_LABEL} ✗ No customer_id / market")
        return None
    separation = tuple(inputs.get("separation") or DEFAULT_SEPARATION)
    billing_type = inputs.get("billing_type", "agency")
    media_center_id = int(inputs.get("media_center_id") or MEDIA_CENTER["broadcast"])
    agency_id = int(inputs.get("agency_id") or AGENCY_ID)
    expected_pct = inputs.get("expected_commission_pct")
    if expected_pct is None:
        expected_pct = order.commission_pct
    code = inputs["contract_code"]
    description = inputs["description"]
    customer_ref = inputs.get("customer_ref", "")
    header_note = inputs.get("header_note", "")
    start = (
        parse_date(inputs["start_date_override"])
        if inputs.get("start_date_override")
        else order.start
    )
    end = order.end

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=code,
            description=description,
            customer_id=int(customer_id),
            agency_id=agency_id,  # fallback only — ANAGRAF (lookup) is authoritative
            lookup_customer_defaults=True,
            contract_date=start,
            contract_end_date=end,
            contract_type=1,
            billing_type=billing_type,
            media_center_id=media_center_id,
            allow_rename=True,
            customer_order_ref=customer_ref,
            note=header_note,
        )
        print(
            f"{_LABEL} ✓ Contract header: ID={contract_id}  code='{code}'  "
            f"{fmt_mmddyyyy(start)}–{fmt_mmddyyyy(end)}  ref={customer_ref!r}"
        )

        line_count = 0
        for p in order_plan(order, start):
            ln: ViewpointLine = p["line"]
            for n in p["notes"]:
                print(f"  [NOTE] {p['description']}: {n}")
            if p["spots"] == 0:
                continue
            line_count += 1
            print(
                f"  [LINE {line_count}] {market_code} {p['description']}: "
                f"{fmt_mmddyyyy(p['date_from'])}–{fmt_mmddyyyy(p['date_to'])} "
                f"{p['spots']} spots over {p['active_days']} days, max {p['max_daily']}/day "
                f":{ln.length_sec}s rate={ln.rate} Rotation"
            )
            client.add_contract_line(
                market=market_code,
                days=p["days"],
                time_range=p["time_range"],
                description=p["description"],
                rate=ln.rate,
                total_spots=p["spots"],
                spots_per_week=0,  # flight total, no weekly cadence on the sheet
                max_daily_run=p["max_daily"],
                date_from=p["date_from"],
                date_to=p["date_to"],
                duration=str(ln.length_sec),
                is_bonus=ln.is_bonus,
                booking_code=10 if ln.is_bonus else 2,
                scheduling_type=SCHEDULING_ROTATION,
                separation_intervals=separation,
            )
        if line_count == 0:
            raise RuntimeError("Viewpoint: no line could be entered — rolling back")

        # Read back the header money basis before committing.
        cur = conn.cursor()
        cur.execute(
            "SELECT P_AGENZIA, AGENZIA, CENTROMEDIA, CUSTOMERREF FROM CONTRATTITESTATA "
            "WHERE ID_CONTRATTITESTATA = %s",
            (int(contract_id),),
        )
        row = cur.fetchone()
        if row is None or int(row[1] or 0) == 0:
            raise RuntimeError(
                f"Viewpoint: header readback AGENZIA={row and row[1]} — no agency on the "
                "contract; rolling back"
            )
        if abs(float(row[0] or 0) - float(expected_pct)) > 0.01:
            raise RuntimeError(
                f"Viewpoint: header P_AGENZIA={row[0]} but the sheet nets at "
                f"{expected_pct:.2f}% — the contract would bill a different commission "
                "than the proposal; rolling back (fix ANAGRAF or the sheet)"
            )
        if int(row[2] or 0) != media_center_id:
            raise RuntimeError(
                f"Viewpoint: header CENTROMEDIA={row[2]} but {media_center_id} was "
                "chosen at gather; rolling back"
            )
        cur.execute(
            "SELECT COUNT(*), SUM(N_PASSAGGI), SUM(N_PASSAGGI * IMPORTO) FROM CONTRATTIRIGHE "
            "WHERE ID_CONTRATTITESTATA = %s",
            (int(contract_id),),
        )
        n_lines, n_spots, gross = cur.fetchone()
        if int(n_lines or 0) != line_count or abs(float(gross or 0) - order.gross_airtime) > 0.01:
            raise RuntimeError(
                f"Viewpoint: lines readback {n_lines} lines / {n_spots} spots / "
                f"${float(gross or 0):,.2f} != {line_count} / ${order.gross_airtime:,.2f}; rolling back"
            )
        if dry_run:
            conn.rollback()
            conn.close()
            print(
                f"{_LABEL} DRY RUN: {line_count} lines / {n_spots} spots / ${float(gross):,.2f} "
                f"in txn, AGENZIA={row[1]} P_AGENZIA={row[0]} CENTROMEDIA={row[2]} "
                f"ref={row[3]!r} — ROLLED BACK"
            )
            return code
        conn.commit()
        conn.close()
        print(
            f"{_LABEL} ✓ {line_count} lines / {n_spots} spots / ${float(gross):,.2f} committed "
            f"on {code} (AGENZIA {row[1]} @ {row[0]}%, CENTROMEDIA {row[2]})."
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


def run_viewpoint_order(order: ViewpointOrder, inputs: dict) -> list[tuple[str, bool]]:
    """One contract per option sheet. Returns [(code, success)]."""
    code = _create_viewpoint_contract(order, inputs)
    return [(inputs.get("contract_code") or code or "", code is not None)]


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Viewpoint Ads option sheet → Etere (direct DB)")
    ap.add_argument("pdf")
    ap.add_argument("--dry-run", action="store_true", help="write in a transaction, roll back")
    ap.add_argument("--start", help="start date M/D/YYYY (default: the sheet's flight start)")
    ap.add_argument("--separation", default="15,0,0")
    a = ap.parse_args()
    o = parse_viewpoint(a.pdf)
    st = parse_date(a.start) if a.start else o.start
    if a.dry_run:
        c, d, r = default_code_desc(o, st)
        inputs = {
            "customer_id": DEFAULT_CUSTOMER_ID,
            "agency_id": AGENCY_ID,
            "expected_commission_pct": o.commission_pct,
            "billing_type": "agency",
            "media_center_id": MEDIA_CENTER[
                "calendar" if "calendar" in o.billing.lower() else "broadcast"
            ],
            "separation": tuple(int(x) for x in a.separation.split(",")),
            "market": o.market_code or DEFAULT_MARKET,
            "contract_code": c,
            "description": d,
            "customer_ref": r,
            "start_date_override": fmt_mmddyyyy(st),
            "header_note": default_header_note(o),
        }
        _create_viewpoint_contract(o, inputs, dry_run=True)
    else:
        inp = gather_viewpoint_inputs(a.pdf)
        if inp:
            print(run_viewpoint_order(o, inp))
