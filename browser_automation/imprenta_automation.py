"""
Imprenta / PG&E Order Automation

Browser automation for entering Imprenta agency orders into Etere.

═══════════════════════════════════════════════════════════════════════════════
BUSINESS RULES
═══════════════════════════════════════════════════════════════════════════════

Agency: Imprenta
Client: PG&E (and potentially other clients)

Billing:
    - Charge To: "Customer share indicating agency %"
    - Invoice Header: "Agency"

File Format: XLSX spreadsheet

Rate Handling:
    - Column header says "Net Amount" — rates are net
    - Gross up: net / 0.85 (standard 15% agency commission)
    - BNS bonus spots: rate = $0, spot_code = 10

Bookend Orders:
    - "Media type: 15-second bookends" in spreadsheet → is_bookend=True
    - Bookend paid lines: separation = (0, 0, 0) — position-locked
    - Bonus lines: separation = (15, 0, 0)

Contract Format (prompted, defaults shown):
    Code:        "Imprenta {client_code} {campaign_abbrev} {year_quarter}"  e.g. "Imprenta PGE TS 26Q1"
    Description: "{client} {campaign_short} {year_quarter}"                 e.g. "PG&E Traditional Safety 26Q1"
═══════════════════════════════════════════════════════════════════════════════
"""

import math
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from etere_client import EtereClient
from src.domain.enums import BillingType

from parsers.imprenta_parser import ImprentaLine, ImprentaParseResult, parse_imprenta_file

# ───────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ───────────────────────────────────────────────────────────────────────────

IMPRENTA_BILLING  = BillingType.CUSTOMER_SHARE_AGENCY
IMPRENTA_SEP_BOOKEND = (0, 0, 0)    # position-locked; no separation needed
IMPRENTA_SEP_BONUS   = (15, 0, 0)
IMPRENTA_SEP_DEFAULT = (15, 0, 0)


# ───────────────────────────────────────────────────────────────────────────
# HELPERS
# ───────────────────────────────────────────────────────────────────────────

def _campaign_code_parts(campaign: str, client: str) -> tuple[str, str, str]:
    """Derive contract code components from campaign name and client.

    Returns (client_code, abbrev, yq):
        client_code — client name stripped to alphanumeric (e.g. "PG&E" → "PGE")
        abbrev      — first letter of each word before the year (e.g. "TS", "WS")
        yq          — 2-digit year + quarter (e.g. "26Q1"), or "" if not found

    Example: "Traditional Safety 2026 Q1", "PG&E" → ("PGE", "TS", "26Q1")
    """
    client_code = re.sub(r'[^A-Za-z0-9]', '', client)
    m = re.search(r'\b(20\d{2})\s*(Q\d)\b', campaign, re.IGNORECASE)
    if m:
        yq = m.group(1)[2:] + m.group(2).upper()       # "26Q1"
        before = campaign[:m.start()].strip()
    else:
        yq = ""
        before = campaign
    abbrev = ''.join(w[0].upper() for w in before.split() if w)
    return client_code, abbrev, yq


# ───────────────────────────────────────────────────────────────────────────
# ETERE LINE BUILDING
# ───────────────────────────────────────────────────────────────────────────

