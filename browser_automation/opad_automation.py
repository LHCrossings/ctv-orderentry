"""
opAD Automation - Browser Automation for opAD Agency Orders

Uses EtereClient for ALL Etere interactions.
Parser handles PDF extraction; this file handles business logic + orchestration.

opAD Business Rules:
- NYC market ONLY (always)
- Multiple clients possible under opAD → customer DB lookup with manual fallback
- Separation intervals: Customer=15, Order=15, Event=0
- Billing: Charge To = "Customer share indicating agency %", Invoice Header = "Agency"
- Description from IO → contract header Notes field
- Estimate number → Customer Order Ref
- Bonus lines (rate=0) → spot code BNS (10)
- Weekly distribution splitting on gaps and differing spot counts
- Sunday 6-7a paid programming rule applies
"""

from typing import Optional

from etere_client import EtereClient
from parsers.opad_parser import (
    OpADLine,
    OpADOrder,
    analyze_weekly_distribution,
    format_time_for_description,
    parse_opad_pdf,
)

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

AGENCY_NAME = "opAD"
DEFAULT_MARKET = "NYC"

# Separation intervals: (customer, order, event) in minutes
SEPARATION_INTERVALS = (15, 15, 0)

# Billing
CHARGE_TO = "Customer share indicating agency %"
INVOICE_HEADER = "Agency"

# Spot codes
SPOT_CODE_PAID = 2       # Paid Commercial
SPOT_CODE_BONUS = 10     # BNS / Bonus Spot

# Customer database path (relative to project root)
from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMERS_DB_PATH

# ─────────────────────────────────────────────────────────────────────────────
# DIRECT DB HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date(s):
    from datetime import date, datetime
    if isinstance(s, date):
        return s
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


def _secs_to_duration(secs: int) -> str:
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:00"


