"""
ACM (American Community Media) direct DB automation.

Creates ONE Etere contract for the entire order. Each market section's lines
are entered under that single contract, with each line carrying its own market code.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
from browser_automation.etere_client import EtereClient
from browser_automation.parsers.acm_parser import AcmOrder, parse_acm

# Charmaine's proposal PDF for an ACM client (Asian Law Caucus, 2026-10-28): the
# order may end at a clock time on its last day ("ends at 5pm on 11/3"). Lee's
# rule: a line whose window runs past the cutoff is split — the day before runs
# its full window, the last day runs only until the cutoff. Separation for such
# buys is what Lee says at the prompt (3/0/0 here); the sheet's "No separation
# requirement" only sets the default offered.
NO_SEPARATION_DEFAULT = (3, 0, 0)

# ─── Month abbreviations (for consolidate_weeks date format) ─────────────────
_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _fmt_mon_dd(d: date) -> str:
    """date → 'Jun 15' (consolidate_weeks input format)."""
    return f"{_MONTH_ABBR[d.month - 1]} {d.day}"


def _fmt_mmddyyyy(d: date) -> str:
    """date → 'MM/DD/YYYY' (EtereDirectClient date format)."""
    return d.strftime("%m/%d/%Y")


def _parse_date(s) -> date:
    """Parse 'MM/DD/YYYY' string or date object → date."""
    if isinstance(s, date):
        return s
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


def _lookup_customer(client_name: str):
    try:
        import os

        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.enums import OrderType

        if not os.path.exists(CUSTOMER_DB_PATH):
            return None
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        # find_by_name_any_type does case-insensitive name match across all order types,
        # which catches manually-entered records where order_type casing may differ.
        return repo.find_by_name(client_name, OrderType.ACM) or repo.find_by_name_any_type(
            client_name
        )
    except Exception as exc:
        print(f"[CUSTOMER] Warning: lookup failed: {exc}")
        return None


def _upsert_customer(
    customer_id: str, client_name: str, separation: tuple, billing_type: str = "direct"
) -> None:
    try:
        import os

        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        from src.domain.enums import OrderType

        if not os.path.exists(CUSTOMER_DB_PATH):
            return
        repo = CustomerRepository(CUSTOMER_DB_PATH)
        repo.save(
            Customer(
                customer_id=customer_id,
                customer_name=client_name,
                order_type=OrderType.ACM,
                billing_type=billing_type,
                separation_customer=separation[0],
                separation_order=separation[1],
                separation_event=separation[2],
            )
        )
        print(f"[CUSTOMER DB] ✓ Saved: {client_name} → ID {customer_id}")
    except Exception as exc:
        print(f"[CUSTOMER DB] Warning: could not save: {exc}")


# ─── PDF flight planning ──────────────────────────────────────────────────────


def parse_cutoff(raw: str) -> str:
    """'5p' / '5pm' / '17:00' → 'HH:MM'; '' / 'none' → '' (no cutoff)."""
    raw = (raw or "").strip().lower()
    if not raw or raw == "none":
        return ""
    return EtereClient.parse_time_range(f"6a-{raw}")[1]


def plan_cutoff_ranges(
    d_from: date,
    d_to: date,
    days: str,
    time_from: str,
    time_to: str,
    spots: int,
    cutoff: str = "",
) -> list[dict]:
    """A single-range line → Etere ranges with explicit caps.

    Spots are spread evenly over the pattern's active days (earlier days take the
    remainder). With a cutoff that the window overruns on an active last day, the
    last day's share becomes its own line ending at the cutoff and the rest keeps
    the full window through the day before (Lee 10/28: "11/2 runs its full day and
    11/3 runs only until 5p")."""
    from math import ceil

    from browser_automation.etere_direct_client import parse_day_bits
    from browser_automation.line_planner import active_days

    bits = parse_day_bits(days)
    n = active_days(d_from, d_to, bits)
    if n == 0:
        raise ValueError(f"no {days} day between {d_from} and {d_to}")
    if spots <= 0:
        return []
    last_active = active_days(d_to, d_to, bits) == 1
    if cutoff and time_to > cutoff and last_active:
        if n == 1:
            return [
                {
                    "date_from": d_from,
                    "date_to": d_to,
                    "time_to": cutoff,
                    "spots": spots,
                    "max_daily": spots,
                    "tag": f"ends {cutoff}",
                }
            ]
        per_day = spots // n  # the last day never carries the remainder
        head = spots - per_day
        out = [
            {
                "date_from": d_from,
                "date_to": d_to - timedelta(days=1),
                "time_to": time_to,
                "spots": head,
                "max_daily": ceil(head / (n - 1)),
                "tag": f"{n - 1} day(s), full window",
            }
        ]
        if per_day:
            out.append(
                {
                    "date_from": d_to,
                    "date_to": d_to,
                    "time_to": cutoff,
                    "spots": per_day,
                    "max_daily": per_day,
                    "tag": f"last day ends {cutoff}",
                }
            )
        return out
    return [
        {
            "date_from": d_from,
            "date_to": d_to,
            "time_to": time_to,
            "spots": spots,
            "max_daily": ceil(spots / n),
            "tag": f"{n} day(s)",
        }
    ]


def _precedent_contracts(customer_id, limit: int = 3) -> list[tuple[str, str]]:
    """(code, description) of the customer's latest contracts — shown above the naming prompts."""
    try:
        from browser_automation.etere_direct_client import connect

        conn = connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT TOP %d COD_CONTRATTO, DESCRIZIONE FROM CONTRATTITESTATA "
                "WHERE COMMITTENTE = %s ORDER BY ID_CONTRATTITESTATA DESC",
                (int(limit), int(customer_id)),
            )
            return [(r[0], r[1]) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


# ─── Input Gather ─────────────────────────────────────────────────────────────


def gather_acm_inputs(xlsx_path: str) -> Optional[dict]:
    """
    Gather user inputs for an ACM order before processing.

    Returns dict with: customer_id, separation, spot_duration, contract_code, description.
    Returns None to abort.
    """
    order = parse_acm(xlsx_path)
    is_pdf = bool(order.client_name)

    print(f"\n{'=' * 60}")
    print(f"ACM: {order.agency}" + (f"   client: {order.client_name}" if is_pdf else ""))
    if order.order_date:
        print(f"Date: {order.order_date:%B %d, %Y}")
    print(f"Markets: {', '.join(m.market_code for m in order.market_sections)}")
    if is_pdf:
        print(
            f"Flight: {order.flight_start} - {order.flight_end}   billing {order.billing_cycle or 'Broadcast'}   NET rates"
        )
        for desc, units, amount in order.production:
            print(
                f"Production: {desc}  ({units} unit(s))  ${amount:,.2f}  → Production box on the first paid line"
            )
        if order.no_separation:
            print("Sheet says: Airtime: No separation requirement")

    for mkt in order.market_sections:
        print(f"\n  {mkt.market_code}:")
        for ln in mkt.lines:
            tag = "BNS" if ln.is_bonus else "   "
            rate_str = f"${ln.rate:.0f}/sp" if ln.rate else "      "
            spots = " + ".join(str(s) for s in ln.week_spots)
            print(
                f"    {tag}  {ln.language_block.strip():<40} {ln.daypart:<22} {rate_str}  [{spots}]"
            )

    # ── Customer lookup ───────────────────────────────────────────────────
    customer_id: Optional[int] = None
    separation = (15, 0, 0)

    billing_type = "direct"  # ACM default; overridden by DB record if present
    cust = _lookup_customer(order.agency)
    if cust:
        customer_id = cust.customer_id
        billing_type = cust.billing_type or "direct"
        s0 = 25 if cust.separation_customer == 30 else cust.separation_customer
        separation = (s0, cust.separation_order, cust.separation_event)
        print(
            f"\n[CUSTOMER] ✓ Found in DB → ID {customer_id}, billing={billing_type}, sep {separation}"
        )
        if is_pdf and order.no_separation:
            separation = NO_SEPARATION_DEFAULT
            print(
                f"[SEPARATION] sheet has no separation requirement → offering {separation} (Lee 10/28); confirm at the prompt"
            )
    else:
        print(f"\n[CUSTOMER] '{order.agency}' not found in DB.")
        raw_id = input("  Enter Etere customer ID: ").strip()
        if not raw_id.isdigit():
            print("  ✗ Invalid ID — aborting")
            return None
        customer_id = int(raw_id)
        raw_bt = input("  Billing type [direct/agency]: ").strip().lower()
        billing_type = "agency" if raw_bt.startswith("a") else "direct"
        save = (
            input(f"  Save '{order.agency}' (ID {customer_id}, {billing_type}) to DB? (y/n): ")
            .strip()
            .lower()
        )
        if save in ("y", "yes"):
            _upsert_customer(str(customer_id), order.agency, separation, billing_type)

    # ── Spot duration ─────────────────────────────────────────────────────
    raw = input("\n  Spot duration in seconds [30]: ").strip()
    spot_duration = int(raw) if raw.isdigit() else 30

    # ── PDF orders: dates and the last-day cutoff ────────────────────────
    start_override = end_override = None
    end_cutoff = ""
    if is_pdf:
        from browser_automation.cbm_automation import confirm_end_date
        from browser_automation.line_planner import confirm_start_date, parse_date

        start_override = confirm_start_date(order.flight_start, None, always_ask=True)
        if start_override is None:
            print("  ✗ No flight start date — aborting")
            return None
        end_override = confirm_end_date(parse_date(order.flight_end), start_override)
        raw = input(
            "  Last day ends at a clock time? (e.g. 5p; lines running past it split) [none]: "
        ).strip()
        try:
            end_cutoff = parse_cutoff(raw)
        except Exception:
            print(f"  ✗ Could not read '{raw}' as a time — no cutoff applied")
            end_cutoff = ""
        print(
            f"\n  Etere lines for {_fmt_mmddyyyy(start_override)} – {_fmt_mmddyyyy(end_override)}"
            + (f", last day ends {end_cutoff}" if end_cutoff else "")
            + ":"
        )
        for mkt in order.market_sections:
            for line in mkt.lines:
                if line.is_bonus or line.total_spots == 0:
                    continue
                t_from, t_to = EtereClient.parse_time_range(line.time)
                for rng in plan_cutoff_ranges(
                    start_override,
                    end_override,
                    line.days,
                    t_from,
                    t_to,
                    line.total_spots,
                    end_cutoff,
                ):
                    print(
                        f"    {mkt.market_code} {line.description:<34} {_fmt_mmddyyyy(rng['date_from'])}-"
                        f"{_fmt_mmddyyyy(rng['date_to'])} {t_from}-{rng['time_to']}  {rng['spots']:>3} spots, "
                        f"{rng['max_daily']}/day  ({rng['tag']})"
                    )

    # ── Single contract code + description (covers all markets) ──────────
    code_name = (cust.code_name or "ACM") if cust else "ACM"
    desc_name = (
        (cust.description_name or "American Community Media")
        if cust
        else "American Community Media"
    )

    # Use overall flight range across all markets for the default description
    start_d = _parse_date(order.flight_start) if order.flight_start else None
    end_d = _parse_date(order.flight_end) if order.flight_end else None
    flight_str = ""
    if start_d and end_d:
        flight_str = (
            f"{start_d.month}/{start_d.day}-{end_d.month}/{end_d.day}/{end_d.strftime('%y')}"
        )
    default_code = code_name
    default_desc = f"{desc_name} {flight_str}".strip()
    if is_pdf:
        # ACM 2881 shape: 'ACM 2606' / 'ACM CA vs Hate 2606' (Lee); the precedents print above the prompt
        from browser_automation.line_planner import broadcast_yymm

        yymm = broadcast_yymm(start_override)
        default_code = f"{code_name} {yymm}"
        default_desc = f"{code_name} {order.client_name} {yymm}"
        for code, desc in _precedent_contracts(customer_id):
            print(f"  (precedent)  {code:<18} {desc}")

    print()
    raw = input(f"  Contract code [{default_code}]: ").strip()
    contract_code = raw or default_code

    raw = input(f"  Description [{default_desc}]: ").strip()
    description = raw or default_desc

    return {
        "customer_id": customer_id,
        "billing_type": billing_type,
        "separation": separation,
        "spot_duration": spot_duration,
        "contract_code": contract_code,
        "description": description,
        "start_date_override": _fmt_mmddyyyy(start_override) if start_override else "",
        "end_date_override": _fmt_mmddyyyy(end_override) if end_override else "",
        "end_cutoff": end_cutoff,
    }


# ─── Direct DB Entry ──────────────────────────────────────────────────────────


def _create_acm_contract(order: AcmOrder, inputs: dict, dry_run: bool = False) -> Optional[str]:
    """
    Enter an ACM order into Etere as a single contract.

    All market sections are written as lines under one contract header.
    Returns the contract code on success, None on failure (rolls back).
    """
    from browser_automation.cbm_automation import MEDIA_CENTER
    from browser_automation.etere_direct_client import EtereDirectClient, connect
    from browser_automation.line_planner import verify_production_charge
    from browser_automation.ros_definitions import ROS_SCHEDULES

    customer_id = inputs.get("customer_id")
    if customer_id is None:
        print("[ACM] ✗ No customer_id")
        return None

    separation = inputs.get("separation", (15, 0, 0))
    billing_type = inputs.get("billing_type", "direct")
    contract_code = inputs.get("contract_code", "ACM")
    description = inputs.get("description", "")
    spot_duration = inputs.get("spot_duration", 30)
    duration_str = str(spot_duration)
    end_cutoff = inputs.get("end_cutoff") or ""
    production_pending = round(
        float(order.production_total or 0), 2
    )  # → the first paid line's Production box
    media_center_id = (
        MEDIA_CENTER.get((order.billing_cycle or "").upper()) if order.billing_cycle else None
    )

    # Overall flight range across all markets
    flight_start_d = _parse_date(order.flight_start) if order.flight_start else None
    flight_end_d = _parse_date(order.flight_end) if order.flight_end else None
    if inputs.get("start_date_override"):
        flight_start_d = _parse_date(inputs["start_date_override"])
    if inputs.get("end_date_override"):
        flight_end_d = _parse_date(inputs["end_date_override"])
    if not flight_start_d or not flight_end_d:
        print("[ACM] ✗ Could not determine overall flight range")
        return None

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=contract_code,
            description=description,
            customer_id=int(customer_id),
            contract_date=flight_start_d,
            contract_end_date=flight_end_d,
            contract_type=1,
            billing_type=billing_type,
            allow_rename=True,
            media_center_id=media_center_id,
        )
        print(f"[ACM] ✓ Contract header: ID={contract_id}  code='{contract_code}'")

        line_count = 0

        for mkt in order.market_sections:
            flight_end_str = _fmt_mmddyyyy(mkt.flight_end or flight_end_d)

            for line in mkt.lines:
                if line.total_spots == 0:
                    continue

                is_bonus = line.is_bonus
                booking_code = 10 if is_bonus else 2

                if is_bonus:
                    lang = line.language_block.strip()
                    ros = ROS_SCHEDULES.get(lang)
                    if ros:
                        days = ros["days"]
                        time_raw = ros["time"]
                    else:
                        days, time_raw = "M-Su", "6a-11:59p"
                        print(f"  [WARN] No ROS schedule for '{lang}' — using {days} {time_raw}")
                    desc = f"BNS {lang} ROS"
                else:
                    days = line.days
                    time_raw = line.time
                    days, _ = EtereClient.check_sunday_6_7a_rule(days, time_raw)
                    label = line.language_block.strip()
                    desc = f"{label} {line.daypart}"
                    if len(desc) > 60:
                        desc = desc[:60]

                time_from, time_to = EtereClient.parse_time_range(time_raw)
                time_range = f"{time_from}-{time_to}"

                if line.week_end is not None:
                    # Proposal PDF: one date-range column → ranges with explicit caps,
                    # split at the last-day cutoff (Lee 10/28), production on the first paid line.
                    for rng in plan_cutoff_ranges(
                        flight_start_d,
                        flight_end_d,
                        days,
                        time_from,
                        time_to,
                        line.total_spots,
                        end_cutoff,
                    ):
                        line_count += 1
                        prod = 0.0
                        if not is_bonus and production_pending:
                            prod, production_pending = production_pending, 0.0
                        print(
                            f"  [LINE {line_count}] {mkt.market_code} {desc}: "
                            f"{_fmt_mmddyyyy(rng['date_from'])}–{_fmt_mmddyyyy(rng['date_to'])} "
                            f"{time_from}-{rng['time_to']}  {rng['spots']} spots, {rng['max_daily']}/day ({rng['tag']})"
                            + (f"  + Production ${prod:,.2f}" if prod else "")
                        )
                        line_id = client.add_contract_line(
                            market=mkt.market_code,
                            days=days,
                            time_range=f"{time_from}-{rng['time_to']}",
                            description=desc,
                            rate=line.rate,
                            total_spots=rng["spots"],
                            spots_per_week=rng["spots"],
                            max_daily_run=rng["max_daily"],
                            date_from=rng["date_from"],
                            date_to=rng["date_to"],
                            duration=duration_str,
                            is_bonus=is_bonus,
                            booking_code=booking_code,
                            separation_intervals=separation,
                            production_cost=prod,
                        )
                        if prod:
                            verify_production_charge(conn.cursor(), line_id, prod, label="ACM")
                    continue
                week_start_strs = [_fmt_mon_dd(d) for d in line.week_dates]
                ranges = EtereClient.consolidate_weeks(
                    line.week_spots,
                    week_start_strs,
                    flight_end=flight_end_str,
                )

                for rng in ranges:
                    total_spots = rng["spots_per_week"] * rng["weeks"]
                    line_count += 1
                    print(
                        f"  [LINE {line_count}] {mkt.market_code} {desc}: "
                        f"{rng['start_date']}–{rng['end_date']} "
                        f"({rng['spots_per_week']}/wk×{rng['weeks']}w={total_spots})"
                    )
                    client.add_contract_line(
                        market=mkt.market_code,
                        days=days,
                        time_range=time_range,
                        description=desc,
                        rate=line.rate,
                        total_spots=total_spots,
                        spots_per_week=rng["spots_per_week"],
                        date_from=_parse_date(rng["start_date"]),
                        date_to=_parse_date(rng["end_date"]),
                        duration=duration_str,
                        is_bonus=is_bonus,
                        booking_code=booking_code,
                        separation_intervals=separation,
                    )

        if production_pending:
            raise RuntimeError(
                f"ACM: ${production_pending:,.2f} of production was never attached — no paid airtime line was created. Rolling back."
            )
        if dry_run:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA = %s", (contract_id,)
            )
            print(
                f"[ACM] DRY RUN: {cur.fetchone()[0]} line(s) written in the transaction — rolling back"
            )
            conn.rollback()
            conn.close()
            return contract_code
        conn.commit()
        conn.close()
        print(f"[ACM] ✓ {line_count} lines committed across {len(order.market_sections)} markets.")
        return contract_code

    except Exception as exc:
        print(f"[ACM] ✗ {exc}")
        import traceback

        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


