"""California Black Media (SOS campaigns) — gather + direct-DB entry.

Conventions (Lee, 2026-10-26; precedent contract 2203 `CBM 2511`, line names from
the ACM contract 2881 on the same proposal template):
  * customer ANAGRAF 404 California Black Media, direct, 0% (lookup_customer_defaults
    reads payment/agent from ANAGRAF); billing from the sheet ("Billing Cycle
    Broadcast" → 316); rates are NET and enter verbatim;
  * ONE contract for all markets — each proposal grid's lines carry their own
    market (LAX / SFO / CVC);
  * code `CBM <yymm>`, description `CA Black Media - <campaign> <yymm>`, Customer
    order ref = the IO's campaign name ("SOS Vote Education Campaign 3") when the
    IO sits beside the proposal, else the sheet's tag ("SOS");
  * line names `Hindi News/Talk M-F 1p-2p`; bonus `BNS Hindi ROS` on the language's
    ROS window (Hindi → South Asian 1p-4p M-Su), booking code 10;
  * separation 15/0/0 (customer row in CTV_Customers once entered);
  * the proposal carries no flight dates or spot length: the start date is always
    asked (first week column), the END date too (the IO's when present, else the
    last week's Sunday) — `plan_ranges` gives a short last week its own max/day.
"""

from __future__ import annotations

import re
import shutil
from datetime import date
from pathlib import Path
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
from browser_automation.parsers.cbm_parser import DEFAULT_CUSTOMER_ID, CBMOrder, parse_cbm

DEFAULT_CODE_PREFIX = "CBM"
DEFAULT_DESCRIPTION_PREFIX = "CA Black Media"
DEFAULT_SEPARATION = (15, 0, 0)  # customer, order, event
MEDIA_CENTER = {"BROADCAST": 316, "CALENDAR": 317}
_LABEL = "[CBM]"


DESCRIPTION_WIDTH = (
    50  # what the header SP keeps of DESCRIZIONE (2026-10-26 dry run: 51 chars came back as 50)
)


def fit_campaign(prefix: str, campaign: str, yymm: str, width: int = DESCRIPTION_WIDTH) -> str:
    """'CA Black Media - SOS Vote Education Campaign 3 2611' is 51 chars: shorten the
    campaign ('Campaign 3' → 'C3', then drop trailing words) so the yymm always survives."""

    def build(c: str) -> str:
        return f"{prefix} - {c} {yymm}" if c else f"{prefix} {yymm}"

    if len(build(campaign)) <= width:
        return build(campaign)
    short = re.sub(r"\bCampaign\s+(\d+)\b", r"C\1", campaign)
    words = short.split()
    while words and len(build(" ".join(words))) > width:
        words.pop()
    return build(" ".join(words))


def default_code_desc(
    start: date,
    campaign: str,
    code_prefix: str = DEFAULT_CODE_PREFIX,
    desc_prefix: str = DEFAULT_DESCRIPTION_PREFIX,
) -> tuple[str, str]:
    yymm = broadcast_yymm(start)
    return f"{code_prefix} {yymm}", fit_campaign(desc_prefix, campaign, yymm)


def _line_plan(order: CBMOrder, start_from: date, line_end: date) -> list[tuple]:
    """(line, days, time_range, description, ranges, notes) per airtime line, all markets."""
    plan: list[tuple] = []
    for ln in order.lines:
        if ln.total_spots == 0:
            continue
        days, time_raw = ln.days, ln.time
        if not ln.is_bonus:
            days, _ = EtereClient.check_sunday_6_7a_rule(days, time_raw)
        time_from, time_to = EtereClient.parse_time_range(time_raw)
        consolidated = EtereClient.consolidate_weeks(
            ln.weekly_spots,
            [WeekCol(d) for d in order.week_dates],
            flight_end=fmt_mmddyyyy(line_end),
        )
        ranges, notes = plan_ranges(consolidated, days, start_from, line_end)
        plan.append((ln, days, f"{time_from}-{time_to}", ln.description, ranges, notes))
    return plan


def _print_plan(order: CBMOrder, start: date, end: date) -> tuple[int, int, list[str]]:
    entered, all_notes = 0, []
    for ln, days, time_range, desc, ranges, notes in _line_plan(order, start, end):
        for rng in ranges:
            entered += rng["spots_per_week"] * rng["weeks"]
            tag = f"   ← {rng['tag']}" if rng["tag"] else ""
            print(
                f"    {ln.market} {desc[:34]:<34} {days:<6} {time_range}  "
                f"{fmt_mmddyyyy(rng['date_from'])}–{fmt_mmddyyyy(rng['date_to'])}"
                f"  {rng['spots_per_week']}/wk×{rng['weeks']}w  max {rng['max_daily']}/day{tag}"
            )
        all_notes.extend(f"{ln.market} {desc[:34]}: {n}" for n in notes)
    return entered, order.total_spots, all_notes


