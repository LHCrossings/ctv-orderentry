"""
Backwrite transformer: Etere placement confirmation CSV → three-tab Excel

Tabs produced:
  Sales Confirmation  — one row per contract line (grouped)
  Run Sheet           — one row per spot that aired
  Sheet1              — monthly pivot (Gross Rate / Station Net)

CSV format expected (Etere export):
  Row 1:  column metadata labels (ignored)
  Row 2:  header values (agency, contract_code, date, description, address, client, city)
  Row 3:  blank
  Row 4:  data column headers (COD_CONTRATTO1, committente, ...)
  Row 5+: one row per spot
"""

import csv
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Dict, List, Optional, Tuple

import openpyxl
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

REVENUE_TYPES = [
    "Branded Content",
    "Direct Response Sales",
    "Internal Ad Sales",
    "Paid Programming",
    "Trade",
]

_LANG_KEYWORDS: List[Tuple[str, str]] = [
    ("cantonese",   "C"),
    ("mandarin",    "M"),
    ("chinese",     "M"),
    ("south asian", "SA"),
    ("hindi",       "SA"),
    ("punjabi",     "SA"),
    ("filipino",    "T"),
    ("tagalog",     "T"),
    ("vietnamese",  "V"),
    ("hmong",       "Hm"),
    ("korean",      "K"),
    ("japanese",    "J"),
]


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CsvHeader:
    agency:        str
    client:        str
    contract_code: str
    description:   str
    order_date:    str
    address:       str
    city:          str


@dataclass
class SpotRow:
    contract_code:   str
    client:          str
    line_id:         int
    priority:        int
    duration_s:      int
    flight_start:    date
    time_from:       str   # "HH:MM"
    time_to:         str   # "HH:MM"
    gross_rate:      float
    days_pattern:    str
    market:          str
    air_date:        date
    air_time:        str   # "HH:MM:SS"
    copy_code:       str
    row_description: str


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def detect_language(text: str) -> str:
    t = text.lower()
    for keyword, code in _LANG_KEYWORDS:
        if keyword in t:
            return code
    return "E"


def _strip_line_prefix(desc: str) -> str:
    """Remove '(Line N) ' prefix from rowdescription."""
    return re.sub(r'^\(Line \d+\)\s*', '', desc)


def _parse_date(s: str) -> date:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Cannot parse date: {s!r}")


def _hhmm_to_timedelta(s: str) -> timedelta:
    """Convert 'HH:MM' or 'HH:MM:SS' string to timedelta for Excel time serial."""
    try:
        parts = s.strip().split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        sec = int(parts[2]) if len(parts) > 2 else 0
        return timedelta(hours=h, minutes=m, seconds=sec)
    except (ValueError, IndexError):
        return timedelta(0)


def compute_broadcast_month(d: date) -> date:
    """Return the first of the broadcast month containing date d.

    Broadcast month = month of the Sunday that ends the week containing d.
    Broadcast weeks run Monday–Sunday.
    """
    days_until_sunday = (6 - d.weekday()) % 7
    next_sunday = d + timedelta(days=days_until_sunday)
    year = d.year
    if d.month == 12 and next_sunday.month == 1:
        year += 1
    return date(year, next_sunday.month, 1)


# ─────────────────────────────────────────────────────────────────────────────
# CSV PARSING
# ─────────────────────────────────────────────────────────────────────────────

