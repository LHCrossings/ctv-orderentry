"""
Lexus / IW Group Order Automation
Browser automation for entering Lexus orders (via IW Group buyer Melissa) into Etere.

═══════════════════════════════════════════════════════════════════════════════
BUSINESS RULES
═══════════════════════════════════════════════════════════════════════════════

Customer:
    IW Group / Lexus (DB ID: 13)
    - Separation: 25, 0, 0  (customer=25, event=0, order=0)

Billing (Universal for ALL Lexus / IW Group):
    - Charge To: "Customer share indicating agency %"
    - Invoice Header: "Agency"

File Formats:
    - JPG screenshots  → new orders
    - XLSX spreadsheets → revisions

Contract Format:
    - Code:        "IW Lexus {estimate} {market} {quarter_code}"
                   e.g. "IW Lexus 202 NYC 2601"
    - Description: "Lexus {market} {language} Est {estimate} {quarter_label}"
                   e.g. "Lexus NYC Hinglish Est 202 26Q1"

Quarter codes (from broadcast month):
    Jan-Mar → 2601 / 26Q1
    Apr-Jun → 2604 / 26Q2
    Jul-Sep → 2607 / 26Q3
    Oct-Dec → 2610 / 26Q4

Rate Handling:
    - Document rates are NET
    - Must gross up: net / 0.85
    - BNS bonus spots: rate = $0, spot_code = 10

Line Generation:
    - spots_per_week from document; max_daily_run = ceil(spots / active_days)
    - Consecutive weeks with identical (program, time, days, rate, spots_per_week)
      are merged into one Etere line
    - BNS lines always entered separately after paid lines

Revision Flow:
    - Prompt for existing contract number (no new contract header created)
    - Prompt for cutoff date; filter out weeks entirely before that date
    - For weeks straddling the cutoff, start_date = cutoff_date

Melissa Check:
    - Non-blocking: show warnings and ask user to confirm before continuing

═══════════════════════════════════════════════════════════════════════════════
"""

import calendar
import math
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from etere_client import EtereClient
from src.domain.enums import BillingType

from parsers.lexus_parser import (
    LexusLine,
    LexusParseResult,
    _extract_days_from_program,
    _extract_time_from_program,
    melissa_check,
    parse_lexus_file,
    parse_lexus_filename,
    resolve_week_dates,
)

# ───────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ───────────────────────────────────────────────────────────────────────────

LEXUS_CUSTOMER_ID = 13
LEXUS_SEPARATION = (25, 0, 0)      # customer=25, event=0, order=0
LEXUS_BILLING = BillingType.CUSTOMER_SHARE_AGENCY
CUSTOMER_DB_PATH = os.path.join("data", "customers.db")


# ───────────────────────────────────────────────────────────────────────────
# QUARTER HELPERS
# ───────────────────────────────────────────────────────────────────────────

_MONTH_ABBR_TO_INT = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _broadcast_month_to_quarter(broadcast_month: str) -> tuple[str, str]:
    """
    Convert broadcast month (e.g. "Jan-26") to quarter code and label.

    Returns:
        (quarter_code, quarter_label) e.g. ("2601", "26Q1")
    """
    # Parse month
    import re
    m = re.match(r'([A-Za-z]+)[\s\-]+(\d{2,4})', broadcast_month)
    if not m:
        return ("2601", "26Q1")  # fallback

    mon_str = m.group(1).lower()[:3]
    yr_str = m.group(2)
    if len(yr_str) == 4:
        yr_str = yr_str[2:]

    month_num = _MONTH_ABBR_TO_INT.get(mon_str, 1)

    if month_num <= 3:
        q_num, q_month = 1, 1
    elif month_num <= 6:
        q_num, q_month = 2, 4
    elif month_num <= 9:
        q_num, q_month = 3, 7
    else:
        q_num, q_month = 4, 10

    quarter_code = f"{yr_str}{q_month:02d}"
    quarter_label = f"{yr_str}Q{q_num}"
    return quarter_code, quarter_label


# ───────────────────────────────────────────────────────────────────────────
# ETERE LINE BUILDING
# ───────────────────────────────────────────────────────────────────────────