def _create_opad_contract_direct(pdf_path: str, user_input) -> bool:
    """Enter opAD order directly via DB stored procedures (no browser)."""
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    if isinstance(user_input, dict):
        customer_id = user_input.get('customer_id')
        order_code  = user_input.get('order_code', '')
        description = user_input.get('description', '')
        separation  = user_input.get('separation', SEPARATION_INTERVALS)
    else:
        customer_id, _ = _resolve_customer_id(pdf_path)
        order_code  = user_input.order_code
        description = user_input.description
        separation  = user_input.separation_intervals or SEPARATION_INTERVALS

    if customer_id is None:
        print("[OPAD DIRECT] ✗ No customer_id — cannot enter without a known ID")
        return False

    conn = None
    try:
        order = parse_opad_pdf(pdf_path)
        conn  = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=order_code,
            description=description,
            customer_id=int(customer_id),
            contract_date=_parse_date(order.flight_start),
            contract_end_date=_parse_date(order.flight_end),
            contract_type=1,
            billing_type="agency",
            note=order.description,  # the IO's Description line (Lee 10/15), never the typed contract description
            customer_order_ref=order.estimate_number,
            allow_rename=True,
        )
        if not contract_id:
            print("[OPAD DIRECT] ✗ Failed to create contract header")
            return False
        print(f"[OPAD DIRECT] ✓ Contract ID={contract_id}")

        if customer_id:
            _save_customer_to_db(order.client, customer_id)

        line_count = 0
        for line in order.lines:
            etere_lines = _build_etere_lines(line, order)
            for el in etere_lines:
                line_count += 1
                print(f"  [LINE {line_count}] {el['description']}: "
                      f"{el['start_date']}–{el['end_date']} "
                      f"({el['spots_per_week']}/wk={el['total_spots']})")
                client.add_contract_line(
                    market=DEFAULT_MARKET,
                    days=el['days'],
                    time_range=f"{el['time_from']}-{el['time_to']}",
                    description=el['description'],
                    rate=float(el['rate']),
                    total_spots=el['total_spots'],
                    spots_per_week=el['spots_per_week'],
                    date_from=_parse_date(el['start_date']),
                    date_to=_parse_date(el['end_date']),
                    duration=_secs_to_duration(line.duration),
                    is_bonus=(el['spot_code'] == SPOT_CODE_BONUS),
                    booking_code=el['spot_code'],
                    separation_intervals=separation,
                )

        conn.commit()
        conn.close()
        print(f"[OPAD DIRECT] ✓ {line_count} line(s) entered")
        return True

    except Exception as exc:
        print(f"[OPAD DIRECT] ✗ {exc}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return False
# ═══════════════════════════════════════════════════════════════════════════════
# PRE-GATHER (called before processing begins)
# ═══════════════════════════════════════════════════════════════════════════════

def gather_opad_inputs(pdf_path: str) -> Optional[dict]:
    """Collect all opAD inputs upfront before processing begins.

    Defaults come from the customer's OWN prior opAD contracts in Etere (Lee,
    NYSDOH 3180, 2026-10-15): code `opAD NYDOH 3180` keeps the short name of the
    last code, description `NYS Dept of Health Est 3180` keeps the prefix most of
    the prior descriptions share. The PDF's own Description line is printed above
    the prompt so a campaign tag ("OE 4Q26") is one edit away, never guessed.
    """
    from browser_automation.customer_defaults import prompt_customer_id

    try:
        order = parse_opad_pdf(pdf_path)
    except Exception as e:
        print(f"[OPAD] ✗ Failed to parse PDF: {e}")
        return None

    est = order.estimate_number
    print(f"\n  Client:      {order.client}")
    print(f"  Estimate:    {est}   Flight {order.flight_start}–{order.flight_end}   {order.market}")
    print(f"  Description: {order.description or '(none)'}   Product: {order.product or '(none)'}")
    print(
        f"  Lines:       {len(order.lines)}  "
        f"{sum(ln.total_spots for ln in order.lines)} spots  "
        f"${sum(ln.total_cost for ln in order.lines):,.2f} gross"
    )

    # Customer: customers.db, else the customer the prior opAD contracts name
    cust = _lookup_customer_db(order.client)
    known_id = int(cust.customer_id) if cust and str(cust.customer_id).isdigit() else None
    prec = _precedent(customer_id=known_id, client=order.client)
    default_id = str(known_id or (prec["customer_id"] if prec else "") or "")
    if cust:
        print(f"[CUSTOMER DB] ✓ {order.client} → ID {cust.customer_id}")
    elif prec:
        print(
            f"[CUSTOMER] Not in customers.db; prior opAD contracts name "
            f"'{prec['customer_name']}' → ID {prec['customer_id']}"
        )
    else:
        print(f"[CUSTOMER] Not found in database or prior opAD contracts: {order.client}")
    customer_id = prompt_customer_id(str(default_id))
    if customer_id is None:
        print("  ✗ No customer ID — aborting")
        return None
    customer_id = int(customer_id)
    if prec and prec["customer_id"] != customer_id:
        prec = _precedent(customer_id=customer_id) or prec

    # Etere's own contracts outrank the stored defaults (they ARE what was typed on
    # real entries); the stored values only carry a customer with no history yet.
    short = (prec["short"] if prec else "") or (cust.code_name if cust and cust.code_name else "")
    short = short or (order.client.split()[0] if order.client else "CLIENT")
    desc_prefix = (prec["desc_prefix"] if prec else "") or (
        cust.description_name if cust and cust.description_name else ""
    )
    desc_prefix = desc_prefix or order.client
    default_code = f"opAD {short} {est}"
    default_desc = f"{desc_prefix} Est {est}"
    if prec:
        print("  Prior opAD contracts for this customer:")
        for code, desc in prec["recent"]:
            print(f"    {code:<24} {desc}")
    print(f"  PDF description: {order.description or '(none)'}")

    order_code = input(f"  Contract code [{default_code}]: ").strip() or default_code
    description = input(f"  Description [{default_desc}]: ").strip() or default_desc

    separation = SEPARATION_INTERVALS
    if cust:
        separation = (cust.separation_customer, cust.separation_order, cust.separation_event)
    # Remember what was TYPED (the operator's convention), not the guess.
    import re

    m = re.match(r"(?i)opAD\s+(.+?)\s+\S+$", order_code)
    typed_short = m.group(1) if m else short
    typed_prefix = re.sub(r"\s*\bEst\b.*$", "", description).strip() or desc_prefix
    _save_customer_to_db(order.client, customer_id, code_name=typed_short,
                         description_name=typed_prefix, separation=separation)

    return {
        'customer_id': customer_id,
        'order_code':  order_code,
        'description': description,
        'separation':  separation,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def process_opad_order(
    pdf_path: str,
    shared_session=None,
    pre_gathered_inputs=None,
) -> bool:
    """
    Process an opAD order end-to-end via direct DB entry.

    Args:
        pdf_path: Path to the opAD PDF
        shared_session: Unused (direct DB — no browser session needed)
        pre_gathered_inputs: Dict with keys gathered upfront:
            - order_code: Contract code (e.g., "opAD NYSDOH 2824")
            - description: Contract description (goes to Notes)
            - customer_id: Customer ID (int or None for manual search)

    Returns:
        True if successful, False otherwise
    """
    return _create_opad_contract_direct(pdf_path, pre_gathered_inputs)


# ═══════════════════════════════════════════════════════════════════════════════
# LINE BUILDING
# ═══════════════════════════════════════════════════════════════════════════════

def _build_etere_lines(line: OpADLine, order: OpADOrder) -> list[dict]:
    """
    Convert a single parsed opAD line into one or more Etere line dicts.

    Splits on:
    - Gaps (weeks with 0 spots)
    - Differing weekly spot counts

    Returns:
        List of dicts ready for etere.add_contract_line()
    """
    # Analyze weekly distribution (splits on gaps + differing counts)
    ranges = analyze_weekly_distribution(
        line.weekly_spots,
        order.week_start_dates,
        contract_end_date=order.flight_end,
    )

    # Build description
    time_fmt = format_time_for_description(line.time)
    lang_suffix = f" {line.language}" if line.language else ""
    description = f"{line.days} {time_fmt}{lang_suffix}"
    if line.is_bonus():
        description = f"BNS {description}"  # house convention (2351 'BNS M-Su 7p-12a Chinese ROS'; Lee 10/15)

    # Parse time range using EtereClient universal parser
    time_from, time_to = EtereClient.parse_time_range(line.time)

    # Apply Sunday 6-7a rule
    adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(line.days, line.time)

    # Spot code: bonus if rate is 0
    spot_code = SPOT_CODE_BONUS if line.is_bonus() else SPOT_CODE_PAID

    etere_lines = []
    for range_data in ranges:
        etere_lines.append({
            "start_date": range_data["start_date"],
            "end_date": range_data["end_date"],
            "days": adjusted_days,
            "time_from": time_from,
            "time_to": time_to,
            "description": description,
            "spot_code": spot_code,
            "total_spots": range_data["spots"],
            "spots_per_week": range_data["spots_per_week"],
            "rate": line.rate,
        })

    return etere_lines


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOMER RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════════

_STOP = {"of", "the", "and", "state", "department", "dept", "inc", "llc", "co"}


def _tokens(name: str) -> set[str]:
    import re

    return {t for t in re.split(r"[^a-z0-9]+", (name or "").lower()) if t} - _STOP


def _lookup_customer_db(client_name: str):
    """customers.db row for this client (opAD first, then any order type)."""
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.enums import OrderType

        if not CUSTOMERS_DB_PATH.exists() or not client_name:
            return None
        repo = CustomerRepository(CUSTOMERS_DB_PATH)
        return repo.find_by_name(client_name, OrderType.OPAD) or repo.find_by_name_any_type(client_name)
    except Exception as exc:
        print(f"[CUSTOMER DB] ⚠ Lookup failed: {exc}")
        return None


def _precedent(customer_id: Optional[int] = None, client: str = "") -> Optional[dict]:
    """The customer's prior opAD contracts in Etere → naming defaults.

    Picks the customer by id, else the opAD customer whose ANAGRAF name shares
    the most words with the PDF's client ('NYS Department of Health' → 'NY State
    Department of Health', 44). Returns the short name of the latest code
    ('opAD NYDOH 2824' → 'NYDOH'), the description prefix most of the prior
    descriptions share once their 'Est N…' tail is removed ('NYS Dept of
    Health'), and the three latest (code, description) pairs for display.
    """
    import re
    from collections import Counter

    try:
        from browser_automation.etere_direct_client import connect

        conn = connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT t.COMMITTENTE, a.RAG_SOCIAL, t.COD_CONTRATTO, t.DESCRIZIONE "
            "FROM CONTRATTITESTATA t JOIN ANAGRAF a ON a.ID_ANAGRAF = t.COMMITTENTE "
            "WHERE t.COD_CONTRATTO LIKE 'opAD %' ORDER BY t.DATA_INIZIO DESC, t.ID_CONTRATTITESTATA DESC"
        )
        rows = [(int(r[0]), str(r[1]).strip(), str(r[2]).strip(), str(r[3] or "").strip()) for r in cur.fetchall()]
        conn.close()
    except Exception as exc:
        print(f"[CUSTOMER] Etere precedent lookup failed: {exc}")
        return None
    if not rows:
        return None
    if customer_id is None:
        want = _tokens(client)
        scores = Counter()
        for cid, name, _c, _d in rows:
            scores[cid] = max(scores.get(cid, 0), len(want & _tokens(name)))
        best, score = scores.most_common(1)[0]
        if score == 0:
            return None
        customer_id = best
    mine = [r for r in rows if r[0] == customer_id]
    if not mine:
        return None
    latest_code = mine[0][2]
    m = re.match(r"opAD\s+(.+?)\s+\S+$", latest_code)
    short = m.group(1) if m else ""
    prefixes = [re.sub(r"\s*\bEst\b.*$", "", d).strip() for _c, _n, _code, d in [(0, 0, 0, r[3]) for r in mine]]
    prefixes = [p for p in prefixes if p]
    desc_prefix = Counter(prefixes).most_common(1)[0][0] if prefixes else ""
    return {
        "customer_id": customer_id,
        "customer_name": mine[0][1],
        "short": short,
        "desc_prefix": desc_prefix,
        "recent": [(r[2], r[3]) for r in mine[:3]],
    }


def _resolve_customer_id(pdf_path: str) -> tuple[Optional[int], None]:
    """Customer ID for a run that skipped the gather (customers.db, else the
    opAD precedent, else a manual prompt). Returns (id, None) for back-compat."""
    try:
        client_name = parse_opad_pdf(pdf_path).client
    except Exception:
        client_name = ""
    cust = _lookup_customer_db(client_name)
    if cust and str(cust.customer_id).isdigit():
        return int(cust.customer_id), None
    prec = _precedent(client=client_name)
    if prec:
        return prec["customer_id"], None
    print(f"[CUSTOMER] Not found in database: {client_name}")
    return _manual_customer_entry(), None


def _manual_customer_entry() -> Optional[int]:
    """Prompt user for customer ID manually."""
    customer_input = input("Enter customer ID (or press Enter to search in Etere): ").strip()
    if customer_input:
        try:
            return int(customer_input)
        except ValueError:
            print("[CUSTOMER] Invalid ID - will search in Etere")
            return None
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOMER DATABASE (Self-Learning)
# ═══════════════════════════════════════════════════════════════════════════════

def _save_customer_to_db(
    client_name: str,
    customer_id: int,
    code_name: str = "",
    description_name: str = "",
    separation: tuple = SEPARATION_INTERVALS,
) -> None:
    """Upsert the customer (id + naming defaults) in customers.db. Non-fatal."""
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        from src.domain.enums import OrderType

        if not CUSTOMERS_DB_PATH.exists():
            print(f"[CUSTOMER DB] ⚠ Database not found at {CUSTOMERS_DB_PATH}")
            return
        CustomerRepository(CUSTOMERS_DB_PATH).save(
            Customer(
                customer_id=str(customer_id),
                customer_name=client_name,
                order_type=OrderType.OPAD,
                billing_type="agency",
                code_name=code_name,
                description_name=description_name,
                separation_customer=separation[0],
                separation_order=separation[1],
                separation_event=separation[2],
                default_market=DEFAULT_MARKET,
            )
        )
        print(f"[CUSTOMER DB] ✓ Saved {client_name} (ID {customer_id}, code '{code_name}')")
    except Exception as e:
        print(f"[CUSTOMER DB] ⚠ Could not save customer (non-fatal): {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# DEFAULT VALUES (called by order_processing_service.get_default_order_values)
# ═══════════════════════════════════════════════════════════════════════════════

def get_opad_defaults(pdf_path: str) -> tuple[str, str]:
    """
    Get smart default contract code and description for an opAD order.

    Code format: "opAD {client_first_word} {estimate_number}"
    Description: PDF's Description field (or Product as fallback)

    Args:
        pdf_path: Path to the opAD PDF

    Returns:
        Tuple of (default_code, default_description)
    """
    try:
        order = parse_opad_pdf(pdf_path)
        client_first = order.client.split()[0] if order.client else "CLIENT"
        code = f"opAD {client_first} {order.estimate_number}"
        description = order.description if order.description else order.product
        return (code, description)
    except Exception as e:
        print(f"[opAD] ⚠ Could not parse defaults: {e}")
        return ("opAD Order", "opAD Order")