def _build_etere_lines(
    parse_result: ImprentaParseResult,
    cutoff_date: Optional[date] = None,
) -> list[dict]:
    """
    Convert parsed Imprenta lines into Etere-ready line specifications.

    Consecutive weeks with same (program, time, days, rate, spots_per_week)
    are merged into a single Etere line.

    Returns list of dicts with keys:
        days, time, start_date, end_date, total_spots, spots_per_week,
        max_daily_run, rate, description, is_bonus, is_bookend,
        spot_code, duration, separation, start_day_warning
    """
    from day_utils import tokenize as _tok

    etere_lines: list[dict] = []
    _WD = {0: 'M', 1: 'T', 2: 'W', 3: 'R', 4: 'F', 5: 'S', 6: 'U'}

    for line in parse_result.lines:
        week_data = list(zip(line.week_date_ranges, line.spots_by_week))

        # Drop historical weeks (ended before current broadcast week)
        bws = date.today() - timedelta(days=date.today().weekday())
        week_data = [(wk, sp) for wk, sp in week_data if wk[1] >= bws]

        # Apply cutoff
        if cutoff_date:
            filtered = []
            for (wk_start, wk_end), spots in week_data:
                if wk_end < cutoff_date:
                    continue
                if wk_start < cutoff_date:
                    wk_start = cutoff_date
                filtered.append(((wk_start, wk_end), spots))
            week_data = filtered

        if not week_data:
            continue

        # Group consecutive weeks with same spot count
        groups: list[list] = []
        current: list = []
        for (wk_start, wk_end), spots in week_data:
            if spots <= 0:
                if current:
                    groups.append(current)
                    current = []
                continue
            if not current:
                current.append(((wk_start, wk_end), spots))
            else:
                prev_end = current[-1][0][1]
                same = current[-1][1] == spots
                consec = (wk_start - prev_end).days <= 2
                if same and consec:
                    current.append(((wk_start, wk_end), spots))
                else:
                    groups.append(current)
                    current = [((wk_start, wk_end), spots)]
        if current:
            groups.append(current)

        for group in groups:
            group_start = group[0][0][0]
            group_end   = group[-1][0][1]
            spots_per_week = group[0][1]
            total_spots    = sum(g[1] for g in group)

            # Bookend adjustment: Etere's Top+Bottom fires 2 spots per entry.
            # ALL lines in a bookend order (including bonus) are halved.
            # Rate is doubled for paid lines only (bonus rate stays $0).
            if parse_result.is_bookend:
                if spots_per_week % 2 != 0 or total_spots % 2 != 0:
                    raise ValueError(
                        f"BOOKEND ERROR: '{line.program}' has an odd spot count "
                        f"(spw={spots_per_week}, total={total_spots}). "
                        f"Bookends must run in pairs. Correct the order before entry."
                    )
                spots_per_week = spots_per_week // 2
                total_spots    = total_spots    // 2

            # max_daily_run — partial week aware
            day_span = (group_end - group_start).days + 1
            pattern_codes = set(_tok(line.days))
            if day_span >= 7:
                active_days = len(pattern_codes)
            else:
                active_days = sum(
                    1 for i in range(day_span)
                    if _WD[(group_start + timedelta(days=i)).weekday()] in pattern_codes
                ) or 1
            max_daily_run = math.ceil(spots_per_week / active_days) if active_days else spots_per_week

            # Separation
            if line.is_bonus:
                separation = IMPRENTA_SEP_BONUS
            elif line.is_bookend:
                separation = IMPRENTA_SEP_BOOKEND
            else:
                separation = IMPRENTA_SEP_DEFAULT

            # Description — bookend lines lead with "BOOKEND" (universal rule)
            program_tc = re.sub(r'\bRos\b', 'ROS', line.program.title())
            if line.is_bonus:
                description = f"BNS {program_tc}".strip()
            elif line.is_bookend:
                description = f"BOOKEND {program_tc}"
            else:
                description = program_tc

            # Start-day mismatch warning (cutoff trimming)
            start_day_warning = (
                cutoff_date is not None
                and group_start == cutoff_date
                and pattern_codes
                and _WD[group_start.weekday()] not in pattern_codes
            )
            if start_day_warning:
                pass  # flagged below in display

            etere_lines.append({
                "days":            line.days,
                "time":            line.time,
                "start_date":      group_start,
                "end_date":        group_end,
                "total_spots":     total_spots,
                "spots_per_week":  spots_per_week,
                "max_daily_run":   max_daily_run,
                "rate":            line.rate_gross * (2 if line.is_bookend else 1),
                "description":     description,
                "is_bonus":        line.is_bonus,
                "is_bookend":      line.is_bookend,
                "spot_code":       10 if line.is_bonus else 2,
                "duration":        line.duration,
                "separation":      separation,
                "start_day_warning": start_day_warning,
            })

    return etere_lines


# ───────────────────────────────────────────────────────────────────────────
# UPFRONT INPUT COLLECTION
# ───────────────────────────────────────────────────────────────────────────

