"""
The Vida Agency direct-DB automation (advertiser: Puget Sound Energy).

Vida is an AGENCY parser: the agency is fixed (The Vida Agency → ANAGRAF 260)
and the advertiser is resolved among the clients linked to it (Puget Sound
Energy = 344, WA Health Benefit Exchange = 360, WA HAF = 261) by the words of
the proposal's "Project" line.

**No agency commission on these contracts** (AE note 2026-10-12; precedent
Vida PSE 2410 / 2411 carry P_AGENZIA 0.00 while the agency row in ANAGRAF says
15%). The header is written with an explicit `agency_pct=0.0`, which
`create_contract_header` honours over the ANAGRAF lookup; rates are NET and
enter verbatim, so gross == net.

**One contract per flight** (Lee 2026-10-12): the proposal packs October and
December into one grid and March 2027 on page 2; each flight gets its own
header, code `Vida PSE <YYMM>` and description `Puget Sound Energy - <project
tail> <YYMM>` (YYMM = broadcast month of the flight's start).

**Late / mid-week starts** go through the shared `line_planner`: the start date
is ALWAYS asked for the first flight (these orders enter late — this one
opened Tuesday 10/13 on a Monday 10/12 grid), and every flight is planned from
its real first day (December opens Tue 12/1 on a 11/30 week), so a short first
or last week gets its own max-per-day line.

**Dual daypart → ONE line on the union window** ("10a-11a/12p-1p" → 10a-1p),
the Ntooitive / HPSJ precedent. Bonus ROS → the Vietnamese ROS window.

**Production** (the sheet's Net Amt, $1,000 here) rides the FIRST flight's first
paid line's Production box → CONTRATTISPESE, verified inside the transaction.

Separation precedent: 5 / 0 / 0 (Vida PSE 2410 / 2411) — needed to fit four
spots a day inside a one-hour show on the compressed weeks.

**Second client, second shape (2026-11-09): WA Health Benefit Exchange (360)
as an EXCEL proposal.** Oracle: 1576 `Vida WAHBE 2411` "WA Health Benefit
Exchange Phase 1" + 1577 `Vida WAHBE 2412` "… Phase 2" (CUSTOMERREF "Phase N",
P_AGENZIA 15, Broadcast, 15/0/0, every line Rotation). What generalised:

- The commission is the SHEET's (`order.agency_pct`, derived from its own
  gross/net columns: 15 here, 0 on a PSE NET sheet), cross-checked against the
  ANAGRAF agency row at the gather; the header is written with it explicitly.
- Naming and separation defaults come from the customer's own prior Vida
  contracts in Etere (`_precedent`: short name of the latest code, the
  description prefix the descriptions share, the lines' separation), the
  customer DB row first when it has one; the three latest pairs print above
  the prompt (the opAD pattern). A phase names the description and the
  customer ref: `Vida WAHBE 2611` / "WA Health Benefit Exchange Phase 1".
- Every line's blocks are pruned to its LANGUAGE's block prefixes right after
  the SP loads them ("C -", "M -", "V -"; Chinese = C + M): 1577's Cantonese
  "7p-8p; 11:30p-12a" row entered as ONE 7p-12a line carrying only the five
  "C -" blocks while the Chinese ROS bonus line kept C and M. The union window
  is the Ntooitive/HPSJ rule; the prune is what keeps the Mandarin shows out
  of a Cantonese buy (and Japanese 10a-11a out of a Vietnamese 10a-1p line).
- Chinese ROS = M-Su 7p-11:59p (1577 line 43827), not the all-day envelope.
- "Added value … Interview segment" ($0) goes on the first header's note.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
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
from browser_automation.parsers.vida_parser import VidaFlight, VidaLine, VidaOrder, parse_vida
from browser_automation.ros_definitions import ROS_SCHEDULES

VIDA_DEFAULT_SEPARATION = (5, 0, 0)  # customer, order, event — Vida PSE 2410/2411
VIDA_AGENCY_PCT = 0.0  # PSE (AE 2026-10-12: no agency commission); a sheet overrides it
_STOP = {"the", "of", "and", "inc", "llc", "co", "project", "flex", "campaign", "energy"}
_FPM = 29.97 * 60  # frames per minute (separation columns are stored in frames)

# Bonus ROS windows where the Vida precedent differs from the shared ROS table:
# 1577 line 43827 "Bonus Chinese ROS" = 7p-12a (C + M blocks), not the all-day envelope.
VIDA_ROS = {"Chinese": {"days": "M-Su", "time": "7p-11:59p"}}

# Every Vida line schedules as Rotation (1576/1577: PRENOTAZIONE 1 on all 18 lines);
# the client's default would leave a one-hour window on first-fit Priority.
VIDA_SCHEDULING_TYPE = 1

# Block-name prefixes per language (data-reference "Language Codes"); a line's
# CONTRATTIFASCE rows are pruned to these after Etere loads the window's blocks.
LANGUAGE_BLOCK_PREFIXES = {
    "Mandarin": ("M",),
    "Cantonese": ("C",),
    "Chinese": ("C", "M"),
    "Vietnamese": ("V",),
    "Filipino": ("T",),
    "Tagalog": ("T",),
    "Korean": ("K",),
    "Japanese": ("J",),
    "Hmong": ("Hm",),
    "Punjabi": ("P",),
    "South Asian": ("SA",),
    "Hindi": ("SA",),
}


# ─── Planning ─────────────────────────────────────────────────────────────────


def _ampm(hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    suffix = "a" if h < 12 else "p"
    h12 = h if 1 <= h <= 12 else (h - 12 if h > 12 else 12)
    return f"{h12}{'' if m == 0 else f':{m:02d}'}{suffix}"


def line_window(ln: VidaLine) -> tuple[str, str, str]:
    """(days, 'HH:MM-HH:MM', 'compact label') for a line. A dual daypart is the
    union of its pieces (parse_time_range's semicolon rule: earliest start +
    latest end). A bonus row takes the language's ROS window."""
    if ln.is_bonus:
        ros = VIDA_ROS.get(ln.language or "") or ROS_SCHEDULES.get(ln.language or "")
        if ros:
            days, time_raw = ros["days"], ros["time"]
        else:
            days, time_raw = "M-Su", "6a-11:59p"
            print(f"  [WARN] No ROS window for '{ln.language}' — using {days} {time_raw}")
    else:
        days, time_raw = ln.days, "; ".join(ln.time_ranges)
        days, _ = EtereClient.check_sunday_6_7a_rule(days, time_raw)
    t_from, t_to = EtereClient.parse_time_range(time_raw)
    label = f"{_ampm(t_from)}-{_ampm(t_to)}"
    return days, f"{t_from}-{t_to}", label