def confirm_end_date(default_end: date, start: date) -> date:
    """The proposal has no flight end; ask (default = the IO's end or the last week's Sunday)."""
    while True:
        raw = input(
            f"  What date does this order end? [{default_end.month}/{default_end.day}/{default_end.strftime('%y')}]: "
        ).strip()
        if not raw or raw.lower() in ("y", "yes"):
            return default_end
        try:
            chosen = parse_date(raw)
        except ValueError:
            print(f"    ✗ Could not read '{raw}' — use M/D/YY or MM/DD/YYYY.")
            continue
        if chosen < start:
            print(f"    ✗ {raw} is before the start {fmt_mmddyyyy(start)}.")
            continue
        return chosen


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


def _upsert_customer_db(name, customer_id, code_name, description_name, separation) -> None:
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        from src.domain.enums import OrderType

        CustomerRepository(CUSTOMER_DB_PATH).save(
            Customer(
                customer_id=str(customer_id),
                customer_name=name,
                order_type=OrderType.CBM,
                billing_type="direct",
                code_name=code_name,
                description_name=description_name,
                separation_customer=separation[0],
                separation_order=separation[1],
                separation_event=separation[2],
            )
        )
    except Exception as exc:
        print(f"[CUSTOMER] customers upsert failed (non-fatal): {exc}")