def parse_csv(data: bytes) -> Tuple[CsvHeader, List[SpotRow]]:
    """Parse an Etere placement confirmation CSV.

    Returns (CsvHeader, list of SpotRow).
    Raises ValueError if the data header row cannot be found.
    """
    text = data.decode("utf-8-sig")
    lines = text.splitlines()

    # ── Header values from row 2 ──────────────────────────────────────────
    h_parts: List[str] = []
    if len(lines) >= 2:
        reader = csv.reader([lines[1]])
        h_parts = next(reader, [])

    csv_header = CsvHeader(
        agency        = h_parts[0].strip() if len(h_parts) > 0 else "",
        contract_code = h_parts[1].strip() if len(h_parts) > 1 else "",
        order_date    = h_parts[2].strip() if len(h_parts) > 2 else "",
        description   = h_parts[3].strip() if len(h_parts) > 3 else "",
        address       = h_parts[4].strip() if len(h_parts) > 4 else "",
        client        = h_parts[5].strip() if len(h_parts) > 5 else "",
        city          = h_parts[6].strip() if len(h_parts) > 6 else "",
    )

    # ── Find data column header row ───────────────────────────────────────
    data_start: Optional[int] = None
    for i, line in enumerate(lines):
        if "COD_CONTRATTO1" in line or "dateschedule" in line.lower():
            data_start = i
            break

    if data_start is None:
        return csv_header, []

    # ── Parse data rows ───────────────────────────────────────────────────
    data_text = "\n".join(lines[data_start:])
    reader = csv.DictReader(data_text.splitlines())

    spots: List[SpotRow] = []
    for row in reader:
        air_date_str = (row.get("dateschedule") or "").strip()
        if not air_date_str:
            continue

        try:
            air_date = _parse_date(air_date_str)
        except ValueError:
            continue

        datestart = row.get("DATESTART2", "").strip()
        try:
            flight_start = _parse_date(datestart) if datestart else air_date
        except ValueError:
            flight_start = air_date

        # Time range "13:00-14:00"
        timerange = row.get("timerange2", "").strip()
        time_parts = timerange.split("-", 1)
        time_from = time_parts[0].strip() if time_parts else ""
        time_to   = time_parts[1].strip() if len(time_parts) > 1 else ""

        try:
            gross_rate = float(row.get("IMPORTO2", 0) or 0)
        except (ValueError, TypeError):
            gross_rate = 0.0

        try:
            duration_s = int(row.get("duration3", 30) or 30)
        except (ValueError, TypeError):
            duration_s = 30

        try:
            line_id = int(row.get("id_contrattirighe", 0) or 0)
        except (ValueError, TypeError):
            line_id = 0

        try:
            priority = int(row.get("Textbox14", 1) or 1)
        except (ValueError, TypeError):
            priority = 1

        spots.append(SpotRow(
            contract_code   = row.get("COD_CONTRATTO1", "").strip(),
            client          = row.get("committente",    "").strip(),
            line_id         = line_id,
            priority        = priority,
            duration_s      = duration_s,
            flight_start    = flight_start,
            time_from       = time_from,
            time_to         = time_to,
            gross_rate      = gross_rate,
            days_pattern    = row.get("Textbox25", "").strip(),
            market          = row.get("nome2",       "").strip(),
            air_date        = air_date,
            air_time        = row.get("airtimep",    "").strip(),
            copy_code       = row.get("bookingcode2","").strip(),
            row_description = row.get("rowdescription","").strip(),
        ))

    return csv_header, spots


# ─────────────────────────────────────────────────────────────────────────────
# EXCEL GENERATION
# ─────────────────────────────────────────────────────────────────────────────

_RUN_SHEET_COLS = [
    "Bill Code", "Air Date", "End Date", "Day",
    "Time In", "Time out", "Length",
    "Media", "Program", "Lang.", "Format",
    "#", "Line", "Type", "Estimate",
    "Gross Rate", "Make Good", "Spot Value", "Month",
    "Broker Fees", "Priority", "Station Net",
    "Sales Person", "Revenue Type", "Billing Type",
    "Agency?", "Affidavit?", "Contract", "Market",
]