def line_description(ln: VidaLine, days: str, label: str) -> str:
    """Precedent 'M-Su 11a-12n Viet' / 'BONUS M-Su Viet ROS' → 'M-Su 11a-12p Viet
    News/Variety', 'M-Su 10a-1p Viet Drama', 'BNS M-Su Viet ROS'."""
    lang = ln.language or "Viet"
    short_lang = "Viet" if lang.lower() == "vietnamese" else lang
    if ln.is_bonus:
        return f"BNS {days} {short_lang} ROS"[:60]
    return f"{days} {label} {short_lang} {ln.program_short}".strip()[:60]


def flight_plan(flight: VidaFlight, start_from: date) -> list[tuple]:
    """(line, days, time_range, description, ranges, notes) for every airtime line
    of one flight — the single source of truth the gather preview and the entry
    both walk, so what Lee approves is what gets written."""
    plan: list[tuple] = []
    for ln in flight.lines:
        if ln.total_spots == 0:
            continue
        days, time_range, label = line_window(ln)
        desc = line_description(ln, days, label)
        consolidated = EtereClient.consolidate_weeks(
            ln.week_spots,
            [WeekCol(d) for d in ln.week_dates],
            flight_end=fmt_mmddyyyy(flight.end),
        )
        ranges, notes = plan_ranges(consolidated, days, start_from, flight.end)
        plan.append((ln, days, time_range, desc, ranges, notes))
    return plan


def naming_from_contracts(rows: list[tuple[str, str]]) -> tuple[str, str]:
    """[(code, description)] of a customer's prior Vida contracts, latest first →
    (short, desc_prefix). 'Vida WAHBE 2412' → 'WAHBE'; descriptions minus their
    trailing yymm / 'Phase N' → the longest word prefix they share ('WA Health
    Benefit Exchange'; PSE: 'Puget Sound Energy -')."""
    if not rows:
        return "", ""
    m = re.match(r"(?i)^vida\s+(.+?)\s+\d{4}$", rows[0][0].strip())
    short = m.group(1) if m else ""
    stems = []
    for _code, desc in rows:
        d = re.sub(r"\s+\d{4}$", "", (desc or "").strip())
        d = re.sub(r"(?i)\s*\bphase\s*\d+\s*$", "", d).strip()
        if d:
            stems.append(d.split())
    if not stems:
        return short, ""
    common: list[str] = []
    for words in zip(*stems):
        if len({w.lower() for w in words}) == 1:
            common.append(words[0])
        else:
            break
    prefix = " ".join(common) if common else " ".join(stems[0])
    return short, prefix


