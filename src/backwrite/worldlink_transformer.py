"""
WorldLink backwrite transformer.
Generates a 2-tab Excel (Sales Confirmation + Monthly Lines and Broker Fees)
from a parsed WorldLink IO PDF.
"""

import io
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict

import openpyxl
from openpyxl.styles import PatternFill

_src = Path(__file__).parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from backwrite.transformer import compute_broadcast_month

YELLOW_FILL = PatternFill("solid", fgColor="FFFFFF00")
AGENCY_FEE  = 0.15

_MONTH_NAMES = {
    1: "January",  2: "February",  3: "March",    4: "April",
    5: "May",      6: "June",      7: "July",      8: "August",
    9: "September",10: "October", 11: "November", 12: "December",
}

_MLBF_HEADERS = [
    "Bill Code", "Start Date", "End Date", "Day", "Time In", "Time out",
    "Length", "Media", "Program", "Lang.", "Format", "#", "Line", "Type",
    "Estimate", "Gross Rate", "Make Good", "Spot Value", "Month",
    "Broker Fees", "Priority", "Station Net", "Sales Person", "Revenue Type",
    "Billing Type", "Agency?", "Affidavit?", "Contract", "Market",
]


# ──────────────────────────── public entry point ──────────────────────────────

def generate_worldlink_excel(io_data: dict, user_inputs: dict) -> bytes:
    """
    io_data    : output of worldlink_parser.parse_worldlink_pdf
    user_inputs: {"contract_number": str, "revision": int|str}
    Returns xlsx bytes.
    """
    wb = openpyxl.Workbook()

    ws_sc = wb.active
    ws_sc.title = "Sales Confirmation"
    _build_sc_tab(ws_sc, io_data, user_inputs)

    ws_mlbf = wb.create_sheet("Monthly Lines and Broker Fees")
    _build_mlbf_tab(ws_mlbf, io_data, user_inputs)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ──────────────────────────── helpers ────────────────────────────────────────

def _fmt_time_short(t24: str) -> str:
    """HH:MM → short 12-hour label: '6a', '9a', '5p', '10p', '12a', '12p'."""
    if not t24:
        return ""
    if t24 == "23:59":
        return "12a"
    try:
        h, m = map(int, t24.split(":"))
    except ValueError:
        return t24
    if h == 0 and m == 0:
        return "12a"
    if h == 12 and m == 0:
        return "12p"
    period = "a" if h < 12 else "p"
    h12 = h if h < 12 else (h - 12 if h > 12 else 12)
    return f"{h12}:{m:02d}{period}" if m else f"{h12}{period}"


def _fmt_program(line: dict) -> str:
    """Build 'M-Su 6a-9a' style string from days + time fields."""
    days   = line.get("days_of_week", "M-Su")
    from_t = line.get("from_time", "06:00")
    to_t   = line.get("to_time",   "23:59")
    return f"{days} {_fmt_time_short(from_t)}-{_fmt_time_short(to_t)}"


def _count_weeks(line: dict) -> int:
    """Return week count — prefer 'weeks' field from parser, then derive."""
    if line.get("weeks"):
        return int(line["weeks"])
    spw = line.get("spots", 0) or 0
    tot = line.get("total_spots", 0) or 0
    if spw:
        return max(1, round(tot / spw))
    try:
        start = _parse_date_str(line.get("start_date", ""))
        end   = _parse_date_str(line.get("end_date",   ""))
        if start and end:
            return max(1, round((end - start).days / 7))
    except Exception:
        pass
    return 1


def _clean_org_name(name: str) -> str:
    """Strip trailing corporate suffixes: ', Inc.', ', LLC', etc."""
    return re.sub(
        r",?\s*(Inc\.?|LLC\.?|Ltd\.?|Corp\.?|Co\.?)$", "", name, flags=re.I
    ).strip()


def _make_bill_code(agency: str, advertiser: str) -> str:
    """'WorldLink:CleanAgency AdvertiserWord'"""
    clean = _clean_org_name(agency)
    adv   = (_clean_org_name(advertiser).split()[0] if advertiser else "").rstrip(",")
    return f"WorldLink:{clean} {adv}".strip()