def _build_etere_lines(
    parse_result: LexusParseResult,
    include_bns: bool,
    spot_duration: int,
    cutoff_date: Optional[date] = None,
) -> list[dict]:
    """
    Convert parsed Lexus lines into Etere-ready line specifications.

    Consecutive weeks with same (program, time, days, rate, spots_per_week)
    are merged into a single Etere line.

    If cutoff_date is set (revision mode), weeks entirely before cutoff are
    dropped, and weeks straddling the cutoff use cutoff_date as start.

    Returns list of dicts with keys:
        days, time, start_date, end_date, total_spots, spots_per_week,
        max_daily_run, rate, description, is_bonus, spot_code, duration
    """
    etere_lines: list[dict] = []

    week_date_ranges = parse_result.lines[0].week_date_ranges if parse_result.lines else []

    for line in parse_result.lines:
        if not include_bns and line.is_bonus:
            continue

        # Per-week data: (start, end, spots)
        week_data = list(zip(line.week_date_ranges, line.spots_by_week))

        # Filter by cutoff_date (revision mode)
        if cutoff_date:
            filtered = []
            for (wk_start, wk_end), spots in week_data:
                if wk_end < cutoff_date:
                    continue  # entirely before cutoff — skip
                if wk_start < cutoff_date:
                    wk_start = cutoff_date  # straddles cutoff — trim start
                filtered.append(((wk_start, wk_end), spots))
            week_data = filtered

        if not week_data:
            continue

        # Group consecutive weeks with same spot count
        # A group = run of consecutive weeks (no gap) with identical spots_per_week
        groups: list[list[tuple[tuple[date, date], int]]] = []
        current_group: list[tuple[tuple[date, date], int]] = []

        for (wk_start, wk_end), spots in week_data:
            if spots <= 0 and not line.is_bonus:
                # Zero-spot week breaks a group
                if current_group:
                    groups.append(current_group)
                    current_group = []
                continue

            if not current_group:
                current_group.append(((wk_start, wk_end), spots))
            else:
                prev_end = current_group[-1][0][1]
                same_spots = current_group[-1][1] == spots
                consecutive = (wk_start - prev_end).days <= 2  # allow 1-day gap (weekend)

                if same_spots and consecutive:
                    current_group.append(((wk_start, wk_end), spots))
                else:
                    groups.append(current_group)
                    current_group = [((wk_start, wk_end), spots)]

        if current_group:
            groups.append(current_group)

        # Convert each group to an Etere line
        for group in groups:
            group_start = group[0][0][0]
            group_end = group[-1][0][1]
            spots_per_week = group[0][1]  # same for all in group
            total_spots = sum(g[1] for g in group)

            # max_daily_run = ceil(spots_per_week / active_day_count)
            from parsers.lexus_parser import _parse_day_codes
            active_days = len(_parse_day_codes(line.days))
            if active_days > 0 and spots_per_week > 0:
                max_daily_run = math.ceil(spots_per_week / active_days)
            else:
                max_daily_run = spots_per_week

            rate = 0.0 if line.is_bonus else line.rate_gross
            spot_code = 10 if line.is_bonus else 2
            desc_suffix = " BNS" if line.is_bonus else ""
            description = f"{line.program} {line.time}{desc_suffix}".strip()

            etere_lines.append({
                "days": line.days,
                "time": line.time,
                "start_date": group_start,
                "end_date": group_end,
                "total_spots": total_spots,
                "spots_per_week": spots_per_week,
                "max_daily_run": max_daily_run,
                "rate": rate,
                "description": description,
                "is_bonus": line.is_bonus,
                "spot_code": spot_code,
                "duration": spot_duration,
            })

    return etere_lines


# ───────────────────────────────────────────────────────────────────────────
# OCR FAILURE DETECTION & MANUAL ENTRY FALLBACK
# ───────────────────────────────────────────────────────────────────────────

def _detect_ocr_failure(result: LexusParseResult, file_path: Path) -> bool:
    """Return True when the OCR parse result looks unreliable."""
    if file_path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
        return False
    return result.broadcast_month == "Unknown" or len(result.lines) < 4