def default_code_desc(
    order: VidaOrder, flight: VidaFlight, start: date, cust=None, precedent: Optional[dict] = None
) -> tuple[str, str, str, str]:
    """Code 'Vida <SHORT> <yymm>'; description '<prefix> Phase N' when the sheet
    names phases (1576/1577), else '<prefix> <project tail> <yymm>' (PSE: 'Puget
    Sound Energy - Flex 2611'). The customer DB row wins, then the customer's own
    prior contracts, then the PSE fallback. Returns (code, desc, short, desc_prefix)."""
    yymm = broadcast_yymm(start)
    prec = precedent or {}
    short = (cust.code_name if cust and getattr(cust, "code_name", "") else "") or prec.get(
        "short", ""
    )
    desc_prefix = (
        cust.description_name if cust and getattr(cust, "description_name", "") else ""
    ) or prec.get("desc_prefix", "")
    if not short:
        short = (
            "PSE"
            if re.search(r"(?i)puget\s+sound", order.project)
            else "".join(
                w[0] for w in (prec.get("customer_name") or order.project).split()[:5]
            ).upper()
        )
    if not desc_prefix:
        if re.search(r"(?i)puget\s+sound", order.project):
            tail = re.sub(r"(?i)^puget\s+sound\s*", "", order.project).strip() or order.project
            desc_prefix = f"Puget Sound Energy - {tail}"
        else:
            desc_prefix = prec.get("customer_name") or order.project
    if flight.phase:
        desc = f"{desc_prefix} {flight.phase}"
    else:
        tail = " ".join(w for w in order.project.split() if w.lower() not in _tokens(desc_prefix))
        tail = "" if _tokens(tail) <= _tokens(desc_prefix) else tail
        desc = f"{desc_prefix} {tail} {yymm}" if tail else f"{desc_prefix} {yymm}"
    return f"Vida {short} {yymm}", " ".join(desc.split()), short, desc_prefix


def blocks_to_drop(
    blocks: dict[int, str], prefixes: tuple[str, ...]
) -> tuple[list[int], list[int]]:
    """{id_fasce: block name} + a language's prefixes → (keep ids, drop ids).
    A block belongs to a language when its name starts '<PREFIX> - ' (the
    Etere block-tab convention: 'C - Cantonese Primetime News')."""
    pat = re.compile(r"^\s*(?:" + "|".join(re.escape(p) for p in prefixes) + r")\s*-", re.I)
    keep = [i for i, n in blocks.items() if pat.match(n or "")]
    drop = [i for i in blocks if i not in keep]
    return keep, drop


def _prune_blocks_to_language(cur, line_id: int, language: str, desc: str) -> tuple[int, int]:
    """Drop the CONTRATTIFASCE rows Etere just loaded for OTHER languages' blocks
    (1577 precedent). Refuses — so the caller rolls back — if nothing would
    remain. Returns (kept, dropped)."""
    prefixes = LANGUAGE_BLOCK_PREFIXES.get(language or "")
    if not prefixes:
        return -1, 0
    cur.execute(
        "SELECT cf.ID_FASCE, tb.Name FROM CONTRATTIFASCE cf "
        "JOIN traffic_block tb ON tb.ID_TrafficBlock = cf.ID_FASCE "
        "WHERE cf.ID_CONTRATTIRIGHE = %s",
        (int(line_id),),
    )
    blocks = {int(r[0]): str(r[1] or "").strip() for r in cur.fetchall()}
    keep, drop = blocks_to_drop(blocks, prefixes)
    if blocks and not keep:
        raise RuntimeError(
            f"Vida: line {line_id} '{desc}' loaded {len(blocks)} block(s) and none is "
            f"{'/'.join(prefixes)} - …: {sorted(set(blocks.values()))[:6]} — the window "
            "does not cover this language; rolling back"
        )
    if drop:
        cur.execute(
            "DELETE FROM CONTRATTIFASCE WHERE ID_CONTRATTIRIGHE = %s AND ID_FASCE IN ("
            + ",".join(str(i) for i in drop)
            + ")",
            (int(line_id),),
        )
        names = sorted({blocks[i] for i in drop})
        print(
            f"           ↳ blocks: kept {len(keep)} {'/'.join(prefixes)}, dropped {len(drop)}: "
            + ", ".join(names[:5])
            + (" …" if len(names) > 5 else "")
        )
    return len(keep), len(drop)


# ─── Customer resolution ──────────────────────────────────────────────────────


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t}


def _resolve_customer(project: str, agency_id: int) -> Optional[dict]:
    """Project words → the ANAGRAF client linked to the agency with the best
    token overlap ('Puget Sound Flex' → Puget Sound Energy)."""
    try:
        from browser_automation.etere_direct_client import connect

        conn = connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT ID_ANAGRAF, RAG_SOCIAL FROM ANAGRAF WHERE AGENZIA = %s", (int(agency_id),)
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as exc:
        print(f"[CUSTOMER] ANAGRAF lookup failed: {exc}")
        return None
    want = _tokens(project) - _STOP
    best, best_score = None, 0
    for anid, name in rows:
        score = len(want & (_tokens(name) - _STOP))
        if score > best_score:
            best, best_score = (int(anid), str(name).strip()), score
    return {"id": best[0], "name": best[1]} if best else None