def gather_imprenta_inputs(file_path: str) -> Optional[dict]:
    """
    Gather ALL user inputs BEFORE browser automation starts.

    Returns dict with all inputs needed, or None on cancellation.
    """
    print("\n" + "=" * 70)
    print("IMPRENTA — UPFRONT INPUT COLLECTION")
    print("=" * 70)

    file_path = Path(file_path)

    # ── Parse file ────────────────────────────────────────────────────────
    print("\n[PARSE] Reading file...")
    try:
        result = parse_imprenta_file(file_path)
    except Exception as e:
        print(f"[PARSE] ✗ Failed: {e}")
        import traceback; traceback.print_exc()
        return None

    print(f"[PARSE] ✓ Campaign:  {result.campaign}")
    print(f"[PARSE] ✓ Client:    {result.client}")
    print(f"[PARSE] ✓ Market:    {result.market}")
    print(f"[PARSE] ✓ Bookend:   {result.is_bookend}")
    print(f"[PARSE] ✓ Flight:    {result.flight_start} – {result.flight_end}")
    print(f"[PARSE] ✓ Weeks:     {[str(d) for d in result.week_start_dates]}")
    print(f"[PARSE] ✓ Lines:     {len(result.lines)}")

    # ── Gross-up confirmation ─────────────────────────────────────────────
    default_pct = "15"
    pct_input = input(f"\n  Agency commission % for gross-up [{default_pct}]: ").strip() or default_pct
    try:
        commission_pct = float(pct_input)
        gross_up_factor = 1 / (1 - commission_pct / 100)
        print(f"  ✓ Gross-up factor: ÷{(1 - commission_pct/100):.2f}  (net × {gross_up_factor:.4f})")
    except ValueError:
        print("  ⚠ Invalid — using 15%")
        gross_up_factor = 1 / 0.85

    # Re-parse with confirmed gross-up factor
    result = parse_imprenta_file(file_path, gross_up_factor=gross_up_factor)

    # ── Cutoff date ───────────────────────────────────────────────────────
    from datetime import timedelta
    _tmr = date.today() + timedelta(days=1)
    suggested = f"{_tmr.month}/{_tmr.day}/{_tmr.year}"
    cutoff_date: Optional[date] = None
    cutoff_str = input(
        f"\n  First date to enter (lines before this are skipped/trimmed) [{suggested}]: "
    ).strip() or suggested
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y"):
        try:
            cutoff_date = datetime.strptime(cutoff_str, fmt).date()
            print(f"  ✓ First entry date: {cutoff_date}")
            break
        except ValueError:
            pass
    else:
        print("  ⚠ Couldn't parse date — no cutoff applied")

    # ── Build Etere lines ─────────────────────────────────────────────────
    try:
        etere_lines = _build_etere_lines(result, cutoff_date=cutoff_date)
    except ValueError as e:
        print(f"\n[ERROR] ✗ {e}")
        print("[ERROR] This bookend order has an odd number of spots and needs to be corrected by the AE before entry.")
        return None
    if not etere_lines:
        print("\n[LINES] ✗ No valid lines found after cutoff filtering")
        return None

    # ── Display lines + warnings ──────────────────────────────────────────
    flight_start = min(ln["start_date"] for ln in etere_lines)
    flight_end   = max(ln["end_date"]   for ln in etere_lines)

    print(f"\n{'─' * 60}")
    print(f"[LINES] {len(etere_lines)} line(s)  |  {flight_start} – {flight_end}")
    start_day_issues = []
    for i, ln in enumerate(etere_lines, 1):
        bns  = " [BNS]"  if ln["is_bonus"]   else ""
        bkn  = " [BKN]"  if ln["is_bookend"] else ""
        warn = " ⚠ START DAY MISMATCH" if ln.get("start_day_warning") else ""
        print(
            f"  {i}. {ln['days']} {ln['time']}"
            f" | {ln['start_date']} - {ln['end_date']}"
            f" | {ln['total_spots']}x @ ${ln['rate']:.2f}{bns}{bkn}{warn}"
        )
        if ln.get("start_day_warning"):
            start_day_issues.append(
                f"Line {i}: start {ln['start_date']} "
                f"({ln['start_date'].strftime('%A')}) not valid for {ln['days']}"
            )

    if start_day_issues:
        print(f"\n  ⚠ START DATE WARNING(S):")
        for msg in start_day_issues:
            print(f"    • {msg}")
        print(f"  Lines will be entered as-is — adjust day pattern in Etere if needed.")

    # ── Contract details ──────────────────────────────────────────────────
    campaign_short = re.sub(r'\s+\d{4}.*$', '', result.campaign).strip()  # e.g. "Traditional Safety"
    client_code, abbrev, yq = _campaign_code_parts(result.campaign, result.client)
    default_code = f"Imprenta {client_code} {abbrev} {yq}".strip()       # e.g. "Imprenta PGE TS 26Q1"
    default_desc = f"{result.client} {campaign_short} {yq}".strip()      # e.g. "PG&E Traditional Safety 26Q1"

    print(f"\n{'─' * 60}")
    flow_input = input("  New contract or add to existing? [N=new / A=add / S=skip]: ").strip().upper()
    if flow_input == 'S':
        print("  [SKIPPED]")
        return None
    elif flow_input == 'A':
        contract_number = input("  Existing contract number: ").strip()
        if not contract_number:
            print("  [CANCELLED] No contract number provided")
            return None
        order_flow = "add"
        contract_code = default_code
        contract_description = default_desc
    else:
        order_flow = "new"
        contract_number = None
        contract_code        = input(f"  Code [{default_code}]: ").strip() or default_code
        contract_description = input(f"  Description [{default_desc}]: ").strip() or default_desc

    # ── Customer ID — look up saved value, then prompt ────────────────────
    import sys as _sys
    _src = Path(__file__).parent.parent / "src"
    if str(_src) not in _sys.path:
        _sys.path.insert(0, str(_src))
    from data_access.repositories.customer_repository import CustomerRepository
    from domain.entities import Customer
    from domain.enums import OrderType as _OT

    _repo = CustomerRepository(Path(__file__).parent.parent / "data" / "customers.db")
    _existing = _repo.find_by_name_any_type(result.client) or []
    _saved_id = next(
        (c.customer_id for c in _existing if c.order_type == _OT.IMPRENTA),
        None
    )

    if _saved_id:
        cid_prompt = f"\n  Etere customer ID [{_saved_id}]: "
    else:
        cid_prompt = "\n  Etere customer ID (blank = manual search in browser): "

    cid_input = input(cid_prompt).strip() or _saved_id or ""
    try:
        customer_id = int(cid_input)
    except (ValueError, TypeError):
        customer_id = None

    # Write customer ID back to DB for future pre-population
    if customer_id is not None:
        try:
            _repo.save(Customer(
                customer_id=str(customer_id),
                customer_name=result.client,
                order_type=_OT.IMPRENTA,
                billing_type="agency",
                code_name=result.client,
                description_name=result.client,
                default_market=result.market,
            ))
        except Exception as e:
            print(f"  ⚠ Could not save customer to DB: {e}")

    print("\n" + "=" * 70)
    print("INPUT COLLECTION COMPLETE")
    print("=" * 70)

    return {
        "order":                result,
        "customer_id":          customer_id,
        "market":               result.market,
        "billing":              IMPRENTA_BILLING,
        "order_flow":           order_flow,
        "contract_number":      contract_number,
        "contract_code":        contract_code,
        "contract_description": contract_description,
        "flight_start":         flight_start,
        "flight_end":           flight_end,
        "etere_lines":          etere_lines,
        "gross_up_factor":      gross_up_factor,
    }