def _manual_entry_fallback(
    estimate: str,
    market: str,
    language: str,
) -> Optional[LexusParseResult]:
    """
    Interactive manual entry for Lexus orders when OCR fails.
    Builds a LexusParseResult from user-supplied data.
    """
    print("\n" + "─" * 70)
    print("MANUAL ENTRY MODE — Enter order data from the document")
    print("─" * 70)

    # Campaign year
    current_year = datetime.now().year
    year_input = input(f"\n  Campaign year [{current_year}]: ").strip() or str(current_year)
    try:
        year = int(year_input)
    except ValueError:
        year = current_year

    # Number of lines
    try:
        num_paid = int(input("\n  Number of paid lines: ").strip() or "0")
    except ValueError:
        num_paid = 0
    try:
        num_bonus = int(input("  Number of BNS bonus lines: ").strip() or "0")
    except ValueError:
        num_bonus = 0

    lines: list[LexusLine] = []
    all_start_dates: list[date] = []

    def _parse_date(s: str, yr: int) -> Optional[date]:
        m = re.match(r'^(\d{1,2})/(\d{1,2})$', s.strip())
        if m:
            try:
                return date(yr, int(m.group(1)), int(m.group(2)))
            except ValueError:
                pass
        return None

    def _collect_date_ranges(yr: int) -> list[tuple[tuple[date, date], int]]:
        """
        Prompt for date ranges with spot counts.
          M/D: N         → single week starting M/D (end = start+6)
          M/D-M/D: N     → explicit start–end range
        Returns list of ((start, end), spots).
        """
        print("    Date ranges (e.g. '1/6: 3'  or  '1/6-1/12: 3').  Blank to finish.")
        entries: list[tuple[tuple[date, date], int]] = []
        while True:
            raw = input("    > ").strip()
            if not raw:
                break
            m = re.match(r'^(\d{1,2}/\d{1,2})\s*[-–]\s*(\d{1,2}/\d{1,2})\s*:\s*(\d+)$', raw)
            if m:
                start = _parse_date(m.group(1), yr)
                end   = _parse_date(m.group(2), yr)
                spots = int(m.group(3))
                if start and end:
                    entries.append(((start, end), spots))
                    continue
            m = re.match(r'^(\d{1,2}/\d{1,2})\s*:\s*(\d+)$', raw)
            if m:
                start = _parse_date(m.group(1), yr)
                spots = int(m.group(2))
                if start:
                    end = start + timedelta(days=6)
                    entries.append(((start, end), spots))
                    continue
            print("    ⚠ Couldn't parse — use '1/6: 3' or '1/6-1/12: 3'")
        return entries

    def _enter_line(line_num: int, is_bonus: bool) -> Optional[LexusLine]:
        kind = "BNS bonus" if is_bonus else "paid"
        print(f"\n  --- Line {line_num} ({kind}) ---")
        program_raw = input("  Program name (e.g. 'M-F 2-3p Punjabi News'): ").strip()
        if not program_raw:
            return None

        auto_days = _extract_days_from_program(program_raw)
        auto_time = _extract_time_from_program(program_raw)

        days_input = input(f"  Days [{auto_days or 'M-F'}]: ").strip() or auto_days or "M-F"
        time_input = input(f"  Time [{auto_time or ''}]: ").strip() or auto_time or ""

        dur_input = input("  Duration (15/30) [30]: ").strip() or "30"
        try:
            duration = int(dur_input)
        except ValueError:
            duration = 30

        rate_net = 0.0
        rate_gross = 0.0
        if not is_bonus:
            rate_input = input("  Net rate (e.g. 150.00): ").strip()
            try:
                rate_net = float(rate_input)
                rate_gross = rate_net / 0.85
            except ValueError:
                print("  ⚠ Invalid rate — using $0")

        entries = _collect_date_ranges(year)
        if not entries:
            print("  ⚠ No date ranges entered — skipping line")
            return None

        week_date_ranges = [e[0] for e in entries]
        spots_by_week   = [e[1] for e in entries]
        all_start_dates.extend(s for (s, _e) in week_date_ranges)

        return LexusLine(
            program=program_raw,
            duration=duration,
            time=time_input,
            days=days_input,
            rate_net=rate_net,
            rate_gross=rate_gross,
            spots_by_week=spots_by_week,
            week_date_ranges=week_date_ranges,
            market=market,
            language=language,
            estimate=estimate,
            is_bonus=is_bonus,
        )

    for i in range(1, num_paid + 1):
        ln = _enter_line(i, is_bonus=False)
        if ln:
            lines.append(ln)

    for i in range(num_paid + 1, num_paid + num_bonus + 1):
        ln = _enter_line(i, is_bonus=True)
        if ln:
            lines.append(ln)

    if not lines:
        print("\n[MANUAL] ✗ No lines entered")
        return None

    # Derive broadcast_month from earliest date entered
    if all_start_dates:
        earliest = min(all_start_dates)
        broadcast_month = f"{earliest.strftime('%b')}-{str(earliest.year)[2:]}"
    else:
        broadcast_month = f"Jan-{str(year)[2:]}"

    week_headers = [
        f"{s.month}/{s.day}" for ln in lines for (s, _e) in ln.week_date_ranges
    ]

    print(f"\n[MANUAL] ✓ {len(lines)} line(s) entered")
    print(f"[MANUAL] Broadcast month: {broadcast_month}")

    return LexusParseResult(
        lines=lines,
        broadcast_month=broadcast_month,
        week_headers=week_headers,
        language=language,
        estimate=estimate,
        market=market,
        order_type="new",
    )