def _precedent(customer_id: int) -> Optional[dict]:
    """The customer's prior Vida contracts in Etere → naming + separation
    defaults and the three latest (code, description) pairs for display."""
    try:
        from browser_automation.etere_direct_client import connect

        conn = connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT t.ID_CONTRATTITESTATA, t.COD_CONTRATTO, t.DESCRIZIONE, a.RAG_SOCIAL, "
            "t.P_AGENZIA, t.CUSTOMERREF FROM CONTRATTITESTATA t "
            "JOIN ANAGRAF a ON a.ID_ANAGRAF = t.COMMITTENTE "
            "WHERE t.COMMITTENTE = %s AND t.COD_CONTRATTO LIKE 'Vida %%' "
            "ORDER BY t.DATA_INIZIO DESC, t.ID_CONTRATTITESTATA DESC",
            (int(customer_id),),
        )
        rows = [
            (
                int(r[0]),
                str(r[1]).strip(),
                str(r[2] or "").strip(),
                str(r[3]).strip(),
                float(r[4] or 0),
                str(r[5] or "").strip(),
            )
            for r in cur.fetchall()
        ]
        sep = None
        if rows:
            cur.execute(
                "SELECT Interv_Committente, INTERVALLO, INTERV_CONTRATTO FROM CONTRATTIRIGHE "
                "WHERE ID_CONTRATTITESTATA = %s AND ID_BOOKINGCODE = 2 ORDER BY ID_CONTRATTIRIGHE",
                (rows[0][0],),
            )
            lines = cur.fetchall()
            if lines:
                sep = tuple(int(round(float(lines[0][k] or 0) / _FPM)) for k in range(3))
        conn.close()
    except Exception as exc:
        print(f"[CUSTOMER] Etere precedent lookup failed: {exc}")
        return None
    if not rows:
        return None
    short, prefix = naming_from_contracts([(r[1], r[2]) for r in rows])
    return {
        "customer_name": rows[0][3],
        "short": short,
        "desc_prefix": prefix,
        "agency_pct": rows[0][4],
        "separation": sep,
        "recent": [(r[1], r[2], r[5]) for r in rows[:3]],
    }


def _agency_commission(agency_id: int) -> Optional[float]:
    try:
        from browser_automation.etere_direct_client import connect

        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT Commissione FROM ANAGRAF WHERE ID_ANAGRAF = %s", (int(agency_id),))
        row = cur.fetchone()
        conn.close()
        return float(row[0] or 0) if row else None
    except Exception as exc:
        print(f"[CUSTOMER] ANAGRAF commission lookup failed: {exc}")
        return None


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
    name: str, customer_id: int, code_name: str, description_name: str, separation: tuple
) -> None:
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        from src.domain.enums import OrderType

        CustomerRepository(CUSTOMER_DB_PATH).save(
            Customer(
                customer_id=str(customer_id),
                customer_name=name,
                order_type=OrderType.VIDA,
                billing_type="agency",
                code_name=code_name,
                description_name=description_name,
                separation_customer=separation[0],
                separation_order=separation[1],
                separation_event=separation[2],
                default_market="SEA",
            )
        )
    except Exception as exc:
        print(f"[CUSTOMER] customer DB upsert failed (non-fatal): {exc}")


# ─── Gather ───────────────────────────────────────────────────────────────────


def _print_plan(plan: list[tuple]) -> tuple[int, list[str]]:
    entered = 0
    all_notes: list[str] = []
    for _ln, days, time_range, desc, ranges, notes in plan:
        for rng in ranges:
            entered += rng["spots_per_week"] * rng["weeks"]
            tag = f"   ← {rng['tag']}" if rng["tag"] else ""
            print(
                f"      {desc[:30]:<30} {days:<5} {time_range}  "
                f"{fmt_mmddyyyy(rng['date_from'])}–{fmt_mmddyyyy(rng['date_to'])}"
                f"  {rng['spots_per_week']}/wk×{rng['weeks']}w"
                f"  max {rng['max_daily']}/day{tag}"
            )
        all_notes.extend(f"{desc[:30]}: {n}" for n in notes)
    return entered, all_notes


