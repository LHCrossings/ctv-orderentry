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
# HELPERS
# ───────────────────────────────────────────────────────────────────────────

def _date_to_quarter(d: date) -> tuple[str, str]:
    """Return (quarter_code, quarter_label) for a given date.
    e.g. 2026-03-16 → ("2601", "26Q1")
    """
    yr2 = str(d.year)[2:]
    if d.month <= 3:
        return f"{yr2}01", f"{yr2}Q1"
    elif d.month <= 6:
        return f"{yr2}04", f"{yr2}Q2"
    elif d.month <= 9:
        return f"{yr2}07", f"{yr2}Q3"
    else:
        return f"{yr2}10", f"{yr2}Q4"


def _window_minutes(time_str: str) -> int:
    """Return duration of the time window in minutes. Returns 0 on parse failure."""
    def _to_mins(t: str) -> int:
        import re
        m = re.match(r'^(\d{1,2})(?::(\d{2}))?([AP]M)$', t.strip().upper())
        if not m:
            return -1
        hour, minute, period = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if period == 'PM' and hour != 12:
            hour += 12
        elif period == 'AM' and hour == 12:
            hour = 0
        return hour * 60 + minute

    parts = time_str.split('-')
    if len(parts) < 2:
        return 0
    start, end = _to_mins(parts[0]), _to_mins(parts[-1])
    return (end - start) if start >= 0 and end > start else 0


# ───────────────────────────────────────────────────────────────────────────
# ETERE LINE BUILDING
# ───────────────────────────────────────────────────────────────────────────