# ───────────────────────────────────────────────────────────────────────────
# UPFRONT INPUT COLLECTION
# ───────────────────────────────────────────────────────────────────────────

def gather_lexus_inputs(file_path: str) -> Optional[dict]:
    """
    Gather ALL user inputs BEFORE browser automation starts.

    This function:
    1. Parses the filename (market, estimate, language hint, order type)
    2. Parses the document (JPG OCR or XLSX) to get lines, broadcast month, language
    3. Resolves broadcast calendar week dates
    4. Runs Melissa Check (warns, prompts to continue)
    5. Collects new-order or revision-specific inputs
    6. Builds contract code, description, etere_lines

    Args:
        file_path: Path to JPG, PNG, or XLSX file

    Returns:
        Dict with all inputs needed for automation, or None on cancellation
    """
    print("\n" + "=" * 70)
    print("LEXUS / IW GROUP — UPFRONT INPUT COLLECTION")
    print("=" * 70)

    file_path = Path(file_path)

    # ── Parse filename ───────────────────────────────────────────────────
    meta = parse_lexus_filename(file_path.name)
    order_flow = meta["order_type"]     # "new" or "revision"
    estimate = meta["estimate"]
    market = meta["market"]

    print(f"\n[FILE]   {file_path.name}")
    print(f"[TYPE]   {order_flow.upper()}")
    print(f"[EST]    {estimate or '(not found in filename)'}")
    print(f"[MARKET] {market or '(not found in filename)'}")

    # ── Parse document ───────────────────────────────────────────────────
    print("\n[PARSE] Reading file...")
    try:
        result = parse_lexus_file(file_path)
    except Exception as e:
        print(f"[PARSE] ✗ Failed: {e}")
        import traceback
        traceback.print_exc()
        return None

    print(f"[PARSE] ✓ Broadcast month: {result.broadcast_month}")
    print(f"[PARSE] ✓ Week headers: {result.week_headers}")
    print(f"[PARSE] ✓ Lines parsed: {len(result.lines)}")

    # Merge estimate / market / language from parse result back into meta
    estimate = estimate or result.estimate or ""
    market = market or result.market or ""
    language = result.language or meta.get("language") or ""

    # Prompt for missing estimate
    if not estimate:
        estimate = input("\n  Estimate number (e.g. 202): ").strip()

    # Prompt for missing market
    if not market:
        market = input("  Market code (NYC, SFO, SEA, LAX, CVC, etc.): ").strip().upper()

    # Prompt for missing language
    if not language:
        print("\n  Language not detected. Options: Hinglish, Chinese, Viet, Korean, Filipino, Other")
        language = input("  Language: ").strip()

    print(f"\n[MARKET]   {market}")
    print(f"[LANGUAGE] {language}")
    print(f"[ESTIMATE] {estimate}")

    # ── OCR failure check ─────────────────────────────────────────────────
    if _detect_ocr_failure(result, file_path):
        print(
            f"\n[PARSE] ⚠ OCR yielded only {len(result.lines)} line(s) "
            f"with month='{result.broadcast_month}'"
        )
        print("[PARSE] Switching to manual entry mode...")
        result = _manual_entry_fallback(estimate, market, language)
        if result is None:
            return None

    # ── Melissa Check ────────────────────────────────────────────────────
    # Attach resolved week_date_ranges to lines before check
    if result.lines:
        warnings = melissa_check(result.lines)
        if warnings:
            print(f"\n[MELISSA CHECK] {len(warnings)} warning(s):")
            for w in warnings:
                print(f"  ⚠ {w}")
            confirm = input("\n  Continue anyway? [y/N]: ").strip().lower()
            if confirm != 'y':
                print("\n[CANCELLED] Aborted by user after Melissa Check warnings")
                return None
        else:
            print("\n[MELISSA CHECK] ✓ No warnings")
    else:
        warnings = []
        print("\n[MELISSA CHECK] (skipped — no lines parsed)")

    # ── Quarter code from broadcast month ────────────────────────────────
    quarter_code, quarter_label = _broadcast_month_to_quarter(result.broadcast_month)

    # ── Default contract code and description ────────────────────────────
    default_code = f"IW Lexus {estimate} {market} {quarter_code}"
    default_desc = f"Lexus {market} {language} Est {estimate} {quarter_label}"

    # ── New order vs revision ────────────────────────────────────────────
    contract_number: Optional[str] = None
    cutoff_date: Optional[date] = None
    separation = LEXUS_SEPARATION
    include_bns = False

    if order_flow == "new":
        # Separation (with override)
        print(f"\n[SEPARATION] Default: Customer={separation[0]}, Event={separation[1]}, Order={separation[2]}")
        sep_input = input("  Press Enter to confirm, or type new values (e.g. 25,0,0): ").strip()
        if sep_input:
            try:
                parts = [int(x.strip()) for x in sep_input.split(',')]
                if len(parts) == 3:
                    separation = tuple(parts)
            except ValueError:
                pass

        # Spot duration
        spot_dur_input = input("\n  Spot duration in seconds [30]: ").strip() or "30"
        try:
            spot_duration = int(spot_dur_input)
        except ValueError:
            spot_duration = 30

        # BNS bonus spots
        bns_input = input("\n  Include BNS bonus spots? [y/N]: ").strip().lower()
        include_bns = bns_input == 'y'

    else:
        # Revision: no new contract header
        spot_dur_input = input("\n  Spot duration in seconds [30]: ").strip() or "30"
        try:
            spot_duration = int(spot_dur_input)
        except ValueError:
            spot_duration = 30

        contract_number = input("\n  Existing contract number: ").strip()
        if not contract_number:
            print("[CANCELLED] No contract number provided")
            return None

        cutoff_str = input(
            "  First NEW date (spots before this date already entered, YYYY-MM-DD): "
        ).strip()
        if cutoff_str:
            try:
                cutoff_date = datetime.strptime(cutoff_str, "%Y-%m-%d").date()
                print(f"  ✓ Cutoff: {cutoff_date}")
            except ValueError:
                print("  ⚠ Invalid date format — no cutoff applied")

        # BNS
        bns_input = input("\n  Include BNS bonus spots? [y/N]: ").strip().lower()
        include_bns = bns_input == 'y'

    # ── Contract code / description prompts ──────────────────────────────
    print(f"\n[CONTRACT]")
    contract_code = input(f"  Code [{default_code}]: ").strip() or default_code
    contract_description = input(f"  Description [{default_desc}]: ").strip() or default_desc

    # ── Build Etere lines ────────────────────────────────────────────────
    etere_lines = _build_etere_lines(
        parse_result=result,
        include_bns=include_bns,
        spot_duration=spot_duration,
        cutoff_date=cutoff_date,
    )

    if not etere_lines:
        print("\n[LINES] ✗ No valid lines to enter (check cutoff date or spots counts)")
        return None

    print(f"\n[LINES] ✓ {len(etere_lines)} Etere line(s) ready")
    for i, ln in enumerate(etere_lines, 1):
        bns_flag = " [BNS]" if ln["is_bonus"] else ""
        print(
            f"  Line {i}: {ln['days']} {ln['time']}"
            f" | {ln['start_date']} - {ln['end_date']}"
            f" | {ln['total_spots']}x ({ln['spots_per_week']}/wk)"
            f" @ ${ln['rate']:.2f}{bns_flag}"
        )

    # Flight dates (union of all line ranges)
    all_starts = [ln["start_date"] for ln in etere_lines]
    all_ends = [ln["end_date"] for ln in etere_lines]
    flight_start = min(all_starts)
    flight_end = max(all_ends)

    print("\n" + "=" * 70)
    print("INPUT COLLECTION COMPLETE — Ready for automation")
    print("=" * 70)

    return {
        "order": result,
        "customer_id": LEXUS_CUSTOMER_ID,
        "market": market,
        "language": language,
        "estimate": estimate,
        "order_flow": order_flow,
        "contract_number": contract_number,       # None for new orders
        "cutoff_date": cutoff_date,
        "contract_code": contract_code,
        "contract_description": contract_description,
        "separation": separation,
        "etere_lines": etere_lines,
        "spot_duration": spot_duration,
        "include_bns": include_bns,
        "billing": LEXUS_BILLING,
        "flight_start": flight_start,
        "flight_end": flight_end,
        "melissa_warnings": warnings,
    }