def generate_excel(
    header: CsvHeader,
    spots: List[SpotRow],
    user_inputs: dict,
) -> bytes:
    """Generate three-tab backwrite Excel and return raw bytes."""

    billing_type = user_inputs.get("billing_type", "Broadcast")
    agency_flag  = user_inputs.get("agency_flag",  "Agency")
    agency_fee   = float(user_inputs.get("agency_fee", 0.15) or 0)
    sales_person = user_inputs.get("sales_person", "")
    revenue_type = user_inputs.get("revenue_type", "Internal Ad Sales")
    affidavit    = user_inputs.get("affidavit",    "Y")
    estimate     = user_inputs.get("estimate",     "")
    contract     = user_inputs.get("contract",     "")

    is_agency = agency_flag == "Agency"
    bill_code = f"{header.agency}:{header.client}" if header.agency else header.client

    # ── Compute derived fields for every spot ─────────────────────────────
    run_rows: List[dict] = []
    for s in spots:
        if billing_type == "Broadcast":
            month_date = compute_broadcast_month(s.air_date)
        else:
            month_date = date(s.air_date.year, s.air_date.month, 1)
        month_dt = datetime(month_date.year, month_date.month, month_date.day)

        broker_fees = round(s.gross_rate * agency_fee, 2) if is_agency else 0.0
        station_net = round(s.gross_rate - broker_fees, 2)
        spot_type   = "BNS" if s.gross_rate == 0 else "COM"
        lang        = detect_language(s.row_description)

        run_rows.append({
            "Bill Code":    bill_code,
            "Air Date":     s.air_date,
            "End Date":     s.air_date,
            "Day":          s.air_date.strftime("%A"),
            "Time In":      _hhmm_to_timedelta(s.time_from),
            "Time out":     _hhmm_to_timedelta(s.time_to),
            "Length":       timedelta(seconds=s.duration_s),
            "Media":        s.copy_code,
            "Program":      s.air_time,
            "Lang.":        lang,
            "Format":       None,
            "#":            s.priority,
            "Line":         s.line_id,
            "Type":         spot_type,
            "Estimate":     estimate,
            "Gross Rate":   s.gross_rate,
            "Make Good":    None,
            "Spot Value":   s.gross_rate,
            "Month":        month_dt,
            "Broker Fees":  broker_fees,
            "Priority":     4,
            "Station Net":  station_net,
            "Sales Person": sales_person,
            "Revenue Type": revenue_type,
            "Billing Type": billing_type,
            "Agency?":      agency_flag,
            "Affidavit?":   affidavit,
            "Contract":     contract,
            "Market":       s.market,
        })

    # ── Build workbook ────────────────────────────────────────────────────
    wb = openpyxl.Workbook()

    ws_sc  = wb.active
    ws_sc.title = "Sales Confirmation"
    ws_run = wb.create_sheet("Run Sheet")
    ws_piv = wb.create_sheet("Sheet1")

    _build_run_sheet(ws_run, run_rows)
    _build_sales_confirmation(
        ws_sc, header, spots, run_rows,
        estimate=estimate, contract=contract,
        is_agency=is_agency, agency_fee=agency_fee,
        sales_person=sales_person, billing_type=billing_type,
    )
    _build_pivot(ws_piv, run_rows)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────────────────────
# RUN SHEET
# ─────────────────────────────────────────────────────────────────────────────

def _build_run_sheet(ws, run_rows: List[dict]) -> None:
    ws.append(_RUN_SHEET_COLS)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for rr in run_rows:
        ws.append([rr[c] for c in _RUN_SHEET_COLS])

    _TIME_COLS  = {"Time In", "Time out", "Length"}
    _DATE_COLS  = {"Air Date", "End Date", "Month"}
    _MONEY_COLS = {"Gross Rate", "Spot Value", "Broker Fees", "Station Net"}

    for row in ws.iter_rows(min_row=2, max_row=len(run_rows) + 1):
        for cell in row:
            col_name = _RUN_SHEET_COLS[cell.column - 1]
            if col_name in _TIME_COLS:
                cell.number_format = "HH:MM:SS"
            elif col_name in _DATE_COLS:
                cell.number_format = "m/d/yy"
            elif col_name in _MONEY_COLS:
                cell.number_format = "$#,##0.00"


# ─────────────────────────────────────────────────────────────────────────────
# SALES CONFIRMATION
# ─────────────────────────────────────────────────────────────────────────────

