"""California Alliance of Family Owned Businesses PAC — gather + direct-DB entry.

Conventions (Lee, 2026-10-06; oracle = Maija's hand entry, contract 3155
`Matson Media CA 2610`, with house rules applied where her entry differed):
  * customer ANAGRAF 484, agency ANAGRAF 483 Matson Media LLC (0%) — the IO prints
    "National Media" (Mike Adam), Lee: Matson Media is the right agency record;
    ANAGRAF wins via lookup_customer_defaults, AGENCY_IDS["MATSON"] is the fallback;
  * rates are GROSS and enter verbatim; market from the sheet ("SF BAY AREA" → SFO),
    confirmed at the prompt; billing from the sheet ("Billing Cycle Broadcast" → 316);
  * code `Matson CAFOB <yymm>`, description `CA Alliance of Family Owned Businesses
    PAC <yymm>`; notes = the sheet's separation sentence ("30 minute separation");
  * separation: the sheet's minutes capped at the house 25 (25/0/0 for "30");
  * equal consecutive weeks consolidate into one line; :30 = 900 frames; Priority
    (week columns) — Maija's 3155 had one line per week and 870-frame spots;
  * the start date is always asked (the first order arrived the day after its
    Monday; `plan_ranges` splits the short first week with its own max/day).
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
from browser_automation.parsers.cafob_parser import CAFOBOrder, parse_cafob

DEFAULT_CUSTOMER_ID = 484
DEFAULT_AGENCY_KEY = "MATSON"  # AGENCY_IDS → 483 Matson Media LLC
DEFAULT_MARKET = "SFO"
DEFAULT_CODE_PREFIX = "Matson CAFOB"
DEFAULT_DESCRIPTION_PREFIX = "CA Alliance of Family Owned Businesses PAC"
DEFAULT_SEPARATION = (25, 0, 0)  # customer, order, event
HOUSE_SEPARATION_CAP = 25  # an IO's "30 minute separation" enters as 25 (house rule)
MEDIA_CENTER = {"BROADCAST": 316, "CALENDAR": 317}
_MARKETS = ("CVC", "SFO", "LAX", "SEA", "HOU", "CMP", "WDC", "NYC", "MMT", "DAL")
_LABEL = "[CAFOB]"
_SHORT = {"Vietnamese": "Viet"}  # house short form on line names (3155: 'M-F Viet News 11a-11:30a')


def _short_time(hhmm: str) -> str:
    """'11:00' → '11a', '13:00' → '1p', '23:59' → '12a', '11:30' → '11:30a'."""
    h, m = (int(x) for x in hhmm.split(":"))
    if hhmm == "23:59":
        return "12a"
    suffix = "a" if h < 12 else "p"
    h12 = h % 12 or 12
    return f"{h12}{suffix}" if m == 0 else f"{h12}:{m:02d}{suffix}"


def separation_for(sheet_minutes: Optional[int]) -> tuple[int, int, int]:
    """Sheet separation → (customer, order, event), capped at the house 25."""
    if not sheet_minutes:
        return DEFAULT_SEPARATION
    return (min(int(sheet_minutes), HOUSE_SEPARATION_CAP), 0, 0)


def line_description(ln, time_from: str, time_to: str) -> str:
    lang = _SHORT.get(ln.language, ln.language)
    desc = f"{ln.days} {lang} {ln.program} {_short_time(time_from)}-{_short_time(time_to)}"
    return (f"BNS {desc}" if ln.is_bonus else desc)[:60]


def _line_plan(order: CAFOBOrder, start_from: date) -> list[tuple]:
    """(line, days, time_range, description, ranges, notes) per airtime line."""
    plan: list[tuple] = []
    line_end = parse_date(order.flight_end)
    for ln in order.lines:
        if ln.total_spots == 0:
            continue
        days, _ = EtereClient.check_sunday_6_7a_rule(ln.days, ln.time)
        time_from, time_to = EtereClient.parse_time_range(ln.time)
        desc = line_description(ln, time_from, time_to)
        consolidated = EtereClient.consolidate_weeks(
            ln.weekly_spots,
            [WeekCol(d) for d in order.week_dates],
            flight_end=fmt_mmddyyyy(line_end),
        )
        ranges, notes = plan_ranges(consolidated, days, start_from, line_end)
        plan.append((ln, days, f"{time_from}-{time_to}", desc, ranges, notes))
    return plan


def _print_plan(order: CAFOBOrder, start: date) -> tuple[int, int, list[str]]:
    entered, all_notes = 0, []
    for _ln, days, time_range, desc, ranges, notes in _line_plan(order, start):
        for rng in ranges:
            entered += rng["spots_per_week"] * rng["weeks"]
            tag = f"   ← {rng['tag']}" if rng["tag"] else ""
            print(
                f"    {desc[:36]:<36} {days:<6} {time_range}  "
                f"{fmt_mmddyyyy(rng['date_from'])}–{fmt_mmddyyyy(rng['date_to'])}"
                f"  {rng['spots_per_week']}/wk×{rng['weeks']}w  max {rng['max_daily']}/day{tag}"
            )
        all_notes.extend(f"{desc[:36]}: {n}" for n in notes)
    return entered, order.total_spots, all_notes


# ─── customer records ─────────────────────────────────────────────────────────


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


def _upsert_customer_db(name, customer_id, code_name, description_name, separation, market) -> None:
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        from src.domain.enums import OrderType

        CustomerRepository(CUSTOMER_DB_PATH).save(
            Customer(
                customer_id=str(customer_id),
                customer_name=name,
                order_type=OrderType.CAFOB,
                billing_type="agency",
                code_name=code_name,
                description_name=description_name,
                separation_customer=separation[0],
                separation_order=separation[1],
                separation_event=separation[2],
                default_market=market,
            )
        )
    except Exception as exc:
        print(f"[CUSTOMER] customers.db upsert failed (non-fatal): {exc}")


def _anagraf_defaults(customer_id: int) -> dict:
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


# ─── gather ──────────────────────────────────────────────────────────────────


def default_code_desc(
    start: date,
    code_prefix: str = DEFAULT_CODE_PREFIX,
    desc_prefix: str = DEFAULT_DESCRIPTION_PREFIX,
) -> tuple[str, str]:
    yymm = broadcast_yymm(start)
    return f"{code_prefix} {yymm}", f"{desc_prefix} {yymm}"


def gather_cafob_inputs(source_path: str) -> Optional[dict]:
    order = parse_cafob(source_path)

    print(f"\n{'=' * 64}")
    print(
        f"Client:     {order.client}   (buyer label on the sheet: {order.agency_label}; contact {order.contact} {order.email})"
    )
    print(f"Market:     {order.market_text} → {order.market or '?'}   channel {order.channel}")
    print(
        f"Flight:     {order.flight_start} → {order.flight_end}  ({len(order.week_dates)} weeks: "
        f"{', '.join(f'{d.month}/{d.day}' for d in order.week_dates)})   :{order.length_sec}s   "
        f"billing {order.billing_cycle}   sheet separation {order.separation_minutes or '—'} min"
    )
    print("Money:      GROSS rates — enter verbatim (agency commission from ANAGRAF)")
    print("\n  Lines:")
    for ln in order.lines:
        print(
            f"    {'BNS' if ln.is_bonus else '   '} {ln.language_block:<26} {ln.daypart:<18} "
            f"{ln.weekly_spots}  = {ln.total_spots:>3} @ ${ln.gross_rate:>7.2f}  ${ln.gross_total:,.2f}"
        )
    print(f"\n  Total: {order.total_spots} spots   ${order.total_cost:,.2f}")

    default_market = order.market or DEFAULT_MARKET
    raw = input(f"  Market [{default_market}]: ").strip().upper()
    market_code = default_market if not raw or raw in ("Y", "YES") else raw
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
            print("  ✗ Aborted — pick an earlier start date or ask the buyer to revise.")
            return None

    from browser_automation.customer_defaults import prompt_customer_id

    raw_id = prompt_customer_id(str(DEFAULT_CUSTOMER_ID))
    if raw_id is None:
        print("  ✗ No customer ID — aborting")
        return None
    customer_id = int(raw_id)
    anag = _anagraf_defaults(customer_id)
    cust_name = anag.get("name") or order.client
    print(
        f"  [CUSTOMER] ANAGRAF {customer_id} = {cust_name}; agency "
        f"{anag.get('agency_id') or '?'} {anag.get('agency_name') or '(none linked)'} @ {anag.get('agency_pct', 0):.0f}%"
    )

    separation = separation_for(order.separation_minutes)
    cust = _lookup_customer_db(customer_id)
    if cust and cust.separation_customer is not None and not order.separation_minutes:
        separation = (cust.separation_customer, cust.separation_order, cust.separation_event)
    code_prefix = (
        cust.code_name if cust and getattr(cust, "code_name", "") else ""
    ) or DEFAULT_CODE_PREFIX
    desc_prefix = (
        cust.description_name if cust and getattr(cust, "description_name", "") else ""
    ) or DEFAULT_DESCRIPTION_PREFIX
    default_code, default_desc = default_code_desc(start_override, code_prefix, desc_prefix)
    print()
    raw = input(f"  Contract code [{default_code}]: ").strip()
    contract_code = raw or default_code
    raw = input(f"  Description [{default_desc}]: ").strip()
    description = raw or default_desc
    raw = input(f"  Notes [{order.notes or 'none'}]: ").strip()
    notes_text = "" if raw.lower() == "none" else (raw or order.notes)
    billing_cycle = MEDIA_CENTER.get(order.billing_cycle.upper(), 316)
    print(
        f"  Billing: {order.billing_cycle} (CENTROMEDIA {billing_cycle})   Separation: {separation}"
    )

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
        "notes": notes_text,
        "media_center_id": billing_cycle,
        "start_date_override": fmt_mmddyyyy(start_override),
        "market": market_code,
    }


# ─── direct DB entry ─────────────────────────────────────────────────────────


def _create_cafob_contract(order: CAFOBOrder, inputs: dict, dry_run: bool = False) -> Optional[str]:
    from browser_automation.etere_direct_client import AGENCY_IDS, EtereDirectClient, connect

    customer_id = inputs.get("customer_id")
    if customer_id is None:
        print(f"{_LABEL} ✗ No customer_id")
        return None
    separation = tuple(inputs.get("separation", DEFAULT_SEPARATION))
    billing_type = inputs.get("billing_type", "agency")
    override = inputs.get("start_date_override")
    flight_start_d = parse_date(override) if override else parse_date(order.flight_start)
    flight_end_d = parse_date(order.flight_end)
    default_code, default_desc = default_code_desc(flight_start_d)
    contract_code = inputs.get("contract_code") or default_code
    description = inputs.get("description") or default_desc
    market_code = inputs.get("market") or order.market or DEFAULT_MARKET
    notes = inputs.get("notes", order.notes)
    media_center_id = int(
        inputs.get("media_center_id") or MEDIA_CENTER.get(order.billing_cycle.upper(), 316)
    )

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=contract_code,
            description=description,
            customer_id=int(customer_id),
            agency_id=AGENCY_IDS[DEFAULT_AGENCY_KEY],  # fallback; ANAGRAF (484 → 483) wins
            lookup_customer_defaults=True,
            contract_date=flight_start_d,
            contract_end_date=flight_end_d,
            contract_type=1,
            billing_type=billing_type,
            media_center_id=media_center_id,
            note=notes,
            allow_rename=True,
        )
        print(
            f"{_LABEL} ✓ Contract header: ID={contract_id}  code='{contract_code}'  notes='{notes}'"
        )

        line_count = 0
        for ln, days, time_range, desc, ranges, line_notes in _line_plan(order, flight_start_d):
            for note in line_notes:
                print(f"  [NOTE] {desc}: {note}")
            for rng in ranges:
                total_spots = rng["spots_per_week"] * rng["weeks"]
                line_count += 1
                tag = f"  ← {rng['tag']}" if rng["tag"] else ""
                print(
                    f"  [LINE {line_count}] {market_code} {desc}: "
                    f"{fmt_mmddyyyy(rng['date_from'])}–{fmt_mmddyyyy(rng['date_to'])} "
                    f"({rng['spots_per_week']}/wk×{rng['weeks']}w={total_spots}) :{ln.length_sec}s "
                    f"rate={ln.gross_rate} max {rng['max_daily']}/day{tag}"
                )
                client.add_contract_line(
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
                    booking_code=10 if ln.is_bonus else 2,
                    separation_intervals=separation,
                )

        if dry_run:
            cur = conn.cursor()
            cur.execute(
                """SELECT COUNT(*), SUM(N_PASSAGGI), MIN(DURATA), MAX(DURATA), MIN(PRENOTAZIONE), MAX(PRENOTAZIONE),
                          MIN(Interv_Committente), MAX(INTERVALLO), MAX(COD_USER)
                   FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA = %s""",
                (contract_id,),
            )
            lines_row = cur.fetchone()
            cur.execute(
                "SELECT CENTROMEDIA, AGENZIA, P_AGENZIA, COD_USER, NOTE FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = %s",
                (contract_id,),
            )
            hdr = cur.fetchone()
            conn.rollback()
            conn.close()
            print(
                f"{_LABEL} DRY RUN header (CENTROMEDIA, AGENZIA, P_AGENZIA, COD_USER, NOTE) = {hdr}"
            )
            print(
                f"{_LABEL} DRY RUN lines (n, spots, dur min/max, prenot min/max, cust sep, order sep, station) = {lines_row} — ROLLED BACK"
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


def run_cafob_order(order: CAFOBOrder, inputs: dict) -> list[tuple[str, bool]]:
    """One contract per proposal. Returns [(contract_code, success)]."""
    code = _create_cafob_contract(order, inputs)
    label = inputs.get("contract_code") or default_code_desc(parse_date(order.flight_start))[0]
    return [(label, code is not None)]


if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description="CAFOB proposal → Etere (dry run rolls back)")
    ap.add_argument("pdf")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--start", help="start date m/d/yyyy (default: the sheet's)")
    a = ap.parse_args()
    o = parse_cafob(a.pdf)
    start = parse_date(a.start) if a.start else parse_date(o.flight_start)
    print(f"\n  Etere lines for a {fmt_mmddyyyy(start)} start:")
    e, n, notes = _print_plan(o, start)
    print(f"  Spots: {e} entered of {n} ordered; notes: {notes or 'none'}")
    if a.dry_run:
        code, desc = default_code_desc(start)
        _create_cafob_contract(
            o,
            {
                "customer_id": DEFAULT_CUSTOMER_ID,
                "billing_type": "agency",
                "separation": separation_for(o.separation_minutes),
                "contract_code": f"{code} DRYRUN",
                "description": desc,
                "notes": o.notes,
                "media_center_id": MEDIA_CENTER.get(o.billing_cycle.upper(), 316),
                "start_date_override": fmt_mmddyyyy(start),
                "market": o.market or DEFAULT_MARKET,
            },
            dry_run=True,
        )