def _parse_date_str(s: str):
    """Parse M/D/YYYY or M/D/YY to date, or return None."""
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _compute_monthly_revenue(lines: list) -> Dict[date, float]:
    """
    Iterate each IO line week-by-week; bucket (spots_per_week × rate) into
    broadcast months.  Zero-rate lines (bonus/free) are skipped.
    Returns {broadcast_month_first_of_month: gross_amount}.
    """
    monthly: Dict[date, float] = defaultdict(float)
    for line in lines:
        rate = float(line.get("rate", 0) or 0)
        if rate == 0:
            continue
        spw   = line.get("spots", 0) or 0
        start = _parse_date_str(line.get("start_date", ""))
        end   = _parse_date_str(line.get("end_date",   ""))
        if not start or not end or spw == 0:
            continue
        week_start = start
        while week_start <= end:
            bm = compute_broadcast_month(week_start)
            monthly[bm] += spw * rate
            week_start += timedelta(days=7)
    return {k: round(v, 2) for k, v in sorted(monthly.items())}


# ──────────────────────────── Sales Confirmation tab ─────────────────────────

def _build_sc_tab(ws, io_data: dict, user_inputs: dict) -> None:
    agency        = io_data.get("agency", "")
    advertiser    = io_data.get("advertiser", "")
    tracking      = str(io_data.get("tracking_number", "") or "")
    buyer         = io_data.get("buyer", "")
    order_comment = io_data.get("order_comment", "") or ""
    lines         = io_data.get("lines", [])

    agency_street = io_data.get("agency_street", "")
    agency_city   = io_data.get("agency_city", "")
    agency_state  = io_data.get("agency_state", "")
    agency_zip    = io_data.get("agency_zip", "")

    contract_no = str(user_inputs.get("contract_number", "") or "")
    revision    = user_inputs.get("revision", 0)
    is_revision = int(revision) > 0
    today_str   = date.today().strftime("%m/%d/%Y")

    def w(r, c, v):
        ws.cell(row=r, column=c).value = v

    # ── Header block ──────────────────────────────────────────────────────────
    w(1,  6,  "SALES CONFIRMATION - CROSSINGS TV")
    w(3,  2,  "Client")
    w(3,  4,  agency)
    w(3,  9,  "Advertiser")
    w(3,  12, advertiser)
    w(4,  2,  "Contact")
    w(4,  4,  buyer)
    w(4,  9,  "Estimate")
    w(4,  12, tracking)
    w(5,  2,  "Address")
    w(5,  4,  agency_street)
    w(5,  9,  "Billing Type")
    w(5,  12, "Broadcast")
    w(6,  4,  agency_city)
    w(6,  6,  agency_state)
    w(6,  7,  agency_zip)
    w(6,  9,  "Market")
    w(6,  12, "National")
    w(8,  2,  "Phone")
    w(8,  9,  "Date Order Written")
    w(8,  12, today_str)
    w(9,  2,  "Fax")
    w(9,  9,  "Contract Number")
    w(9,  12, contract_no)
    w(10, 2,  "Email")
    w(10, 9,  "Revision")
    w(10, 12, str(revision))
    w(11, 9,  "Station Representative")
    w(11, 11, "House (Worldlink)")

    # ── Line-items header ─────────────────────────────────────────────────────
    HDR = 13
    w(HDR, 2,  "Line Number")
    w(HDR, 4,  "Start Date")
    w(HDR, 5,  "End Date")
    w(HDR, 6,  "# spt per")
    w(HDR, 7,  "Per ____")
    w(HDR, 8,  "TP/Program/Lang Ordered")
    w(HDR, 9,  "# of days, wks, mos")
    w(HDR, 10, "Spot type")
    w(HDR, 12, "Total # of Units")
    w(HDR, 14, "Length")
    w(HDR, 15, "Gross Unit Rate")
    w(HDR, 16, "Gross Line Total")

    # ── Line-item rows ────────────────────────────────────────────────────────
    DATA_START = 14
    for i, line in enumerate(lines):
        r      = DATA_START + i
        action = line.get("action", "ADD")
        is_cancel = action == "CANCEL"

        # CANCEL: zero out spots (keep everything else for reference)
        spots  = 0 if is_cancel else (line.get("spots", 0) or 0)
        rate   = 0.0 if is_cancel else float(line.get("rate", 0) or 0)
        weeks  = _count_weeks(line)

        w(r, 2,  line.get("line_number", i + 1))
        w(r, 4,  _parse_date_str(line.get("start_date", "")))
        w(r, 5,  _parse_date_str(line.get("end_date",   "")))
        w(r, 6,  spots)
        w(r, 7,  "week")
        w(r, 8,  _fmt_program(line))
        w(r, 9,  weeks)
        w(r, 10, "COM")
        w(r, 12, f"=F{r}*I{r}")
        w(r, 14, f":{line.get('duration', '30')}")
        w(r, 15, rate)
        w(r, 16, f"=L{r}*O{r}")

        # Yellow fill for added/changed lines in a revision
        if is_revision and action in ("ADD", "CHANGE"):
            for col in range(1, 23):
                ws.cell(row=r, column=col).fill = YELLOW_FILL

    last_data = DATA_START + len(lines) - 1
    sum_row   = last_data + 1
    disc_row  = sum_row + 1
    net_row   = disc_row + 1

    # ── Summary block ─────────────────────────────────────────────────────────
    w(sum_row,  9,  "Gross Amount")
    w(sum_row,  12, f"=SUM(L{DATA_START}:L{last_data})")
    w(sum_row,  14, "spots")
    w(sum_row,  16, f"=SUM(P{DATA_START}:P{last_data})")

    w(disc_row, 2,  "Additional Notes")
    w(disc_row, 9,  "Agency Discount")
    w(disc_row, 12, AGENCY_FEE)
    w(disc_row, 16, f"=-1*(L{disc_row}*P{sum_row})")

    w(net_row,  2,  order_comment)
    w(net_row,  9,  "Net Amount of Contract")
    w(net_row,  16, f"=SUM(P{sum_row}:P{disc_row})")

    sig1 = net_row + 2
    sig2 = sig1 + 2
    w(sig1, 9, "Client Signature")
    w(sig2, 9, "Station Rep Signature")

    # ── Monthly Breakdown section ─────────────────────────────────────────────
    mbr_title = sig2 + 4
    w(mbr_title, 2, "MONTHLY BREAKDOWN")

    mbr_hdr = mbr_title + 2
    w(mbr_hdr, 2, "Month")
    w(mbr_hdr, 4, "Gross")
    w(mbr_hdr, 5, "Net")
    w(mbr_hdr, 6, "Broker Fee")

    monthly_rev   = _compute_monthly_revenue(lines)
    sorted_months = sorted(monthly_rev.keys())
    mbr_first     = mbr_hdr + 1

    for j, bm in enumerate(sorted_months):
        r = mbr_first + j
        w(r, 2, _MONTH_NAMES[bm.month])
        w(r, 4, round(monthly_rev[bm], 2))
        w(r, 5, f"=D{r}*0.85")
        w(r, 6, f"=E{r}*0.1*-1")

    if sorted_months:
        mbr_last  = mbr_first + len(sorted_months) - 1
        total_row = mbr_last + 1
        w(total_row, 2, "Total")
        w(total_row, 4, f"=SUM(D{mbr_first}:D{mbr_last})")
        w(total_row, 5, f"=SUM(E{mbr_first}:E{mbr_last})")