# ───────────────────────────────────────────────────────────────────────────
# BROWSER AUTOMATION
# ───────────────────────────────────────────────────────────────────────────

def process_lexus_order(driver, file_path: str, user_input: dict = None) -> bool:
    """
    Process Lexus order with completely unattended browser automation.

    Follows admerasia_automation.py / worldlink_automation.py pattern.

    Args:
        driver:     Selenium WebDriver (raw driver)
        file_path:  Path to the JPG or XLSX file
        user_input: Pre-collected inputs from orchestrator (optional)

    Returns:
        True if all lines were entered successfully
    """
    if user_input is None:
        user_input = gather_lexus_inputs(file_path)
        if not user_input:
            return False

    etere_lines = user_input["etere_lines"]
    order_flow = user_input["order_flow"]
    billing = user_input["billing"]
    separation = user_input["separation"]
    market = user_input["market"]
    spot_duration = user_input["spot_duration"]

    print("\n" + "=" * 70)
    print("STARTING BROWSER AUTOMATION — LEXUS / IW GROUP")
    print("=" * 70)

    etere = EtereClient(driver)
    all_success = True

    try:
        # ── Create or retrieve contract ──────────────────────────────────
        if order_flow == "new":
            flight_start = user_input["flight_start"]
            flight_end = user_input["flight_end"]

            contract_number = etere.create_contract_header(
                customer_id=int(user_input["customer_id"]),
                code=user_input["contract_code"],
                description=user_input["contract_description"],
                contract_start=flight_start.strftime('%m/%d/%Y'),
                contract_end=flight_end.strftime('%m/%d/%Y'),
                charge_to=billing.get_charge_to(),
                invoice_header=billing.get_invoice_header(),
            )

            if not contract_number:
                print("[CONTRACT] ✗ Failed to create contract")
                return False

            print(f"[CONTRACT] ✓ Created: {contract_number}")

        else:
            # Revision: use existing contract number
            contract_number = user_input["contract_number"]
            print(f"[CONTRACT] Using existing contract: {contract_number}")

            # Extend contract end date to cover new lines if needed
            flight_end = user_input["flight_end"]
            try:
                etere._extend_contract_end_date(contract_number, flight_end.strftime('%m/%d/%Y'))
            except AttributeError:
                pass  # Method may not exist; non-fatal

        # ── Add contract lines ───────────────────────────────────────────
        for line_idx, line_spec in enumerate(etere_lines, 1):
            days = line_spec["days"]
            time_str = line_spec["time"]

            # Apply Sunday 6-7a rule (EtereClient standard)
            days, _ = EtereClient.check_sunday_6_7a_rule(days, time_str)

            # Parse time range
            time_from, time_to = EtereClient.parse_time_range(time_str)

            start_date_str = line_spec["start_date"].strftime('%m/%d/%Y')
            end_date_str = line_spec["end_date"].strftime('%m/%d/%Y')

            bns_flag = " [BNS]" if line_spec["is_bonus"] else ""
            print(f"\n[LINE {line_idx}] {days} {time_str}{bns_flag}")
            print(f"  {start_date_str} - {end_date_str}")
            print(
                f"  {line_spec['total_spots']}x total, "
                f"{line_spec['spots_per_week']}/wk, "
                f"{line_spec['max_daily_run']}x/day @ ${line_spec['rate']:.2f}"
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
                duration_seconds=spot_duration,
                total_spots=line_spec["total_spots"],
                spots_per_week=line_spec["spots_per_week"],
                max_daily_run=line_spec["max_daily_run"],
                rate=float(line_spec["rate"]),
                separation_intervals=separation,
            )

            if not success:
                print(f"  [LINE {line_idx}] ✗ Failed")
                all_success = False

        print(f"\n[COMPLETE] Contract {contract_number} — "
              f"{len(etere_lines)} line(s) processed")

    except Exception as e:
        print(f"\n[ERROR] Browser automation failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    return all_success


# ───────────────────────────────────────────────────────────────────────────
# STANDALONE ENTRY POINT
# ───────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 70)
    print("LEXUS AUTOMATION — STANDALONE MODE NOT SUPPORTED")
    print("=" * 70)
    print()
    print("Run through the orchestrator (main.py):")
    print("  1. Place JPG or XLSX in incoming/ folder")
    print("  2. Run: uv run python main.py")
    print("  3. Select the Lexus order from the menu")
    print("=" * 70)
    sys.exit(1)