def _build_sales_confirmation(
    ws,
    header: CsvHeader,
    spots: List[SpotRow],
    run_rows: List[dict],
    *,
    estimate: str,
    contract: str,
    is_agency: bool,
    agency_fee: float,
    sales_person: str,
    billing_type: str,
) -> None:

    market = spots[0].market if spots else ""
    bold   = Font(bold=True)

    def label(row, col, text):
        ws.cell(row, col, text).font = bold

    # ── Title ─────────────────────────────────────────────────────────────
    ws.cell(1, 6, "SALES CONFIRMATION - CROSSINGS TV").font = Font(bold=True, size=14)

    # ── Contact block (left) / Order block (right) ────────────────────────
    label(2,  2, "Client");           ws.cell(2,  4, header.agency)
    label(2,  9, "Advertiser");       ws.cell(2, 12, header.client)
    label(3,  2, "Contact")
    label(3,  9, "Estimate");         ws.cell(3, 12, estimate)
    label(4,  2, "Address");          ws.cell(4,  4, header.address)
    label(4,  9, "Billing Type");     ws.cell(4, 12, billing_type)
    ws.cell(5,  4, header.city)
    label(5,  9, "Market");           ws.cell(5, 12, market)
    label(6,  2, "Phone")
    label(6,  9, "Date Order Written")
    date_cell = ws.cell(6, 12, datetime.today().date())
    date_cell.number_format = "m/d/yy"
    label(7,  2, "Fax")
    label(7,  9, "Contract Number");  ws.cell(7, 12, contract)
    label(8,  2, "Email")
    label(8,  9, "Revision");         ws.cell(8, 12, 0)
    label(9,  9, "Station Representative")
    ws.cell(9, 11, sales_person)

    # ── Line item header (row 10) ─────────────────────────────────────────
    _SC_HDR = [
        None, "Line Number", None, "Start Date", "End Date",
        "# spt per", "Per ____", "TP/Program/Lang Ordered",
        "# of days, wks, mos", "Spot type", None,
        "Total # of Units", None, "Length",
        "Gross Unit Rate", "Gross Line Total",
    ]
    for c, val in enumerate(_SC_HDR, 1):
        if val:
            ws.cell(10, c, val).font = bold

    # ── Line items ────────────────────────────────────────────────────────
    # Group spots by line_id, preserving first-appearance order
    groups: Dict[int, List[SpotRow]] = OrderedDict()
    for s in spots:
        groups.setdefault(s.line_id, []).append(s)

    current_row = 11
    total_gross = 0.0

    for line_num, (line_id, group) in enumerate(groups.items(), start=1):
        total_spots = len(group)
        start_date  = min(s.air_date for s in group)
        end_date    = max(s.air_date for s in group)
        gross_rate  = group[0].gross_rate
        duration_s  = group[0].duration_s
        desc        = _strip_line_prefix(group[0].row_description)
        spot_type   = "BNS" if gross_rate == 0 else "COM"
        gross_total = round(gross_rate * total_spots, 2)
        total_gross += gross_total

        ws.cell(current_row, 2, line_num)

        sd = ws.cell(current_row, 4, datetime(start_date.year, start_date.month, start_date.day))
        sd.number_format = "m/d/yy"
        ed = ws.cell(current_row, 5, datetime(end_date.year, end_date.month, end_date.day))
        ed.number_format = "m/d/yy"

        ws.cell(current_row, 6, total_spots)    # # spt per
        ws.cell(current_row, 7, "order")         # Per ____
        ws.cell(current_row, 8, desc)            # TP/Program/Lang Ordered
        ws.cell(current_row, 9, 1)               # # of days, wks, mos
        ws.cell(current_row, 10, spot_type)
        ws.cell(current_row, 12, total_spots)    # Total # of Units
        ws.cell(current_row, 14, f":{duration_s}")   # Length

        gr_cell = ws.cell(current_row, 15, gross_rate)
        gr_cell.number_format = "$#,##0.00"

        gt_cell = ws.cell(current_row, 16, gross_total)
        gt_cell.number_format = "$#,##0.00"

        current_row += 1

    # ── Totals ────────────────────────────────────────────────────────────
    current_row += 1  # blank row

    ws.cell(current_row, 15, "Grand Total").font = bold
    gt = ws.cell(current_row, 16, round(total_gross, 2))
    gt.font = bold;  gt.number_format = "$#,##0.00"
    current_row += 1

    if is_agency:
        disc     = round(total_gross * agency_fee, 2)
        net_tot  = round(total_gross - disc, 2)

        ws.cell(current_row, 15, f"Agency Discount ({int(agency_fee * 100)}%)")
        d_cell = ws.cell(current_row, 16, -disc)
        d_cell.number_format = "$#,##0.00"
        current_row += 1

        ws.cell(current_row, 15, "Net Total").font = bold
        n_cell = ws.cell(current_row, 16, net_tot)
        n_cell.font = bold;  n_cell.number_format = "$#,##0.00"
        current_row += 1

    current_row += 1  # blank

    ws.cell(current_row, 9, "Station Rep Signature")
    current_row += 2

    # ── Monthly breakdown ─────────────────────────────────────────────────
    monthly: Dict[str, dict] = OrderedDict()
    for rr in run_rows:
        m_dt = rr["Month"]
        key  = m_dt.strftime("%b") if isinstance(m_dt, datetime) else str(m_dt)
        if key not in monthly:
            monthly[key] = {"gross": 0.0, "net": 0.0}
        monthly[key]["gross"] += rr["Gross Rate"]
        monthly[key]["net"]   += rr["Station Net"]

    ws.cell(current_row, 2, "MONTHLY BREAKDOWN").font = bold
    current_row += 1

    label(current_row, 2, "Month")
    label(current_row, 4, "Gross")
    label(current_row, 5, "Net")
    current_row += 1

    total_m_gross = total_m_net = 0.0
    for month_name, totals in monthly.items():
        ws.cell(current_row, 2, month_name)
        g = ws.cell(current_row, 4, round(totals["gross"], 2))
        g.number_format = "$#,##0.00"
        n = ws.cell(current_row, 5, round(totals["net"], 2))
        n.number_format = "$#,##0.00"
        total_m_gross += totals["gross"]
        total_m_net   += totals["net"]
        current_row += 1

    ws.cell(current_row, 2, "Total").font = bold
    tg = ws.cell(current_row, 4, round(total_m_gross, 2))
    tg.font = bold;  tg.number_format = "$#,##0.00"
    tn = ws.cell(current_row, 5, round(total_m_net, 2))
    tn.font = bold;  tn.number_format = "$#,##0.00"


