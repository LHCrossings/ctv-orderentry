"""
EDI billing service — affidavit/post-log parsing, template store, and TVB EDI
.txt generation shared by the /edi/* routes.

Phase 1 of tasks/edi-billing-redesign.md: logic consolidated (moved, not
copied) out of src/web/routes/edi.py and src/web/routes/edi_export.py.
The two affidavit parsers are merged into parse_affidavit(); the two post-log
CSV parsers (spot-level + totals-row) are merged into parse_postlog_csv().

Generated EDI output must remain byte-identical for identical inputs —
locked by tests/unit/test_edi_golden.py. Do not change the _r* builders or
generate_edi() without sign-off (see the golden test docstring).
"""

from __future__ import annotations

import csv as csv_mod
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE = Path(__file__).resolve().parents[3]
TEMPLATE_DIR = _BASE / "data" / "edi_templates"
INCOMING_DIR = _BASE / "incoming" / "EDI"


# ---------------------------------------------------------------------------
# Affidavit PDF parsing
# ---------------------------------------------------------------------------

_SUBTOTAL_RE = re.compile(
    r'COPY LIST Subtotals\s+(\d+)\s+\$\s*([\d,]+\.?\d*)'
)
_ROW_RE = re.compile(
    r'^\d{1,2}/\d{1,2}/\d{2,4}'
    r'\s+\w+'
    r'(?:\s+\d+:\d+:\d+){4}'
    r'\s+\w+'
    r'\s+(\d+)'
    r'\s+\S+'
    r'\s+\S+'          # estimate number — may be alphanumeric (e.g. 13931-SF)
    r'\s+\$\s*([\d,]+\.?\d*)'
)


@dataclass
class AffidavitData:
    """Everything extractable from a CTV invoice affidavit PDF."""
    invoice_id: str | None = None
    contract_no: str | None = None
    advertiser: str = ""
    market: str = ""
    total_spots: int | None = None
    gross_amount: float | None = None
    rep_order_number: str = ""
    agency_ad_code: str = ""
    agency_prod_code: str = ""
    product_name: str = ""
    comment_top: str = ""
    comment_bottom: str = ""
    warnings: list[str] = field(default_factory=list)


def parse_affidavit(pdf_bytes: bytes, source: str = "") -> AffidavitData:
    """
    Parse a CTV invoice affidavit PDF. Never raises — failures are recorded
    in .warnings (and logged with `source` for context).

    Header fields (invoice id, contract number, advertiser, market) come from
    the affidavit page (page 2; page 1 for single-page PDFs). Totals come from
    the 'COPY LIST Subtotals' summary line with a sum-of-rows fallback.
    Comment-box fields (Davis Elen Order#/CLIENT/PRODUCT/ESTIMATE pattern and
    the HL COMMENTS/ATTN: section) are searched across all pages.
    """
    out = AffidavitData()
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page_texts = [p.extract_text() or "" for p in pdf.pages]

        full_text = "\n".join(page_texts)
        page2_text = page_texts[1] if len(page_texts) > 1 else (
            page_texts[0] if page_texts else ""
        )

        # --- affidavit header ---
        if m := re.search(r'Contract\s+Number\s+(\d+)', page2_text):
            out.contract_no = m.group(1)
        if m := re.search(r'Affidavit\s+([\w-]+)', page2_text):
            out.invoice_id = m.group(1)
        if m := re.search(r'Advertiser\s+(.+)$', page2_text, re.MULTILINE):
            out.advertiser = m.group(1).strip()
        if m := re.search(r'Market\s+(\S+)', page2_text):
            out.market = m.group(1).strip()

        # --- totals: last subtotal line wins; sum of rows as fallback ---
        total_spots = None
        gross_amount = None
        row_spots = 0
        row_gross = 0.0
        for text in page_texts[1:]:
            if sub_m := _SUBTOTAL_RE.search(text):
                total_spots = int(sub_m.group(1))
                gross_amount = float(sub_m.group(2).replace(',', ''))
            for line in text.splitlines():
                if row_m := _ROW_RE.match(line.strip()):
                    cnt = int(row_m.group(1))
                    rate = float(row_m.group(2).replace(',', ''))
                    row_spots += cnt
                    if rate > 0:
                        row_gross += cnt * rate
        out.total_spots = total_spots if total_spots is not None else row_spots
        out.gross_amount = round(
            gross_amount if gross_amount is not None else row_gross, 2
        )

        # --- comment-box fields (may span pages) ---
        if m := re.search(r'Order\s*#:\s*(\d+)', full_text):
            out.rep_order_number = m.group(1).strip()
        if m := re.search(r'CLIENT\s+(\w+)', full_text):
            out.agency_ad_code = m.group(1).strip()
        if m := re.search(r'PRODUCT\s+(\w+)\s+(.+?)(?:\s+http|\s+CPE\b|\n|$)', full_text):
            out.agency_prod_code = m.group(1).strip()
            out.product_name = m.group(2).strip().replace("-", " ").title()
        if m := re.search(r'ESTIMATE\s+\d+\s+(\S+)', full_text):
            out.comment_top = m.group(1).strip()
        # HL-style: COMMENTS section — line after "ATTN:" before "Phone:" or URL
        if m := re.search(r'COMMENTS\s+ATTN:.*?\n(.+?)\s+(?:Phone:|http)', full_text, re.DOTALL):
            out.comment_bottom = m.group(1).strip()

    except Exception as e:
        logger.warning("Affidavit parse failed for %s: %s", source or "<upload>", e)
        out.warnings.append(f"Affidavit parse failed: {e}")
    return out