def _anagraf_defaults(customer_id: int) -> dict:
    try:
        from browser_automation.etere_direct_client import connect

        conn = connect()
        cur = conn.cursor()
        cur.execute(
            """SELECT c.RAG_SOCIAL, c.AGENZIA, a.RAG_SOCIAL, ISNULL(a.Commissione, 0), c.CENTROMEDIA
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
            "media_center_id": int(row[4] or 0),
        }
    except Exception as exc:
        print(f"[CUSTOMER] ANAGRAF lookup failed: {exc}")
        return {}


# ─── gather ──────────────────────────────────────────────────────────────────


def gather_cbm_inputs(source_path: str) -> Optional[dict]:
    order = parse_cbm(source_path)

    print(f"\n{'=' * 64}")
    print(
        f"Client:     {order.client}   (sheet agency line: {order.agency_label}: {order.campaign_tag}; "
        f"contact {order.contact} {order.email})"
    )
    if order.io:
        print(
            f"IO:         {Path(order.io.source_path).name} — '{order.io.campaign}' dated {order.io.io_date}, "
            f"flight {order.io.flight_start:%m/%d/%Y}–{order.io.flight_end:%m/%d/%Y}, "
            f":{order.io.length_sec or '?'}, ${order.io.total:,.2f} — reconciles with the proposal ✓"
        )
    else:
        print(
            "IO:         none beside the proposal — the campaign name, flight end and length will be asked"
        )
    print(f"Markets:    {order.market_text} → {', '.join(order.markets)}")
    print(
        f"Weeks:      {', '.join(f'{d.month}/{d.day}/{d.strftime("%y")}' for d in order.week_dates)}   "
        f"billing {order.billing_cycle}"
    )
    print("Money:      NET rates — enter verbatim (direct customer, 0%)")
    for sec in order.sections:
        print(f"\n  {sec.market}  {sec.banner}")
        for ln in sec.lines:
            print(
                f"    {'BNS' if ln.is_bonus else '   '} {ln.language_block:<22} {ln.daypart:<16} "
                f"{ln.weekly_spots}  = {ln.total_spots:>3} @ ${ln.net_rate:>7.2f}  ${ln.net_total:,.2f}"
            )
    print(
        f"\n  Total: {order.total_spots} spots ({order.total_paid_units} paid + {order.total_bonus_units} bonus)   ${order.total_cost:,.2f}"
    )

    raw = input(f"\n  Spot length in seconds [{order.length_sec}]: ").strip()
    length_sec = int(raw) if raw.isdigit() else order.length_sec

    start_override = confirm_start_date(order.flight_start, None, always_ask=True)
    if start_override is None:
        print("  ✗ No flight start date — aborting")
        return None
    end_override = confirm_end_date(parse_date(order.flight_end), start_override)

    print(f"\n  Etere lines for {fmt_mmddyyyy(start_override)} – {fmt_mmddyyyy(end_override)}:")
    entered, ordered, notes = _print_plan(order, start_override, end_override)
    if notes:
        print("\n  ⚠ The dates make some spots undeliverable:")
        for n in notes:
            print(f"      {n}")
    print(
        f"\n  Spots: {entered} entered of {ordered} ordered{'' if entered == ordered else '  ← SHORT'}"
    )
    if entered != ordered:
        raw = input("  Enter the order short anyway? [y/N]: ").strip().lower()
        if raw not in ("y", "yes"):
            print("  ✗ Aborted — pick other dates or ask the buyer to revise.")
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
        f"{anag.get('agency_id') or '—'} {anag.get('agency_name') or '(none linked, direct)'} @ {anag.get('agency_pct', 0):.0f}%"
    )

    separation = DEFAULT_SEPARATION
    cust = _lookup_customer_db(customer_id)
    if cust and cust.separation_customer is not None:
        separation = (cust.separation_customer, cust.separation_order, cust.separation_event)
    code_prefix = (
        cust.code_name if cust and getattr(cust, "code_name", "") else ""
    ) or DEFAULT_CODE_PREFIX
    desc_prefix = (
        cust.description_name if cust and getattr(cust, "description_name", "") else ""
    ) or DEFAULT_DESCRIPTION_PREFIX
    campaign = order.campaign_name
    if order.io is None:
        # proposal-only entry (Lee 10/26): the sheet carries just the tag ("SOS");
        # the campaign name lives on CBM's IO — type it so the description/ref carry it
        raw = input(f"  Campaign name (from the CBM IO) [{campaign}]: ").strip()
        campaign = raw or campaign
    default_code, default_desc = default_code_desc(
        start_override, campaign, code_prefix, desc_prefix
    )
    print()
    raw = input(f"  Contract code [{default_code}]: ").strip()
    contract_code = raw or default_code
    raw = input(f"  Description [{default_desc}]: ").strip()
    description = raw or default_desc
    raw = input(f"  Customer order ref [{campaign}]: ").strip()
    customer_ref = raw or campaign
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
    )

    return {
        "customer_id": customer_id,
        "billing_type": "direct",
        "separation": separation,
        "contract_code": contract_code,
        "description": description,
        "customer_ref": customer_ref,
        "campaign": campaign,
        "media_center_id": billing_cycle,
        "spot_duration": length_sec,
        "start_date_override": fmt_mmddyyyy(start_override),
        "end_date_override": fmt_mmddyyyy(end_override),
    }


# ─── direct DB entry ─────────────────────────────────────────────────────────


def _create_cbm_contract(order: CBMOrder, inputs: dict, dry_run: bool = False) -> Optional[str]:
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = inputs.get("customer_id")
    if customer_id is None:
        print(f"{_LABEL} ✗ No customer_id")
        return None
    separation = tuple(inputs.get("separation", DEFAULT_SEPARATION))
    billing_type = inputs.get("billing_type", "direct")
    start_d = parse_date(inputs.get("start_date_override") or order.flight_start)
    end_d = parse_date(inputs.get("end_date_override") or order.flight_end)
    campaign = inputs.get("campaign") or order.campaign_name
    default_code, default_desc = default_code_desc(start_d, campaign)
    contract_code = inputs.get("contract_code") or default_code
    description = inputs.get("description") or default_desc
    customer_ref = inputs.get("customer_ref") or campaign
    length_sec = int(inputs.get("spot_duration") or order.length_sec)
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
            lookup_customer_defaults=True,  # ANAGRAF 404: no agency, payment, agent
            contract_date=start_d,
            contract_end_date=end_d,
            contract_type=1,
            billing_type=billing_type,
            media_center_id=media_center_id,
            customer_order_ref=customer_ref,
            allow_rename=True,
        )
        print(
            f"{_LABEL} ✓ Contract header: ID={contract_id}  code='{contract_code}'  ref='{customer_ref}'"
        )

        line_count = 0
        for ln, days, time_range, desc, ranges, line_notes in _line_plan(order, start_d, end_d):
            for note in line_notes:
                print(f"  [NOTE] {ln.market} {desc}: {note}")
            for rng in ranges:
                total_spots = rng["spots_per_week"] * rng["weeks"]
                line_count += 1
                tag = f"  ← {rng['tag']}" if rng["tag"] else ""
                print(
                    f"  [LINE {line_count}] {ln.market} {desc}: "
                    f"{fmt_mmddyyyy(rng['date_from'])}–{fmt_mmddyyyy(rng['date_to'])} "
                    f"({rng['spots_per_week']}/wk×{rng['weeks']}w={total_spots}) :{length_sec}s "
                    f"rate={ln.rate} max {rng['max_daily']}/day{tag}"
                )
                client.add_contract_line(
                    market=ln.market,
                    days=days,
                    time_range=time_range,
                    description=desc,
                    rate=ln.rate,
                    total_spots=total_spots,
                    spots_per_week=rng["spots_per_week"],
                    max_daily_run=rng["max_daily"],
                    date_from=rng["date_from"],
                    date_to=rng["date_to"],
                    duration=str(length_sec),
                    is_bonus=ln.is_bonus,
                    booking_code=10 if ln.is_bonus else 2,
                    separation_intervals=separation,
                )

        if dry_run:
            cur = conn.cursor()
            cur.execute(
                "SELECT CENTROMEDIA, AGENZIA, P_AGENZIA, COD_USER, INVOICEMODE, CUSTOMERREF, DESCRIZIONE "
                "FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = %s",
                (contract_id,),
            )
            hdr = cur.fetchone()
            cur.execute(
                """SELECT COD_USER, DESCRIZIONE, DATESTART, DATEEND, N_PASSAGGI, IMPORTO, ID_BOOKINGCODE,
                          PRENOTAZIONE, DURATA, Interv_Committente, INTERVALLO, INTERV_CONTRATTO, PASSAGGI_GIORNALIERI
                   FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA = %s ORDER BY ID_CONTRATTIRIGHE""",
                (contract_id,),
            )
            rows = cur.fetchall()
            conn.rollback()
            conn.close()
            print(
                f"{_LABEL} DRY RUN header (CENTROMEDIA, AGENZIA, P_AGENZIA, COD_USER, INVOICEMODE, REF, DESC) = {hdr}"
            )
            for r in rows:
                print(f"{_LABEL} DRY RUN line {r}")
            print(
                f"{_LABEL} DRY RUN {len(rows)} lines, {sum(r[4] for r in rows)} spots — ROLLED BACK"
            )
            return contract_code

        conn.commit()
        conn.close()
        print(f"{_LABEL} ✓ {line_count} lines committed across {len(order.sections)} markets.")
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


def move_companion_to_entered(order: CBMOrder) -> None:
    """The entry flow moves the dropped file; the other document of the pair (the IO
    beside the proposal, or the proposal beside a dropped IO) must follow it, or it
    stays in the queue as a second pending order for a contract already entered."""
    if not order.companion_path:
        return
    from src.business_logic.services.backwrite_manifest import ENTERED_DIRNAME

    src = Path(order.companion_path)
    if not src.exists():
        return
    try:
        dest = src.parent / ENTERED_DIRNAME / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        shutil.move(str(src), str(dest))
        print(f"{_LABEL} moved companion {src.name} to {dest.parent}")
    except OSError as exc:
        print(f"{_LABEL} NOTE: companion {src.name} stays in incoming ({exc})")


def run_cbm_order(order: CBMOrder, inputs: dict) -> list[tuple[str, bool]]:
    """One contract per proposal (all markets). Returns [(contract_code, success)]."""
    code = _create_cbm_contract(order, inputs)
    if code is not None:
        move_companion_to_entered(order)
    label = (
        inputs.get("contract_code")
        or default_code_desc(parse_date(order.flight_start), order.campaign_name)[0]
    )
    return [(label, code is not None)]


if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description="CBM proposal → Etere (dry run rolls back)")
    ap.add_argument("pdf")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--start", help="start date m/d/yyyy (default: the first week column)")
    ap.add_argument(
        "--end", help="end date m/d/yyyy (default: the IO's, else the last week's Sunday)"
    )
    a = ap.parse_args()
    o = parse_cbm(a.pdf)
    start = parse_date(a.start) if a.start else parse_date(o.flight_start)
    end = parse_date(a.end) if a.end else parse_date(o.flight_end)
    print(f"\n  Etere lines for {fmt_mmddyyyy(start)} – {fmt_mmddyyyy(end)}:")
    e, n, notes = _print_plan(o, start, end)
    print(f"  Spots: {e} entered of {n} ordered; notes: {notes or 'none'}")
    if a.dry_run:
        code, desc = default_code_desc(start, o.campaign_name)
        _create_cbm_contract(
            o,
            {
                "customer_id": DEFAULT_CUSTOMER_ID,
                "billing_type": "direct",
                "separation": DEFAULT_SEPARATION,
                "contract_code": f"{code} DRYRUN",
                "description": desc,
                "customer_ref": o.campaign_name,
                "media_center_id": MEDIA_CENTER.get(o.billing_cycle.upper(), 316),
                "spot_duration": o.length_sec,
                "start_date_override": fmt_mmddyyyy(start),
                "end_date_override": fmt_mmddyyyy(end),
            },
            dry_run=True,
        )