# ─────────────────────────────────────────────────────────────────────────────
# SHEET1 PIVOT
# ─────────────────────────────────────────────────────────────────────────────

def _build_pivot(ws, run_rows: List[dict]) -> None:
    bold = Font(bold=True)
    ws.append(["Row Labels", "Sum of Gross Rate", "Sum of Station Net"])
    for cell in ws[1]:
        cell.font = bold

    ws.append(["(blank)", None, None])

    monthly: Dict[datetime, dict] = OrderedDict()
    for rr in run_rows:
        m = rr["Month"]
        if not isinstance(m, datetime):
            continue
        key = datetime(m.year, m.month, 1)
        if key not in monthly:
            monthly[key] = {"gross": 0.0, "net": 0.0}
        monthly[key]["gross"] += rr["Gross Rate"]
        monthly[key]["net"]   += rr["Station Net"]

    total_gross = total_net = 0.0
    for month_dt, totals in sorted(monthly.items()):
        ws.append([month_dt, round(totals["gross"], 2), round(totals["net"], 2)])
        ws.cell(ws.max_row, 1).number_format = "m/d/yy"
        total_gross += totals["gross"]
        total_net   += totals["net"]

    ws.append(["Grand Total", round(total_gross, 2), round(total_net, 2)])
    for cell in ws[ws.max_row]:
        cell.font = bold