def gather_vida_inputs(source_path: str) -> Optional[dict]:
    """Gather inputs for a Vida Agency proposal. Returns dict or None to abort."""
    from browser_automation.etere_direct_client import AGENCY_IDS

    order = parse_vida(source_path)
    agency_id = AGENCY_IDS["VIDA"]
    agency_pct = float(order.agency_pct)

    print(f"\n{'=' * 64}")
    print(f"Agency:     {order.agency}  (fixed — Etere agency ID {agency_id})")
    print(f"Project:    {order.project}")
    print(f"Market:     {order.market_code or '?'}   ({order.market_label})")
    if order.order_date:
        print(f"Dated:      {order.order_date:%B %d, %Y}")
    if order.rates_are_net:
        print(
            f"Money:      NET rates, entered verbatim ({agency_pct:g}% commission ⇒ gross == net)"
        )
    else:
        print(
            f"Money:      GROSS rates, entered verbatim; the sheet nets them at "
            f"{agency_pct:g}% (${order.entered_airtime:,.2f} gross → ${order.net_airtime:,.2f} net)"
        )
    print(f"Flights:    {len(order.flights)} → one contract each")
    for n in order.notes:
        print(f"Added value: {n}  → header note, not airtime")

    market_code = order.market_code
    if not market_code:
        raw = input("  Market code (CVC/SFO/LAX/SEA/HOU/CMP/WDC/NYC): ").strip().upper()
        if raw not in ("CVC", "SFO", "LAX", "SEA", "HOU", "CMP", "WDC", "NYC", "MMT"):
            print("  ✗ Unknown market — aborting")
            return None
        market_code = raw

    for i, fl in enumerate(order.flights, 1):
        print(
            f"\n  Flight {i}: {fl.label}  ({len(fl.week_dates)} weeks)  "
            f"${fl.entered_total:,.2f} entered / ${fl.net_total:,.2f} net"
        )
        for ln in fl.lines:
            days, _tr, label = line_window(ln)
            tag = "BNS" if ln.is_bonus else "   "
            rate = f"${ln.rate:.2f}" if ln.rate else "  bonus"
            pieces = ln.time_ranges
            union = f"  ← union of {' + '.join(pieces)}" if len(pieces) > 1 else ""
            pref = LANGUAGE_BLOCK_PREFIXES.get(ln.language or "")
            blocks = f"  blocks {'/'.join(pref)} -" if pref else ""
            print(
                f"    {tag} :{ln.length_sec}s {ln.program:<26} {days:<5} {label:<12}"
                f" {rate:>8}  {ln.total_spots} spots  {ln.week_spots}{union}{blocks}"
            )
    if order.charges:
        print("\n  Charges (→ Production box on flight 1's first paid line, not airtime):")
        for ch in order.charges:
            lst = f"  (list ${ch.list_amount:,.2f})" if ch.list_amount else ""
            net = f"  → ${ch.billed:,.2f} net" if abs(ch.billed - ch.amount) > 0.005 else ""
            print(f"    {ch.description:<20} ${ch.amount:>10,.2f} in the box{net}{lst}")
    print(
        f"\n  Order: {sum(ln.total_spots for fl in order.flights for ln in fl.paid_lines)} paid"
        f" + {sum(ln.total_spots for fl in order.flights for ln in fl.bonus_lines)} bonus"
        f" spots   ${order.entered_airtime:,.2f} entered / ${order.net_airtime:,.2f} net airtime"
    )

    # ── Customer: project words → client linked to the agency ──
    resolved = _resolve_customer(order.project, agency_id)
    if resolved:
        print(
            f"\n[CUSTOMER] '{order.project}' + agency {agency_id} → "
            f"ID {resolved['id']}  ({resolved['name']})"
        )
        default_id = str(resolved["id"])
    else:
        print(f"\n[CUSTOMER] Could not auto-resolve '{order.project}' via agency {agency_id}.")
        default_id = ""
    raw_id = input(f"  Customer (ANAGRAF) ID [{default_id}]: ").strip()
    customer_id = raw_id or default_id
    if not customer_id.isdigit():
        print("  ✗ Invalid customer ID — aborting")
        return None
    customer_id = int(customer_id)
    cust_name = resolved["name"] if resolved else order.project

    # ── The customer's own prior Vida contracts: naming, separation, commission ──
    precedent = _precedent(customer_id)
    if precedent:
        cust_name = precedent.get("customer_name") or cust_name
        print(f"\n  Prior Vida contracts for {cust_name}:")
        for code, desc, ref in precedent["recent"]:
            print(f"    {code:<20} {desc}" + (f"   ref '{ref}'" if ref else ""))

    # ── Commission: the sheet's own rate, cross-checked against ANAGRAF ──
    anagraf_pct = _agency_commission(agency_id)
    prec_pct = precedent.get("agency_pct") if precedent else None
    print(
        f"\n  Commission: sheet {agency_pct:g}%"
        + (f"   ANAGRAF agency {anagraf_pct:g}%" if anagraf_pct is not None else "")
        + (f"   last contract {prec_pct:g}%" if prec_pct is not None else "")
    )
    if prec_pct is not None and abs(prec_pct - agency_pct) > 0.001:
        print(
            f"  ⚠ The sheet nets at {agency_pct:g}% but this customer's last Vida contract "
            f"carried {prec_pct:g}% — the header takes what you confirm here."
        )
        raw = input(f"  Agency commission % [{agency_pct:g}]: ").strip().rstrip("%")
        if raw:
            try:
                agency_pct = float(raw)
            except ValueError:
                print("  ✗ Not a number — aborting")
                return None

    # ── Separation / billing: customer DB, else the precedent's lines, else PSE ──
    separation = VIDA_DEFAULT_SEPARATION
    billing_type = "agency"
    cust = _lookup_customer_db(cust_name)
    if cust:
        billing_type = cust.billing_type or "agency"
        separation = (cust.separation_customer, cust.separation_order, cust.separation_event)
    elif precedent and precedent.get("separation"):
        separation = precedent["separation"]
    print(
        f"  Separation default: {separation[0]}/{separation[1]}/{separation[2]} (customer/order/event)"
    )

    # ── Per flight: start date, line plan, code / description ──
    flights_in: list[dict] = []
    today = date.today()
    for i, fl in enumerate(order.flights, 1):
        print(f"\n  ── Flight {i}: {fl.label} ──")
        # Flight 1 is always asked (these orders enter late); later flights only when late.
        start = confirm_start_date(
            fmt_mmddyyyy(fl.start), fmt_mmddyyyy(fl.end), always_ask=(i == 1)
        )
        if start is None:
            print("  ✗ No flight start date — aborting")
            return None
        plan = flight_plan(fl, start)
        print(f"\n    Etere lines for a {fmt_mmddyyyy(start)} start:")
        entered, notes = _print_plan(plan)
        ordered = sum(ln.total_spots for ln in fl.lines)
        if notes:
            print("\n    ⚠ The start date makes some spots undeliverable:")
            for n in notes:
                print(f"        {n}")
        print(
            f"    Spots: {entered} entered of {ordered} ordered"
            f"{'' if entered == ordered else '  ← SHORT'}"
        )
        if entered != ordered:
            raw = input("    Enter this flight short anyway? [y/N]: ").strip().lower()
            if raw not in ("y", "yes"):
                print("  ✗ Aborted — pick an earlier start date or ask the agency to revise.")
                return None

        default_code, default_desc, short, desc_prefix = default_code_desc(
            order, fl, start, cust, precedent
        )
        raw = input(f"    Contract code [{default_code}]: ").strip()
        code = raw or default_code
        raw = input(f"    Description [{default_desc}]: ").strip()
        desc = raw or default_desc
        ref = ""
        if fl.phase:
            raw = input(f"    Customer ref [{fl.phase}] ('-' = none): ").strip()
            ref = "" if raw == "-" else (raw or fl.phase)

        # A flight that opens weeks out usually has no creative yet — leave a note
        # on its lines so traffic is checked before it airs (AE 2026-10-12: "not
        # sure which traffic to use for December").
        note = ""
        if i > 1 and start > today + timedelta(days=21):
            default_note = (
                f"Creative/traffic for this flight not confirmed at entry "
                f"({today.month}/{today.day}/{today:%y}) - check before "
                f"{start.month}/{start.day}/{start:%y}"
            )
            raw = input(f"    Line note [{default_note}] ('-' = none): ").strip()
            note = "" if raw == "-" else (raw or default_note)

        flights_in.append(
            {
                "index": i - 1,
                "contract_code": code,
                "description": desc,
                "start_date_override": fmt_mmddyyyy(start),
                "note": note[:399],
                "customer_ref": ref,
                "header_note": "; ".join(order.notes)[:399] if i == 1 else "",
            }
        )

    _upsert_customer_db(
        cust_name, customer_id, code_name=short, description_name=desc_prefix, separation=separation
    )

    return {
        "customer_id": customer_id,
        "billing_type": billing_type,
        "separation": separation,
        "market": market_code,
        "agency_pct": agency_pct,
        "flights": flights_in,
        # Batch-summary label: the first contract's code (lesson: the gathered code,
        # never the DB id). Every flight's code is returned by run_vida_order.
        "contract_code": flights_in[0]["contract_code"],
        "description": flights_in[0]["description"],
        "start_date_override": flights_in[0]["start_date_override"],
    }