def _build_etere_lines(
    parse_result: LexusParseResult,
    include_bns: bool,
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

        # Drop weeks that ended before the current broadcast week (historical data)
        broadcast_week_start = date.today() - timedelta(days=date.today().weekday())
        week_data = [(wk, sp) for wk, sp in week_data if wk[1] >= broadcast_week_start]

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
            if spots <= 0:
                # Zero-spot week breaks a group (paid and bonus alike)
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
            # For partial weeks, count only the days that actually fall in the range.
            from parsers.lexus_parser import _parse_day_codes
            day_span = (group_end - group_start).days + 1
            if day_span >= 7:
                active_days = len(_parse_day_codes(line.days))
            else:
                # Map Python weekday() → Etere single-letter codes
                _WD_CODE = {0: 'M', 1: 'T', 2: 'W', 3: 'R', 4: 'F', 5: 'S', 6: 'U'}
                pattern_codes = set(_parse_day_codes(line.days))
                active_days = sum(
                    1 for i in range(day_span)
                    if _WD_CODE[(group_start + timedelta(days=i)).weekday()] in pattern_codes
                ) or 1  # floor at 1 to avoid div/0
            if active_days > 0 and spots_per_week > 0:
                max_daily_run = math.ceil(spots_per_week / active_days)
            else:
                max_daily_run = spots_per_week

            rate = 0.0 if line.is_bonus else line.rate_gross
            spot_code = 10 if line.is_bonus else 2
            desc_prefix = "BNS " if line.is_bonus else ""
            program_tc = re.sub(r'\bRos\b', 'ROS', line.program.title())
            description = f"{desc_prefix}{program_tc}".strip()

            # Per-line separation
            if line.is_bonus:
                line_separation = (15, 0, 0)
            elif max_daily_run <= 1:
                line_separation = LEXUS_SEPARATION
            else:
                window = _window_minutes(line.time)
                customer_sep = min(LEXUS_SEPARATION[0], max(0, window // max_daily_run - 5)) if window > 0 else LEXUS_SEPARATION[0]
                line_separation = (customer_sep, LEXUS_SEPARATION[1], LEXUS_SEPARATION[2])

            # Flag when cutoff trimming left the start date on an invalid pattern day
            from day_utils import tokenize as _tokenize_days
            _WD_CODE = {0: 'M', 1: 'T', 2: 'W', 3: 'R', 4: 'F', 5: 'S', 6: 'U'}
            start_day_code = _WD_CODE[group_start.weekday()]
            pattern_codes_check = set(_tokenize_days(line.days))
            start_day_warning = (
                cutoff_date is not None
                and group_start == cutoff_date
                and pattern_codes_check
                and start_day_code not in pattern_codes_check
            )

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
                "duration": line.duration,
                "separation": line_separation,
                "start_day_warning": start_day_warning,
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
    is_revision = (order_flow == "revision")
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

    # ── First entry date ─────────────────────────────────────────────────
    # Melissa sends orders late. Spots before this date won't be entered;
    # lines that straddle it have their start date trimmed to this date.
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

    # ── Build all Etere lines ────────────────────────────────────────────
    all_etere_lines = _build_etere_lines(parse_result=result, include_bns=True, cutoff_date=cutoff_date)

    if not all_etere_lines:
        print("\n[LINES] ✗ No valid lines found")
        return None

    # ── Group lines by broadcast quarter ─────────────────────────────────
    from collections import defaultdict
    quarter_buckets: dict[tuple, list] = defaultdict(list)
    for ln in all_etere_lines:
        qkey = _date_to_quarter(ln["start_date"])   # (quarter_code, quarter_label)
        quarter_buckets[qkey].append(ln)

    tomorrow = date.today() + timedelta(days=1)
    contracts = []

    for qkey in sorted(quarter_buckets.keys()):
        quarter_code, quarter_label = qkey
        q_lines = quarter_buckets[qkey]
        q_flight_start = min(ln["start_date"] for ln in q_lines)
        q_flight_end   = max(ln["end_date"]   for ln in q_lines)

        default_code = f"IW Lexus {estimate} {market} {quarter_code}"
        default_desc = f"Lexus {market} {language} Est {estimate} {quarter_label}"

        print(f"\n{'─' * 60}")
        print(f"[{quarter_label}] {len(q_lines)} line(s)  |  {q_flight_start} – {q_flight_end}")
        start_day_issues = []
        for i, ln in enumerate(q_lines, 1):
            bns = " [BNS]" if ln["is_bonus"] else ""
            warn = " ⚠ START DAY MISMATCH" if ln.get("start_day_warning") else ""
            print(
                f"  {i}. {ln['days']} {ln['time']}"
                f" | {ln['start_date']} - {ln['end_date']}"
                f" | {ln['total_spots']}x @ ${ln['rate']:.2f}{bns}{warn}"
            )
            if ln.get("start_day_warning"):
                start_day_issues.append(
                    f"Line {i}: start date {ln['start_date']} "
                    f"({ln['start_date'].strftime('%A')}) is not a valid {ln['days']} day"
                )

        if start_day_issues:
            print(f"\n  ⚠ START DATE WARNING(S) — cutoff trimming left these lines starting on an invalid day:")
            for msg in start_day_issues:
                print(f"    • {msg}")
            print(f"  Lines will be entered as-is — adjust day pattern to M-Su in Etere if needed.")

        flow_input = input("\n  New contract or add to existing? [N=new / A=add / S=skip]: ").strip().upper()
        if flow_input == 'S':
            print(f"  [SKIPPED] {quarter_label}")
            continue
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
            contract_code = input(f"  Code [{default_code}]: ").strip() or default_code
            contract_description = input(f"  Description [{default_desc}]: ").strip() or default_desc

        contracts.append({
            "quarter_code": quarter_code,
            "quarter_label": quarter_label,
            "order_flow": order_flow,
            "contract_number": contract_number,
            "contract_code": contract_code,
            "contract_description": contract_description,
            "etere_lines": q_lines,
            "flight_start": q_flight_start,
            "flight_end": q_flight_end,
        })

    print("\n" + "=" * 70)
    print(f"INPUT COLLECTION COMPLETE — {len(contracts)} contract(s) to process")
    print("=" * 70)

    # Silently upsert customer to DB so future orders pre-populate
    try:
        _src = Path(__file__).parent.parent / "src"
        if str(_src) not in sys.path:
            sys.path.insert(0, str(_src))
        from data_access.repositories.customer_repository import CustomerRepository
        from domain.entities import Customer
        from domain.enums import OrderType as _OT
        _repo = CustomerRepository(Path(__file__).parent.parent / "data" / "customers.db")
        _repo.save(Customer(
            customer_id=str(LEXUS_CUSTOMER_ID),
            customer_name="Lexus",
            order_type=_OT.LEXUS,
            billing_type="agency",
            code_name="IW Lexus",
            description_name="Lexus",
            default_market=market,
        ))
    except Exception:
        pass  # DB write is best-effort — never block order entry

    return {
        "order": result,
        "customer_id": LEXUS_CUSTOMER_ID,
        "market": market,
        "language": language,
        "estimate": estimate,
        "billing": LEXUS_BILLING,
        "contracts": contracts,
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

    billing = user_input["billing"]
    market  = user_input["market"]

    print("\n" + "=" * 70)
    print("STARTING BROWSER AUTOMATION — LEXUS / IW GROUP")
    print("=" * 70)

    etere = EtereClient(driver)
    all_success = True

    try:
        for contract_job in user_input["contracts"]:
            order_flow    = contract_job["order_flow"]
            etere_lines   = contract_job["etere_lines"]
            quarter_label = contract_job["quarter_label"]

            print(f"\n{'═' * 60}")
            print(f"[{quarter_label}] {len(etere_lines)} line(s) — {order_flow.upper()}")

            # ── Create or retrieve contract ───────────────────────────
            if order_flow == "new":
                contract_number = etere.create_contract_header(
                    customer_id=int(user_input["customer_id"]),
                    code=contract_job["contract_code"],
                    description=contract_job["contract_description"],
                    contract_start=contract_job["flight_start"].strftime('%m/%d/%Y'),
                    contract_end=contract_job["flight_end"].strftime('%m/%d/%Y'),
                    charge_to=billing.get_charge_to(),
                    invoice_header=billing.get_invoice_header(),
                    customer_order_ref=f"{user_input['estimate']} {user_input['market']} {user_input['language']}",
                )
                if not contract_number:
                    print(f"[{quarter_label}] ✗ Failed to create contract")
                    all_success = False
                    continue
                print(f"[{quarter_label}] ✓ Created contract: {contract_number}")
            else:
                contract_number = contract_job["contract_number"]
                print(f"[{quarter_label}] Adding to existing contract: {contract_number}")
                lines_for_extend = [
                    {"end_date": ln["end_date"].strftime('%m/%d/%Y')}
                    for ln in etere_lines
                ]
                if not etere.extend_contract_end_date(contract_number, lines_for_extend):
                    print(f"[{quarter_label}] ✗ Failed to extend contract end date")
                    all_success = False
                    continue

            # ── Add contract lines ────────────────────────────────────
            for line_idx, line_spec in enumerate(etere_lines, 1):
                days     = line_spec["days"]
                time_str = line_spec["time"]
                days, _  = EtereClient.check_sunday_6_7a_rule(days, time_str)
                time_from, time_to = EtereClient.parse_time_range(time_str)
                start_date_str = line_spec["start_date"].strftime('%m/%d/%Y')
                end_date_str   = line_spec["end_date"].strftime('%m/%d/%Y')

                bns_flag = " [BNS]" if line_spec["is_bonus"] else ""
                print(f"\n  [LINE {line_idx}] {days} {time_str}{bns_flag}")
                print(f"    {start_date_str} – {end_date_str}")
                print(
                    f"    {line_spec['total_spots']}x total, "
                    f"{line_spec['spots_per_week']}/wk, "
                    f"{line_spec['max_daily_run']}x/day @ ${line_spec['rate']:.2f}  "
                    f"sep={line_spec['separation']}"
                )

                is_priority = (
                    not line_spec.get("is_bonus", False)
                    and line_spec["separation"][0] < LEXUS_SEPARATION[0]
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
                    is_priority=is_priority,
                )
                if not success:
                    print(f"    ✗ Failed")
                    all_success = False

            print(f"\n[{quarter_label}] ✓ Contract {contract_number} — {len(etere_lines)} line(s) complete")

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