# ---------------------------------------------------------------------------
# Post-log CSV parsing
# ---------------------------------------------------------------------------

@dataclass
class PostLogData:
    """Spot-level parse of an Etere post-log CSV plus its totals-row cross-check."""
    spots: list[dict] = field(default_factory=list)
    spot_count: int = 0
    gross_cents: int = 0
    bcast_start: str = ""
    bcast_end: str = ""
    estimate_code: str = ""
    advertiser: str = ""
    market: str = ""
    contract_no: str | None = None        # from *_NNNN_postlog filename
    totals_row_gross: float | None = None  # report's own totals line (dollars)
    totals_row_spots: int | None = None
    warnings: list[str] = field(default_factory=list)


def parse_postlog_csv(csv_bytes: bytes, filename: str = "") -> PostLogData:
    """
    Parse an Etere post-log CSV: individual spots (for EDI R51 lines and
    derived totals) plus the report's own last-line totals row as a
    cross-check. A disagreement between derived and reported totals is
    recorded as a warning, never raised.
    """
    out = PostLogData()

    if filename:
        if fn_match := re.search(r'_(\d+)_postlog', filename, re.IGNORECASE):
            out.contract_no = fn_match.group(1)

    text = csv_bytes.decode("utf-8-sig", errors="replace")
    rows = list(csv_mod.reader(io.StringIO(text)))

    # Structure: row0=labels, row1=values, row2=blank, row3=data-headers, row4+=data
    meta = rows[1] if len(rows) > 1 else []
    est_desc = meta[3].strip() if len(meta) > 3 else ""

    m = re.search(r'\bEst\.?\s+(\S+)', est_desc, re.IGNORECASE)
    if m:
        out.estimate_code = m.group(1).rstrip(",.:")
    else:
        # Fallback: trailing number in meta[3], then meta[1]
        for src in [est_desc, meta[1].strip() if len(meta) > 1 else ""]:
            if fb := re.search(r'(\d+)\s*$', src):
                out.estimate_code = fb.group(1)
                break

    hdr = rows[3] if len(rows) > 3 else []
    col = {name.strip(): i for i, name in enumerate(hdr)}

    for row in rows[4:]:
        if not row or all(c.strip() == "" for c in row):
            continue

        def g(name: str) -> str:
            i = col.get(name)
            return row[i].strip() if i is not None and i < len(row) else ""

        date_str    = g("dateschedule")
        airtime_str = g("airtimep")
        duration    = g("duration3")
        copy_id     = g("bookingcode2")
        rate_str    = g("IMPORTO2")
        market_raw  = g("nome2")

        if not date_str or not airtime_str:
            continue

        try:
            dt = datetime.strptime(date_str, "%m/%d/%Y")
            run_date = dt.strftime("%y%m%d")
        except ValueError:
            continue

        parts = airtime_str.split(":")
        time_hhmm = parts[0].zfill(2) + (parts[1] if len(parts) > 1 else "00")

        try:
            rate_cents = int(round(float(rate_str) * 100))
        except (ValueError, TypeError):
            rate_cents = 0

        try:
            dur_secs = int(float(duration))
        except (ValueError, TypeError):
            dur_secs = 0

        out.spots.append({
            "run_date":   run_date,
            "time_hhmm":  time_hhmm,
            "duration":   dur_secs,
            "copy_id":    copy_id,
            "rate_cents": rate_cents,
            "market":     market_raw,
        })

    dates = sorted(s["run_date"] for s in out.spots if s["run_date"])
    out.spot_count  = len(out.spots)
    out.gross_cents = sum(s["rate_cents"] for s in out.spots)
    out.bcast_start = dates[0] if dates else ""
    out.bcast_end   = dates[-1] if dates else ""
    out.advertiser  = meta[5].strip() if len(meta) > 5 else ""
    out.market      = out.spots[0]["market"] if out.spots else ""

    # --- totals row (last non-empty line): col0=gross dollars, col1=spot count ---
    last_line = None
    for line in reversed(text.splitlines()):
        if line.strip():
            last_line = line
            break

    if not last_line:
        out.warnings.append("CSV appears empty")
        return out

    parts = next(csv_mod.reader([last_line]), [])

    def _clean_num(s: str) -> str:
        return s.replace(',', '').replace('$', '').strip()

    try:
        out.totals_row_gross = round(float(_clean_num(parts[0])), 2)
    except (ValueError, IndexError):
        out.totals_row_gross = None

    try:
        out.totals_row_spots = int(_clean_num(parts[1]))
    except (ValueError, IndexError):
        out.totals_row_spots = None

    if out.totals_row_spots is not None and out.totals_row_spots != out.spot_count:
        out.warnings.append(
            f"CSV totals row says {out.totals_row_spots} spots "
            f"but {out.spot_count} spot rows parsed"
        )
    if (out.totals_row_gross is not None
            and abs(out.gross_cents / 100 - out.totals_row_gross) > 0.005):
        out.warnings.append(
            f"CSV totals row says ${out.totals_row_gross:,.2f} "
            f"but spot rows sum to ${out.gross_cents / 100:,.2f}"
        )

    return out