# ─── Direct DB entry ──────────────────────────────────────────────────────────


def _create_vida_contract(
    order: VidaOrder,
    flight: VidaFlight,
    fin: dict,
    inputs: dict,
    production: float,
    dry_run: bool = False,
) -> Optional[str]:
    from browser_automation.etere_direct_client import (
        AGENCY_IDS,
        EtereDirectClient,
        connect,
    )

    customer_id = inputs.get("customer_id")
    market_code = inputs.get("market") or order.market_code
    if customer_id is None or not market_code:
        print("[VIDA] ✗ No customer_id / market")
        return None
    separation = tuple(inputs.get("separation") or VIDA_DEFAULT_SEPARATION)
    billing_type = inputs.get("billing_type", "agency")
    agency_pct = float(inputs.get("agency_pct", order.agency_pct))
    code = fin["contract_code"]
    description = fin["description"]
    note = fin.get("note", "")
    start = (
        parse_date(fin["start_date_override"]) if fin.get("start_date_override") else flight.start
    )
    end = flight.end

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=code,
            description=description,
            customer_id=int(customer_id),
            agency_id=AGENCY_IDS["VIDA"],
            agency_pct=agency_pct,  # explicit 0% beats the ANAGRAF 15% (precedent)
            lookup_customer_defaults=True,  # media center / payment terms from ANAGRAF
            contract_date=start,
            contract_end_date=end,
            contract_type=1,
            billing_type=billing_type,
            customer_order_ref=fin.get("customer_ref", ""),
            note=fin.get("header_note", ""),
            allow_rename=True,
        )
        print(
            f"[VIDA] ✓ Contract header: ID={contract_id}  code='{code}'  "
            f"{fmt_mmddyyyy(start)}–{fmt_mmddyyyy(end)}  commission {agency_pct:g}%"
            + (f"  ref '{fin['customer_ref']}'" if fin.get("customer_ref") else "")
        )

        production_pending = round(production, 2)
        line_count = 0
        for ln, days, time_range, desc, ranges, notes in flight_plan(flight, start):
            booking_code = 10 if ln.is_bonus else 2
            for n in notes:
                print(f"  [NOTE] {desc}: {n}")
            for rng in ranges:
                total_spots = rng["spots_per_week"] * rng["weeks"]
                line_count += 1
                tag = f"  ← {rng['tag']}" if rng["tag"] else ""
                print(
                    f"  [LINE {line_count}] {market_code} {desc}: "
                    f"{fmt_mmddyyyy(rng['date_from'])}–{fmt_mmddyyyy(rng['date_to'])} "
                    f"({rng['spots_per_week']}/wk×{rng['weeks']}w={total_spots}) "
                    f":{ln.length_sec}s rate={ln.rate} max {rng['max_daily']}/day{tag}"
                )
                prod = production_pending if not ln.is_bonus else 0.0
                production_pending = round(production_pending - prod, 2)
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
                    is_bonus=ln.is_bonus,
                    booking_code=booking_code,
                    separation_intervals=separation,
                    production_cost=prod,
                    note=note,
                    scheduling_type=VIDA_SCHEDULING_TYPE,
                )
                if prod:
                    verify_production_charge(conn.cursor(), line_id, prod, label="Vida")
                    print(f"           ↳ Production ${prod:,.2f} (→ CONTRATTISPESE 'Production')")
                kept, _dropped = _prune_blocks_to_language(
                    conn.cursor(), line_id, ln.language, desc
                )
                if kept == 0:
                    raise RuntimeError(f"Vida: line {line_id} '{desc}' has no blocks; rolling back")

        if production_pending:
            raise RuntimeError(
                f"Vida: ${production_pending:,.2f} production was never "
                "attached — no paid line on this flight. Rolling back."
            )

        # Read back the header money basis before committing.
        cur = conn.cursor()
        cur.execute(
            "SELECT P_AGENZIA, AGENZIA, CENTROMEDIA FROM CONTRATTITESTATA "
            "WHERE ID_CONTRATTITESTATA = %s",
            (int(contract_id),),
        )
        row = cur.fetchone()
        if (
            row is None
            or abs(float(row[0] or 0) - agency_pct) > 0.001
            or int(row[1] or 0) != AGENCY_IDS["VIDA"]
        ):
            raise RuntimeError(
                f"Vida: header readback P_AGENZIA={row and row[0]} "
                f"AGENZIA={row and row[1]} — expected {agency_pct} / "
                f"{AGENCY_IDS['VIDA']}; rolling back"
            )
        if dry_run:
            _print_dry_run_readback(cur, int(contract_id))
            conn.rollback()
            conn.close()
            print(f"[VIDA] ✓ DRY RUN: {line_count} lines on {code} verified and rolled back.")
            return code
        conn.commit()
        conn.close()
        print(f"[VIDA] ✓ {line_count} lines committed on {code} (CENTROMEDIA {row[2]}).")
        return code

    except Exception as exc:
        print(f"[VIDA] ✗ {exc}")
        import traceback

        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