# ───────────────────────────────────────────────────────────────────────────
# BROWSER AUTOMATION
# ───────────────────────────────────────────────────────────────────────────

def process_imprenta_order(driver, file_path: str, user_input: dict = None) -> bool:
    """
    Process Imprenta order with browser automation.

    Args:
        driver:     Selenium WebDriver
        file_path:  Path to the XLSX file
        user_input: Pre-collected inputs from orchestrator (optional)

    Returns:
        True if all lines were entered successfully
    """
    if user_input is None:
        user_input = gather_imprenta_inputs(file_path)
        if not user_input:
            return False

    billing    = user_input["billing"]
    market     = user_input["market"]
    order_flow = user_input["order_flow"]
    etere_lines = user_input["etere_lines"]

    print("\n" + "=" * 70)
    print("STARTING BROWSER AUTOMATION — IMPRENTA")
    print("=" * 70)

    etere = EtereClient(driver)
    all_success = True

    try:
        # Master market must be set before navigating to /sales/new
        etere.set_master_market("NYC")

        # ── Create or retrieve contract ───────────────────────────────────
        if order_flow == "new":
            contract_number = etere.create_contract_header(
                customer_id=int(user_input["customer_id"]) if user_input["customer_id"] else None,
                code=user_input["contract_code"],
                description=user_input["contract_description"],
                contract_start=user_input["flight_start"].strftime('%m/%d/%Y'),
                contract_end=user_input["flight_end"].strftime('%m/%d/%Y'),
                charge_to=billing.get_charge_to(),
                invoice_header=billing.get_invoice_header(),
            )
            if not contract_number:
                print("[ERROR] ✗ Failed to create contract")
                return False
            print(f"[CONTRACT] ✓ Created: {contract_number}")
        else:
            contract_number = user_input["contract_number"]
            print(f"[CONTRACT] Adding to existing: {contract_number}")
            lines_for_extend = [
                {"end_date": ln["end_date"].strftime('%m/%d/%Y')}
                for ln in etere_lines
            ]
            if not etere.extend_contract_end_date(contract_number, lines_for_extend):
                print("[ERROR] ✗ Failed to extend contract end date")
                return False

        # ── Add contract lines ────────────────────────────────────────────
        for line_idx, line_spec in enumerate(etere_lines, 1):
            days     = line_spec["days"]
            time_str = line_spec["time"]
            days, _  = EtereClient.check_sunday_6_7a_rule(days, time_str)

            if time_str:
                time_from, time_to = EtereClient.parse_time_range(time_str)
            else:
                time_from, time_to = "06:00", "23:59"

            start_date_str = line_spec["start_date"].strftime('%m/%d/%Y')
            end_date_str   = line_spec["end_date"].strftime('%m/%d/%Y')

            bns_flag = " [BNS]" if line_spec["is_bonus"] else ""
            bkn_flag = " [BKN]" if line_spec["is_bookend"] else ""
            print(f"\n  [LINE {line_idx}] {days} {time_str}{bns_flag}{bkn_flag}")
            print(f"    {start_date_str} – {end_date_str}")
            print(
                f"    {line_spec['total_spots']}x total, "
                f"{line_spec['spots_per_week']}/wk, "
                f"{line_spec['max_daily_run']}x/day @ ${line_spec['rate']:.2f}  "
                f"sep={line_spec['separation']}"
            )

            success = etere.add_contract_line(
                contract_number=contract_number,
                market=market,
                start_date=start_date_str,
                end_date=end_date_str,
                days=days,
                time_from=time_from,
                time_to=time_to,
                description=line_spec["description"],
                spot_code=line_spec["spot_code"],
                duration_seconds=line_spec["duration"],
                total_spots=line_spec["total_spots"],
                spots_per_week=line_spec["spots_per_week"],
                max_daily_run=line_spec["max_daily_run"],
                rate=float(line_spec["rate"]),
                separation_intervals=line_spec["separation"],
                is_bookend=line_spec["is_bookend"],
            )
            if not success:
                print(f"    ✗ Failed")
                all_success = False

        print(f"\n[CONTRACT] ✓ {contract_number} — {len(etere_lines)} line(s) complete")

    except Exception as e:
        print(f"\n[ERROR] Browser automation failed: {e}")
        import traceback; traceback.print_exc()
        return False

    return all_success


# ───────────────────────────────────────────────────────────────────────────
# STANDALONE ENTRY POINT
# ───────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Run through the orchestrator (main.py)")
    sys.exit(1)