def run_acm_order(order: AcmOrder, inputs: dict) -> list[tuple[str, bool]]:
    """
    Process an ACM order as a single contract covering all markets.

    Returns list with one (contract_code, success) tuple.
    """
    code = _create_acm_contract(order, inputs)
    label = inputs.get("contract_code") or "ACM"
    return [(label, code is not None)]


if (
    __name__ == "__main__"
):  # python -m browser_automation.acm_automation <pdf> --dry-run [--cutoff 5p] [--sep 3,0,0]
    import argparse

    ap = argparse.ArgumentParser(
        description="ACM proposal PDF: plan and dry-run entry (rolled back)"
    )
    ap.add_argument("path")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cutoff", default="", help="last day ends at, e.g. 5p")
    ap.add_argument("--customer", type=int, default=438)
    ap.add_argument("--sep", default="3,0,0")
    a = ap.parse_args()
    if not a.dry_run:
        raise SystemExit("live entry runs through the orchestrator; pass --dry-run here")
    o = parse_acm(a.path)
    from browser_automation.line_planner import broadcast_yymm, parse_date

    start = parse_date(o.flight_start)
    yymm = broadcast_yymm(start)
    inputs = {
        "customer_id": a.customer,
        "billing_type": "direct",
        "separation": tuple(int(x) for x in a.sep.split(",")),
        "spot_duration": 30,
        "contract_code": f"ACM {yymm}",
        "description": f"ACM {o.client_name} {yymm}",
        "start_date_override": o.flight_start,
        "end_date_override": o.flight_end,
        "end_cutoff": parse_cutoff(a.cutoff),
    }
    print(inputs)
    print("result:", _create_acm_contract(o, inputs, dry_run=True))