def _print_dry_run_readback(cur, contract_id: int) -> None:
    """In-transaction readback of what a dry run wrote (header, lines, charges, blocks)."""
    cur.execute(
        "SELECT COD_CONTRATTO, DESCRIZIONE, COMMITTENTE, AGENZIA, P_AGENZIA, CENTROMEDIA, "
        "CUSTOMERREF, DATA_INIZIO, DATA_TERMINE FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = %s",
        (contract_id,),
    )
    h = cur.fetchone()
    print(
        f"  [READBACK] {h[0]!r} {h[1]!r} cust {h[2]} agency {h[3]} @ {h[4]}% media {h[5]} "
        f"ref {h[6]!r} {h[7]:%m/%d/%y}-{h[8]:%m/%d/%y}"
    )
    cur.execute(
        "SELECT r.ID_CONTRATTIRIGHE, r.DESCRIZIONE, r.DATESTART, r.DATEEND, r.N_PASSAGGI, r.IMPORTO, "
        "r.ID_BOOKINGCODE, r.PRENOTAZIONE, r.Interv_Committente, r.INTERVALLO, r.INTERV_CONTRATTO, "
        "r.PASSAGGI_GIORNALIERI, (SELECT COUNT(*) FROM CONTRATTIFASCE f WHERE f.ID_CONTRATTIRIGHE = "
        "r.ID_CONTRATTIRIGHE), (SELECT ISNULL(SUM(IMPORTO), 0) FROM CONTRATTISPESE s WHERE "
        "s.ID_CONTRATTIRIGHE = r.ID_CONTRATTIRIGHE) FROM CONTRATTIRIGHE r "
        "WHERE r.ID_CONTRATTITESTATA = %s ORDER BY r.ID_CONTRATTIRIGHE",
        (contract_id,),
    )
    spots = money = 0.0
    for r in cur.fetchall():
        spots += int(r[4] or 0)
        money += float(r[4] or 0) * float(r[5] or 0)
        print(
            f"  [READBACK] line {r[0]} {r[1]!r} {r[2]:%m/%d}-{r[3]:%m/%d} {r[4]}x ${float(r[5]):.2f} "
            f"bk {r[6]} pren {r[7]} sep {int(round(float(r[8] or 0) / _FPM))}/"
            f"{int(round(float(r[9] or 0) / _FPM))}/{int(round(float(r[10] or 0) / _FPM))} "
            f"max/day {r[11]} blocks {r[12]}"
            + (f" production ${float(r[13]):,.2f}" if r[13] else "")
        )
    print(f"  [READBACK] {int(spots)} spots, ${money:,.2f} at the entered rates")


