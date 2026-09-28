"""
Polaris Media Group Automation.

Gathers user inputs and enters Polaris insertion orders into Etere for
Crossings TV (SFO, CVC, SEA, LAX, or other markets).

Master market: NYC (standard default — Polaris is not WorldLink/DAL).
Billing: agency (charge_to="Customer share indicating agency %", invoice_header="Agency").
"""

import re
from datetime import datetime
from typing import Optional

from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
from browser_automation.customer_defaults import prompt_customer_id
from browser_automation.etere_client import EtereClient
from browser_automation.parsers.polaris_parser import PolarisOrder
from browser_automation.parsers.polaris_parser import parse_polaris_file as parse_polaris_xlsx

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


def _create_polaris_contracts_direct(order: 'PolarisOrder', user_input: dict) -> Optional[str]:
    """Enter Polaris order directly via DB stored procedures (no browser). One contract per market."""
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = user_input.get('customer_id')
    if customer_id is None:
        print("[POLARIS DIRECT] ✗ No customer_id — cannot enter without a known ID")
        return None

    contracts    = user_input['contracts']
    separation   = user_input.get('separation', (15, 0, 0))
    flight_start = user_input.get('actual_start', order.flight_start)
    flight_end   = user_input.get('actual_end',   order.flight_end)

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")
        last_contract_id = None

        for market in order.markets:
            market_lines  = order.lines_for_market(market)
            contract_info = contracts[market]
            code          = contract_info['code']
            description   = contract_info['description']

            print(f"\n[POLARIS DIRECT] Creating contract for {market}: {code}")
            contract_id = client.create_contract_header(
                code=code,
                description=description,
                customer_id=int(customer_id),
                contract_date=_parse_date(flight_start),
                contract_end_date=_parse_date(flight_end),
                contract_type=1,
                billing_type="agency",
                allow_rename=True,
                # The description keeps the short house name; the sheet's
                # full committee name (130+ chars) goes in the header note
                # (Lee 9/28, contract 3132).
                note=order.advertiser.strip(),
            )
            if not contract_id:
                print(f"[POLARIS DIRECT] ✗ Failed to create contract for {market}")
                return None
            print(f"[POLARIS DIRECT] ✓ Contract ID={contract_id}")

            line_count = 0
            for ln in market_lines:
                time_from, time_to = ln.get_time_from_to()
                is_bonus     = ln.is_bonus
                booking_code = 10 if is_bonus else 2
                adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(ln.days, ln.time_str)

                line_count += 1
                print(f"  [LINE {line_count}] {ln.get_description()}  ×{ln.total_spots}")
                client.add_contract_line(
                    market=market,
                    days=adjusted_days,
                    time_range=f"{time_from}-{time_to}",
                    description=ln.get_description(),
                    rate=float(ln.rate),
                    total_spots=ln.total_spots,
                    spots_per_week=ln.total_spots,
                    date_from=_parse_date(flight_start),
                    date_to=_parse_date(flight_end),
                    duration=_secs_to_duration(ln.duration),
                    is_bonus=is_bonus,
                    booking_code=booking_code,
                    separation_intervals=separation,
                )

            print(f"[POLARIS DIRECT] ✓ {market}: {line_count} line(s) entered")
            last_contract_id = contract_id

        conn.commit()
        conn.close()
        return str(last_contract_id) if last_contract_id else None

    except Exception as exc:
        print(f"[POLARIS DIRECT] ✗ {exc}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


_MARKET_SHORT = {
    "CVC": "CV",
    "SFO": "SF",
    "SEA": "SEA",
    "LAX": "LA",
    "HOU": "HOU",
    "CMP": "CMP",
    "WDC": "WDC",
    "NYC": "NYC",
}


# ─────────────────────────────────────────────────────────────────────────────
# Customer DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_customer(name: str, db_path: str = CUSTOMER_DB_PATH) -> Optional[dict]:
    """Look the advertiser up in the shared dbo.CTV_Customers table (the sqlite
    customers.db this used to read is retired): exact Polaris record, then any
    order type, then fuzzy."""
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.enums import OrderType

        repo = CustomerRepository(db_path)
        cust = (
            repo.find_by_name(name, OrderType.POLARIS)
            or repo.find_by_name_any_type(name)
            or repo.find_by_name_fuzzy(name, OrderType.POLARIS)
        )
    except Exception as exc:
        print(f"[CUSTOMER DB] ⚠ Lookup error: {exc}")
        return None
    if cust is None:
        return None
    return {
        "customer_id": cust.customer_id,
        "customer_name": cust.customer_name,
        "code_name": cust.code_name,
        "description_name": cust.description_name,
        "include_market_in_code": int(cust.include_market_in_code),
        "separation_customer": cust.separation_customer,
        "separation_order": cust.separation_order,
        "separation_event": cust.separation_event,
    }


def _upsert_customer(
    customer_id: str,
    customer_name: str,
    code_name: str,
    description_name: str,
    include_market: bool,
    separation: tuple[int, int, int] = (15, 0, 0),
    db_path: str = CUSTOMER_DB_PATH,
) -> None:
    """Save the Polaris customer record to dbo.CTV_Customers.  `separation`
    is (customer, order, event) — the stored values on an update, so learning
    a prefix never resets a tuned separation."""
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        from src.domain.enums import OrderType

        CustomerRepository(db_path).save(
            Customer(
                customer_id=str(customer_id),
                customer_name=customer_name,
                order_type=OrderType.POLARIS,
                billing_type="agency",
                separation_customer=int(separation[0]),
                separation_order=int(separation[1]),
                separation_event=int(separation[2]),
                code_name=code_name,
                description_name=description_name,
                include_market_in_code=include_market,
            )
        )
        print(f"[CUSTOMER DB] ✓ Saved: {customer_name} → ID {customer_id}  "
              f"(code prefix '{code_name}', description prefix '{description_name}')")
    except Exception as exc:
        print(f"[CUSTOMER DB] ✗ Save failed: {exc}")


# Etere field widths (INFORMATION_SCHEMA, 2026-09-28)
CODE_MAX = 32
DESC_MAX = 80


def _yymmdd(mdy: str) -> str:
    return datetime.strptime(mdy, "%m/%d/%Y").strftime("%y%m%d")


def _default_names(
    code_name: str, description_name: str, start: str, end: str, market_suffix: str = ""
) -> tuple[str, str]:
    """Polaris buys arrive one week at a time, so the weekly start date keeps
    every contract code unique (Lee 9/28, the shape of the six Prop C
    contracts: 'Yes on Prop C 260526'). Description carries the whole flight."""
    code = " ".join(p for p in (code_name, market_suffix, _yymmdd(start)) if p)
    desc = f"{description_name} {_yymmdd(start)}-{_yymmdd(end)}".strip()
    return code, desc


def _prompt_within(label: str, default: str, limit: int) -> str:
    """Bracket-default prompt that re-prompts until the value fits Etere's column."""
    while True:
        raw = input(f"  {label} [{default}]: ").strip()
        value = raw or default
        if len(value) <= limit:
            return value
        print(f"  ✗ {len(value)} characters — Etere allows {limit}. Shorten it.")


_CODE_TAIL = re.compile(r"\s+\d{6}$")
_DESC_TAIL = re.compile(r"\s+\d{6}-\d{6}$")


def _learn_prefixes(code: str, description: str, market_suffix: str = "") -> tuple[str, str]:
    """Back out the customer's code/description prefixes from what the operator
    typed at the contract prompts, so a hand-corrected name becomes the next
    order's default (Lee 9/28: 482 was saved with blank prefixes and the
    corrected names never reached the record).  Strips the `yymmdd` /
    `yymmdd-yymmdd` tails `_default_names` adds and, when the market rides in
    the code, its short suffix."""
    code_prefix = _CODE_TAIL.sub("", code.strip())
    if market_suffix and code_prefix.upper().endswith(" " + market_suffix.upper()):
        code_prefix = code_prefix[: -len(market_suffix) - 1]
    desc_prefix = _DESC_TAIL.sub("", description.strip())
    return code_prefix.strip(), desc_prefix.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Input gathering
# ─────────────────────────────────────────────────────────────────────────────

def gather_polaris_inputs(xlsx_path: str) -> Optional[dict]:
    """
    Parse the Polaris xlsx and collect all user inputs before the browser opens.

    Args:
        xlsx_path: Path to the .xlsx file.

    Returns:
        Dict with keys: order, customer_id, contracts (per-market code/description),
        separation.  Returns None if the user cancels.
    """
    order = parse_polaris_xlsx(xlsx_path)

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("POLARIS MEDIA GROUP")
    print(f"  Advertiser : {order.advertiser}")
    print(f"  Prepared by: {order.prepared_by}")
    print(f"  Flight     : {order.flight_start} – {order.flight_end}")
    print(f"  Budget     : ${order.gross_budget:,}")
    print(f"  Markets    : {', '.join(order.markets)}")
    print(f"  Lines      : {len(order.lines)} ({order.total_spots} spots)")
    print()
    for ln in order.lines:
        tf, tt = ln.get_time_from_to()
        rate_label = "BONUS" if ln.is_bonus else f"${ln.rate}"
        print(f"    [{ln.market}]  {ln.days:<8s}  {ln.time_str:<15s}  "
              f"{ln.program:<28s}  :{ln.duration}  {rate_label}  ×{ln.total_spots}")
    for w in getattr(order, "warnings", []):
        print(f"  ⚠ {w}")
    print(f"{'─'*60}")

    # ── Flight date confirmation (political orders often arrive day-of) ────────
    print(f"\n  Sheet start date: {order.flight_start}")
    start_input = input(
        "  Use this start date? [Enter=yes / type override, e.g. 4/17/2026]: "
    ).strip()
    actual_start = start_input if start_input and start_input.lower() not in ('y', 'yes') else order.flight_start

    print(f"\n  Sheet end date: {order.flight_end}")
    end_input = input(
        "  Use this end date? [Enter=yes / type override, e.g. 4/20/2026]: "
    ).strip()
    actual_end = end_input if end_input and end_input.lower() not in ('y', 'yes') else order.flight_end

    if actual_start != order.flight_start or actual_end != order.flight_end:
        print(f"  ✓ Using adjusted flight: {actual_start} – {actual_end}")

    # ── Customer lookup ───────────────────────────────────────────────────────
    existing = _lookup_customer(order.advertiser)
    customer_id: Optional[str] = None
    code_name: str = ""
    description_name: str = ""
    include_market: bool = len(order.markets) > 1

    if existing:
        stored_id   = existing.get("customer_id", "")
        stored_code = existing.get("code_name", "") or ""
        stored_desc = existing.get("description_name", "") or ""
        stored_mkt  = bool(existing.get("include_market_in_code", 0))
        print(f"\n[CUSTOMER DB] Found: {existing['customer_name']}")
        print(f"  Customer ID : {stored_id}")
        print(f"  Code prefix : {stored_code}")
        customer_id = prompt_customer_id(stored_id)
        if not customer_id:
            print("[CANCELLED] No customer ID entered.")
            return None
        code_name        = stored_code
        description_name = stored_desc
        include_market   = stored_mkt
    else:
        print(f"\n[CUSTOMER DB] '{order.advertiser}' not found in database.")
        customer_id = prompt_customer_id()
        if not customer_id:
            print("[CANCELLED] No customer ID entered.")
            return None
        if include_market:
            inc = input("  Append market to contract code? [Y/n]: ").strip().lower()
            include_market = inc not in ("n", "no")
        # Code/description prefixes are learned from the contract prompts below
        # and saved with the record — never asked blind up front.

    # ── Per-market contract code / description ────────────────────────────────
    # A record with no prefix yet gets a suggestion in the house shape
    # (`Polaris <yymmdd>`, advertiser trimmed to fit); whatever the operator
    # types is learned back into the record as the next default.
    date_tail = len(f" {_yymmdd(actual_start)}-{_yymmdd(actual_end)}")
    suggested_code = code_name or "Polaris"
    suggested_desc = description_name or order.advertiser.strip()[: DESC_MAX - date_tail].rstrip(" ,")
    contracts: dict[str, dict] = {}
    learned: Optional[tuple[str, str]] = None
    for market in order.markets:
        suffix = _MARKET_SHORT.get(market, market) if include_market else ""
        default_code, default_desc = _default_names(
            suggested_code, suggested_desc, actual_start, actual_end, suffix,
        )

        print(f"\n  Market: {market}")
        code = _prompt_within("Contract code", default_code, CODE_MAX)
        desc = _prompt_within("Contract description", default_desc, DESC_MAX)
        contracts[market] = {"code": code, "description": desc}
        if learned is None:
            learned = _learn_prefixes(code, desc, suffix)

    if learned and learned != (code_name, description_name):
        code_name, description_name = learned
        if existing:
            sep = (
                int(existing.get("separation_customer", 15) or 15),
                int(existing.get("separation_order", 0) or 0),
                int(existing.get("separation_event", 0) or 0),
            )
        else:
            sep = (15, 0, 0)
        _upsert_customer(
            customer_id, order.advertiser,
            code_name, description_name, include_market, sep,
        )

    # ── Separation: (customer, order, event) — the order the entry API takes ──
    if existing:
        sep_c = int(existing.get("separation_customer", 15) or 15)
        sep_o = int(existing.get("separation_order",    0)  or 0)
        sep_e = int(existing.get("separation_event",    0)  or 0)
        separation = (sep_c, sep_o, sep_e)
    else:
        separation = (15, 0, 0)

    return {
        "order":        order,
        "customer_id":  customer_id,
        "contracts":    contracts,
        "separation":   separation,
        "actual_start": actual_start,
        "actual_end":   actual_end,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Order processing
# ─────────────────────────────────────────────────────────────────────────────

def process_polaris_order(xlsx_path: str, user_input: dict) -> Optional[str]:
    """
    Enter a Polaris order into Etere via direct DB.  Creates one contract per market.

    Args:
        xlsx_path:  Path to the .xlsx file (re-parsed from disk for safety).
        user_input: Dict returned by gather_polaris_inputs.

    Returns:
        Contract number of the last created contract, or None on failure.
    """
    order = user_input.get("order") or parse_polaris_xlsx(xlsx_path)
    return _create_polaris_contracts_direct(order, user_input)