# ---------------------------------------------------------------------------
# EDI record builders — moved verbatim from edi_export.py.
# Byte-identical output is locked by tests/unit/test_edi_golden.py.
# ---------------------------------------------------------------------------

def _pad(lst: list, n: int) -> list[str]:
    out = [str(x) for x in lst]
    while len(out) < n:
        out.append("")
    return out[:n]


def _r21(t: dict) -> str:
    aa = _pad(t.get("agency_address", []), 4)
    return ";".join(["21", t.get("edi_code",""), t.get("agency_name",""), *aa]) + ";"


def _r22(t: dict) -> str:
    return f"22;{t.get('call_letters','')};TV;TV;;;;;;;;;"


def _r23(t: dict) -> str:
    pa = _pad(t.get("payee_address", []), 4)
    return ";".join(["23", t.get("payee_name",""), *pa]) + ";"


def _r31(t: dict, inv: dict) -> str:
    f = _pad([], 42)
    f[0]  = "31"
    f[1]  = t.get("representative", "")
    f[2]  = t.get("salesperson", "")
    f[3]  = inv.get("advertiser_name", "") or t.get("advertiser_name", "")
    f[4]  = inv.get("product_name", "")    or t.get("product_name", "")
    f[5]  = inv.get("invoice_date", "")
    f[7]  = inv.get("estimate_code", "")
    f[8]  = inv.get("invoice_number", "")
    f[9]  = inv.get("broadcast_month", "")
    f[10] = inv.get("bcast_start", "")
    f[11] = inv.get("bcast_end", "")
    f[12] = inv.get("bcast_start", "")
    f[13] = inv.get("bcast_end", "")
    f[14] = inv.get("bcast_start", "")
    f[15] = inv.get("bcast_end", "")
    f[18] = "Y"
    f[21] = inv.get("rep_order_number", "")
    f[22] = inv.get("order_number", "")
    f[24] = inv.get("agency_ad_code", "")  or t.get("agency_ad_code", "")
    f[26] = inv.get("agency_prod_code", "") or t.get("agency_prod_code", "")
    return ";".join(f) + ";"