def run_vida_order(order: VidaOrder, inputs: dict, dry_run: bool = False) -> list[tuple[str, bool]]:
    """One contract per flight. Returns [(code, success)] — the codes actually
    created (autocommit per contract, so a partial batch is reported as such).
    dry_run writes everything inside the transaction, reads it back, rolls back."""
    results: list[tuple[str, bool]] = []
    production_total = round(sum(ch.amount for ch in order.charges), 2)
    flights_in = inputs.get("flights") or []
    if not flights_in:
        print("[VIDA] ✗ gather returned no flights")
        return []
    for fin in flights_in:
        flight = order.flights[fin["index"]]
        production = production_total if fin["index"] == 0 else 0.0
        code = _create_vida_contract(order, flight, fin, inputs, production, dry_run=dry_run)
        results.append((fin["contract_code"], code is not None))
        if code is None:
            print(
                f"[VIDA] ✗ stopping after flight {fin['index'] + 1} failed; "
                f"{len(results) - 1} contract(s) already committed"
            )
            break
    return results


def rehearsal_inputs(order: VidaOrder, start: Optional[date] = None) -> dict:
    """The gather's answers with every default taken (no prompts), for a dry run."""
    from browser_automation.etere_direct_client import AGENCY_IDS

    resolved = _resolve_customer(order.project, AGENCY_IDS["VIDA"])
    if not resolved:
        raise RuntimeError(f"Vida: cannot resolve a customer for {order.project!r}")
    precedent = _precedent(resolved["id"])
    cust = _lookup_customer_db((precedent or {}).get("customer_name") or resolved["name"])
    separation = VIDA_DEFAULT_SEPARATION
    if cust:
        separation = (cust.separation_customer, cust.separation_order, cust.separation_event)
    elif precedent and precedent.get("separation"):
        separation = precedent["separation"]
    flights_in = []
    for i, fl in enumerate(order.flights):
        st = start if (i == 0 and start) else fl.start
        code, desc, _s, _p = default_code_desc(order, fl, st, cust, precedent)
        flights_in.append(
            {
                "index": i,
                "contract_code": code,
                "description": desc,
                "start_date_override": fmt_mmddyyyy(st),
                "note": "",
                "customer_ref": fl.phase,
                "header_note": "; ".join(order.notes)[:399] if i == 0 else "",
            }
        )
    return {
        "customer_id": resolved["id"],
        "billing_type": (cust.billing_type if cust and cust.billing_type else "agency"),
        "separation": separation,
        "market": order.market_code,
        "agency_pct": float(order.agency_pct),
        "flights": flights_in,
        "contract_code": flights_in[0]["contract_code"],
        "description": flights_in[0]["description"],
        "start_date_override": flights_in[0]["start_date_override"],
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Vida proposal → Etere (dry run by default)")
    ap.add_argument("path")
    ap.add_argument("--start", help="flight 1 start m/d/yyyy")
    ap.add_argument("--apply", action="store_true", help="commit (default: rehearse and roll back)")
    a = ap.parse_args()
    _order = parse_vida(a.path)
    _inputs = rehearsal_inputs(_order, parse_date(a.start) if a.start else None)
    for _fin in _inputs["flights"]:
        _fl = _order.flights[_fin["index"]]
        print(f"\n== {_fin['contract_code']}  {_fin['description']}  ref {_fin['customer_ref']!r}")
        _print_plan(flight_plan(_fl, parse_date(_fin["start_date_override"])))
    print(
        f"\ncustomer {_inputs['customer_id']}  sep {_inputs['separation']}  "
        f"commission {_inputs['agency_pct']:g}%  billing {_inputs['billing_type']}"
    )
    print(run_vida_order(_order, _inputs, dry_run=not a.apply))