# ──────────────────────── Monthly Lines and Broker Fees tab ──────────────────

def _build_mlbf_tab(ws, io_data: dict, user_inputs: dict) -> None:
    agency      = io_data.get("agency", "")
    advertiser  = io_data.get("advertiser", "")
    tracking    = str(io_data.get("tracking_number", "") or "")
    lines       = io_data.get("lines", [])
    contract_no = user_inputs.get("contract_number", "")
    # contract stored as int if purely numeric (matches template behaviour)
    try:
        contract_val = int(contract_no) if str(contract_no).isdigit() else contract_no
    except (ValueError, TypeError):
        contract_val = contract_no

    # Tracking number as int for Estimate column when possible
    try:
        tracking_val = int(tracking) if tracking.isdigit() else tracking
    except (ValueError, TypeError):
        tracking_val = tracking

    bill_code     = _make_bill_code(agency, advertiser)
    monthly_rev   = _compute_monthly_revenue(lines)
    sorted_months = sorted(monthly_rev.keys())

    def w(r, c, v):
        ws.cell(row=r, column=c).value = v

    # ── Column header row ─────────────────────────────────────────────────────
    for col, hdr in enumerate(_MLBF_HEADERS, 1):
        w(1, col, hdr)

    # ── Billing group ─────────────────────────────────────────────────────────
    w(2, 1, "These are the lines you will paste in to show the monthly revenues")

    BILL_START = 3
    for i, bm in enumerate(sorted_months):
        r            = BILL_START + i
        gross        = round(monthly_rev[bm], 2)
        billing_date = date(bm.year, bm.month, 20)

        w(r,  1, bill_code)
        w(r,  2, billing_date)
        w(r,  3, f"=B{r}")
        w(r,  4, f'=TEXT(B{r},"dddd")')
        w(r,  5, 0)                                      # Time In
        w(r,  6, 0)                                      # Time out
        w(r,  7, 0)                                      # Length
        w(r,  8, f"{tracking} Monthly Charges")          # Media
        w(r,  9, "BILLING LINE")                         # Program
        # col 10 (Lang.) — blank
        w(r, 11, "NX")                                   # Format
        w(r, 12, 1)                                      # #
        # col 13 (Line) — blank
        w(r, 14, "COM")                                  # Type
        w(r, 15, tracking_val)                           # Estimate
        w(r, 16, gross)                                  # Gross Rate  ← P
        # col 17 (Make Good) — blank
        w(r, 18, f"=P{r}")                               # Spot Value  ← R
        w(r, 19, date(bm.year, bm.month, 1))             # Month       ← S
        w(r, 20, f"=P{r}*0.15")                          # Broker Fees (agency fee) ← T
        w(r, 21, 4)                                      # Priority
        w(r, 22, f"=P{r}-T{r}")                          # Station Net ← V
        w(r, 23, "House")                                # Sales Person
        w(r, 24, "Direct Response Sales")                # Revenue Type
        w(r, 25, "Broadcast")                            # Billing Type
        w(r, 26, "Agency")                               # Agency?
        w(r, 27, "Y")                                    # Affidavit?
        w(r, 28, contract_val)                           # Contract    ← AB
        w(r, 29, "Admin")                                # Market      ← AC

    # ── Broker fee group ──────────────────────────────────────────────────────
    fee_instruction_row = BILL_START + len(sorted_months) + 1
    w(fee_instruction_row, 1,
      "These are the lines you will paste in to show the monthly broker fees")

    FEE_START = fee_instruction_row + 1
    for i, bm in enumerate(sorted_months):
        r            = FEE_START + i
        gross        = round(monthly_rev[bm], 2)
        broker_fee   = round(-gross * 0.85 * 0.10, 2)
        billing_date = date(bm.year, bm.month, 20)

        w(r,  1, "WorldLink Broker Fees (DO NOT INVOICE)")
        w(r,  2, billing_date)
        w(r,  3, f"=B{r}")
        w(r,  4, f'=TEXT(B{r},"dddd")')
        w(r,  5, 0)
        w(r,  6, 0)
        w(r,  7, 0)
        w(r,  8, f"{tracking} Broker Fees")              # Media
        w(r,  9, "BILLING LINE")
        w(r, 11, "NX")
        w(r, 12, 1)
        w(r, 14, "COM")
        w(r, 15, tracking_val)
        w(r, 16, broker_fee)                             # P: negative gross
        w(r, 18, f"=P{r}")
        w(r, 19, date(bm.year, bm.month, 1))
        w(r, 20, 0)                                      # T: hard-coded 0 for fee rows
        w(r, 21, 4)
        w(r, 22, f"=P{r}-T{r}")
        w(r, 23, "House")
        w(r, 24, "Direct Response Sales")
        w(r, 25, "Broadcast")
        w(r, 26, "Agency")
        w(r, 27, "Y")
        w(r, 28, contract_val)
        w(r, 29, "Admin")