def _r33_lines(inv: dict) -> list[str]:
    out = []
    for key in ("comment_bottom", "comment_bottom_2", "comment_bottom_3", "comment_bottom_4"):
        val = inv.get(key, "").strip()
        if val:
            out.append(f"33;{val};")
    return out


def _r51(spot: dict) -> str:
    f = _pad([], 28)
    f[0] = "51"
    f[1] = "Y"
    f[2] = spot["run_date"]
    f[4] = spot["time_hhmm"]
    f[5] = str(spot["duration"])
    f[6] = spot["copy_id"]
    f[7] = str(spot["rate_cents"])
    return ";".join(f) + ";"


def _r34(t: dict, gross: int, spot_count: int) -> str:
    pct  = float(t.get("commission_pct", 15.0))
    comm = int(round(gross * pct / 100))
    net  = gross - comm
    f = _pad([], 16)
    f[0] = "34"
    f[2] = str(gross)
    f[3] = str(comm)
    f[4] = str(net)
    f[12] = str(spot_count)
    return ";".join(f) + ";"


def generate_edi(template: dict, inv: dict, spots: list[dict]) -> str:
    gross = inv.get("gross_cents", sum(s["rate_cents"] for s in spots))
    count = inv.get("spot_count", len(spots))
    lines = [
        _r21(template),
        _r22(template),
        _r23(template),
        _r31(template, inv),
    ]
    comment = inv.get("comment_top", "").strip()
    if comment:
        lines.append(f"32;{comment};")
    for spot in spots:
        lines.append(_r51(spot))
    lines.extend(_r33_lines(inv))
    lines.append(_r34(template, gross, count))
    lines.append(f"12;1;{gross};")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Template store
# ---------------------------------------------------------------------------

def slug(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


def all_templates() -> list[dict]:
    out = []
    for p in sorted(TEMPLATE_DIR.glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except Exception as e:
            logger.warning("Skipping unreadable EDI template %s: %s", p.name, e)
    return out


def get_template(name: str) -> dict | None:
    p = TEMPLATE_DIR / f"{slug(name)}.json"
    return json.loads(p.read_text()) if p.exists() else None


def suggest_template(filename: str, templates: list[dict],
                     advertiser: str = "", market: str = "") -> str:
    """
    Legacy string-based template suggestion. Known-flawed (see
    tasks/edi-billing-redesign.md §1) — replaced by the customer-ID matcher
    in Phase 2; this survives only as the fuzzy fallback.
    """
    fn  = filename.lower()
    adv = advertiser.lower()
    mkt = market.upper()

    def _words(s: str) -> list[str]:
        return re.findall(r'[a-z]{3,}', s.lower())

    # Pass 1: exact advertiser_match + optional market_match (user-configured)
    for t in templates:
        am = t.get("advertiser_match", "").strip()
        mm = t.get("market_match", "").strip().upper()
        if not am:
            continue
        if am.lower() == adv:
            if mm and mm != mkt:
                continue
            return t.get("name", "")

    # Pass 2: fuzzy fallback (agency name in filename)
    for t in templates:
        if any(w in fn for w in _words(t.get("agency_name", ""))):
            return t.get("name", "")

    return templates[0].get("name", "") if templates else ""


# ---------------------------------------------------------------------------
# Invoice metadata from filename (MMYY-NNN prefix convention)
# ---------------------------------------------------------------------------

def invoice_info(filename: str) -> dict:
    stem = Path(filename).stem
    inv_m = re.match(r'^(\d{4}-\d{3})', stem)
    invoice_number = inv_m.group(1) if inv_m else ""
    bcast_month    = invoice_number[:4] if len(invoice_number) >= 4 else ""

    # Derive last day of billing month for invoice_date
    invoice_date = ""
    if len(bcast_month) == 4:
        import calendar
        yy, mm = int(bcast_month[:2]), int(bcast_month[2:])
        full_year = 2000 + yy
        last_day = calendar.monthrange(full_year, mm)[1]
        invoice_date = f"{bcast_month}{last_day:02d}"

    # Etere contract number from _NNNN_postlog
    cont_m = re.search(r'_(\d+)_postlog', stem)
    order_number = cont_m.group(1) if cont_m else ""

    return {
        "invoice_number":  invoice_number,
        "broadcast_month": bcast_month,
        "invoice_date":    invoice_date,
        "order_number":    order_number,
    }
