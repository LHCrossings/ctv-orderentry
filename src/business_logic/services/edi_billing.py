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
from datetime import date, datetime, timedelta
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
        # [ \t] not \s: the Market value column can be blank, and \s would
        # walk across the newline and grab the next line's label ("Fax")
        if m := re.search(r'Market[ \t]+(\S+)', page2_text):
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


# ---------------------------------------------------------------------------
# Template matching (Phase 2 — tasks/edi-billing-redesign.md §2)
# ---------------------------------------------------------------------------

MARKET_CODES = {"NYC", "CMP", "HOU", "SFO", "SEA", "LAX", "CVC", "WDC", "MMT", "DAL"}

# Post-log CSVs (nome2) sometimes carry full market names where affidavits
# carry codes — normalize both to the code form used in template market_match.
MARKET_NAME_TO_CODE = {
    "NEW YORK": "NYC", "NEW YORK CITY": "NYC",
    "CHICAGO": "CMP", "MINNEAPOLIS": "CMP",
    "HOUSTON": "HOU",
    "SAN FRANCISCO": "SFO",
    "SEATTLE": "SEA",
    "LOS ANGELES": "LAX",
    "CENTRAL VALLEY": "CVC", "SACRAMENTO": "CVC",
    "WASHINGTON DC": "WDC", "WASHINGTON": "WDC",
    "DALLAS": "DAL",
}


def normalize_market(value: str) -> str:
    v = (value or "").strip().upper()
    return MARKET_NAME_TO_CODE.get(v, v)


def resolve_market(csv_market: str, pdf_market: str) -> str:
    """
    Market used for template tie-breaks and comment_top_by_market.
    The CSV's spot-level market (where the spots actually aired) outranks the
    affidavit header, which can be blank or wrong (contract 2590's affidavit
    said SEA while every spot aired CVC — June 2026 batch).
    """
    csv_m, pdf_m = normalize_market(csv_market), normalize_market(pdf_market)
    if csv_m in MARKET_CODES:
        return csv_m
    if pdf_m in MARKET_CODES:
        return pdf_m
    return csv_m or pdf_m


# Words too generic to identify an agency on their own. "media" in a filename
# used to pull the Ocean Media BetMGM template for anything (confirmed
# misdetection, spec §1).
GENERIC_AGENCY_WORDS = {
    "media", "group", "partners", "agency", "advertising", "solutions",
    "communications", "the", "and", "llc", "inc", "dba",
}


@dataclass
class TemplateMatch:
    name: str = ""                 # matched template name; "" = no match / needs pick
    confidence: str = "none"       # 'customer-id' | 'fuzzy' | 'ambiguous' | 'none'
    candidates: list[str] = field(default_factory=list)  # for 'ambiguous'
    detail: str = ""               # human-readable reason for the UI


def lookup_contract_customers(contract_nos: list[str | int]) -> tuple[dict[int, dict], str | None]:
    """
    One MSSQL query: Etere contract IDs → authoritative customer/agency.
    The affidavit's "Contract Number" is CONTRATTITESTATA.ID_CONTRATTITESTATA
    (verified live 2026-07-09).

    Returns ({contract_id: {customer_id, customer_name, agency_id, agency_name}},
    error) — on DB failure the dict is empty and error holds the message so
    callers can fall back to fuzzy matching with a visible warning.
    """
    nos = sorted({int(n) for n in contract_nos if str(n).strip().isdigit()})
    if not nos:
        return {}, None
    try:
        import sys
        for p in [str(_BASE), str(_BASE / "browser_automation")]:
            if p not in sys.path:
                sys.path.insert(0, p)
        from browser_automation.etere_direct_client import connect

        conn = connect()
        try:
            cur = conn.cursor()
            ph = ",".join("%s" for _ in nos)
            cur.execute(f"""
                SELECT ct.ID_CONTRATTITESTATA, ct.COMMITTENTE, cust.RAG_SOCIAL,
                       ct.AGENZIA, ag.RAG_SOCIAL
                FROM CONTRATTITESTATA ct
                LEFT JOIN ANAGRAF cust ON cust.ID_ANAGRAF = ct.COMMITTENTE
                LEFT JOIN ANAGRAF ag   ON ag.ID_ANAGRAF = ct.AGENZIA
                WHERE ct.ID_CONTRATTITESTATA IN ({ph})
            """, tuple(nos))
            out = {}
            for cid, cust_id, cust_name, ag_id, ag_name in cur.fetchall():
                out[int(cid)] = {
                    "customer_id":   int(cust_id) if cust_id is not None else None,
                    "customer_name": (cust_name or "").strip(),
                    "agency_id":     int(ag_id) if ag_id is not None else None,
                    "agency_name":   (ag_name or "").strip(),
                }
            return out, None
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Etere customer lookup failed for contracts %s: %s", nos, e)
        return {}, f"Etere customer lookup failed: {e}"


def match_template(templates: list[dict], *,
                   customer_id: int | None = None,
                   agency_id: int | None = None,
                   market: str = "",
                   filename: str = "",
                   advertiser: str = "") -> TemplateMatch:
    """
    Pick the EDI template for an invoice. Deterministic pass order:

    0. Etere customer ID (authoritative): templates whose etere_customer_ids
       contains the contract's COMMITTENTE. Several → narrow by market_match,
       then etere_agency_id. Still several → 'ambiguous', do NOT guess.
    1. Legacy exact advertiser_match (+ market_match) → 'fuzzy' (amber in UI).
    2. Legacy agency-name words in filename, generic words excluded → 'fuzzy'.

    No default-to-first-template pass: an unmatched invoice returns 'none'
    so the UI forces an explicit pick.
    """
    mkt = market.strip().upper()

    if customer_id is not None:
        hits = [t for t in templates
                if customer_id in (t.get("etere_customer_ids") or [])]
        if len(hits) > 1 and mkt:
            narrowed = [t for t in hits
                        if t.get("market_match", "").strip().upper() == mkt]
            if narrowed:
                hits = narrowed
        if len(hits) > 1 and agency_id is not None:
            narrowed = [t for t in hits if t.get("etere_agency_id") == agency_id]
            if narrowed:
                hits = narrowed
        if len(hits) == 1:
            return TemplateMatch(hits[0].get("name", ""), "customer-id",
                                 detail=f"Etere customer {customer_id}")
        if len(hits) > 1:
            return TemplateMatch("", "ambiguous", [t.get("name", "") for t in hits],
                                 detail=f"{len(hits)} templates for customer "
                                        f"{customer_id} — pick one")

    # --- legacy string passes (fuzzy fallback) ---
    fn  = filename.lower()
    adv = advertiser.lower().strip()

    if adv:
        for t in templates:
            am = t.get("advertiser_match", "").strip()
            mm = t.get("market_match", "").strip().upper()
            if not am:
                continue
            if am.lower() == adv:
                if mm and mkt and mm != mkt:
                    continue
                return TemplateMatch(t.get("name", ""), "fuzzy",
                                     detail="advertiser text match — verify")

    for t in templates:
        words = [w for w in re.findall(r'[a-z]{3,}', t.get("agency_name", "").lower())
                 if w not in GENERIC_AGENCY_WORDS]
        if words and any(w in fn for w in words):
            return TemplateMatch(t.get("name", ""), "fuzzy",
                                 detail="agency name in filename — verify")

    return TemplateMatch("", "none", detail="no template matched — pick one")


# ---------------------------------------------------------------------------
# Broadcast calendar
# ---------------------------------------------------------------------------

def broadcast_month_range(yy: int, mm: int) -> tuple[date, date]:
    """
    Date range of a broadcast month (weeks run Mon–Sun; a broadcast month
    starts on the Monday of the week containing the calendar 1st and ends the
    day before the next broadcast month starts). Broadcast June 2026 =
    6/1–6/28 — matches the R31 period dates on the validated June invoices.
    """
    def _start(y: int, m: int) -> date:
        first = date(y, m, 1)
        return first - timedelta(days=first.weekday())

    year = 2000 + yy if yy < 100 else yy
    ny, nm = (year + 1, 1) if mm == 12 else (year, mm + 1)
    return _start(year, mm), _start(ny, nm) - timedelta(days=1)


# ---------------------------------------------------------------------------
# Reconcile: affidavit vs post-log totals
# ---------------------------------------------------------------------------

def reconcile_status(pdf_spots: int | None, pdf_gross: float | None,
                     csv_spots: int | None, csv_gross: float | None) -> dict:
    """
    Compare affidavit totals against post-log totals.

    status: 'match' | 'rounding' | 'mismatch' | 'missing'
    Rounding rule (Lee, 2026-07-09): fractional-cent rates are legitimate —
    the affidavit subtotal sums unrounded rates while the CSV sums rounded
    per-spot values. If spot counts match and the gross difference is within
    spot_count × $0.005, badge 'rounding' (exportable); TVInvoices flags it
    on upload and Lee confirms there.
    """
    if None in (pdf_spots, pdf_gross, csv_spots, csv_gross):
        return {"status": "missing", "detail": "totals unavailable on one side"}
    if pdf_spots != csv_spots:
        return {"status": "mismatch",
                "detail": f"spots: affidavit {pdf_spots} vs post-log {csv_spots}"}
    diff = abs(pdf_gross - csv_gross)
    if diff < 0.005:
        return {"status": "match", "detail": ""}
    if diff <= csv_spots * 0.005 + 1e-9:
        return {"status": "rounding",
                "detail": f"gross differs ${diff:.2f} — fractional-cent rate "
                          f"rounding ({pdf_spots} spots); OK to export"}
    return {"status": "mismatch",
            "detail": f"gross: affidavit ${pdf_gross:,.2f} vs post-log ${csv_gross:,.2f}"}


# ---------------------------------------------------------------------------
# Field validation (TVB EDI spec — see reference_edi_spec / redesign spec §3)
# ---------------------------------------------------------------------------

_YYMMDD = re.compile(r"^\d{6}$")
_YYMM   = re.compile(r"^\d{4}$")
_HHMM   = re.compile(r"^\d{4}$")


def validate_invoice(template: dict, inv: dict, spots: list[dict]) -> list[dict]:
    """
    Validate one invoice against the TVB EDI field rules. Returns a list of
    {field, level, message}; level 'error' blocks export, 'warn' is amber.
    Used by both the UI (per-row display) and the export endpoint (gate).
    """
    issues: list[dict] = []

    def err(fieldname: str, msg: str) -> None:
        issues.append({"field": fieldname, "level": "error", "message": msg})

    def warn(fieldname: str, msg: str) -> None:
        issues.append({"field": fieldname, "level": "warn", "message": msg})

    def _maxlen(fieldname: str, value: str, n: int, level: str = "error") -> None:
        if value and len(value) > n:
            (err if level == "error" else warn)(
                fieldname, f"'{value[:30]}…' is {len(value)} chars — max {n}")

    # R21/R31 name fields ≤ 25
    _maxlen("advertiser_name", inv.get("advertiser_name") or template.get("advertiser_name", ""), 25)
    _maxlen("product_name",    inv.get("product_name")    or template.get("product_name", ""), 25)
    _maxlen("agency_name",     template.get("agency_name", ""), 25)
    _maxlen("representative",  template.get("representative", ""), 25)
    _maxlen("salesperson",     template.get("salesperson", ""), 25)

    # Codes
    _maxlen("agency_ad_code",   inv.get("agency_ad_code")   or template.get("agency_ad_code", ""), 8)
    _maxlen("agency_prod_code", inv.get("agency_prod_code") or template.get("agency_prod_code", ""), 8)
    if not (inv.get("agency_ad_code") or template.get("agency_ad_code")):
        warn("agency_ad_code", "empty — strongly recommended by the spec")
    if not (inv.get("agency_prod_code") or template.get("agency_prod_code")):
        warn("agency_prod_code", "empty — strongly recommended by the spec")

    # Template plumbing
    _maxlen("edi_code", template.get("edi_code", ""), 8)
    cl = template.get("call_letters", "")
    if len(cl) != 4:
        err("call_letters", f"'{cl}' must be exactly 4 characters")
    for i, line in enumerate(template.get("agency_address", [])):
        _maxlen(f"agency_address[{i}]", line, 30)
    for i, line in enumerate(template.get("payee_address", [])):
        _maxlen(f"payee_address[{i}]", line, 30)
    if len(template.get("agency_address", [])) > 4:
        err("agency_address", "more than 4 lines — R21 carries 4; the rest are dropped")

    # Dates
    for f_ in ("invoice_date", "bcast_start", "bcast_end"):
        v = str(inv.get(f_, "") or "")
        if not _YYMMDD.match(v):
            err(f_, f"'{v}' must be 6-digit YYMMDD")
    bm = str(inv.get("broadcast_month", "") or "")
    if not _YYMM.match(bm):
        err("broadcast_month", f"'{bm}' must be 4-digit YYMM")

    # Identifiers
    _maxlen("invoice_number",   str(inv.get("invoice_number", "") or ""), 10)
    if not inv.get("invoice_number"):
        err("invoice_number", "required")
    _maxlen("estimate_code",    str(inv.get("estimate_code", "") or ""), 10)
    _maxlen("rep_order_number", str(inv.get("rep_order_number", "") or ""), 10)
    _maxlen("order_number",     str(inv.get("order_number", "") or ""), 10)

    # Comments ≤ 130
    for f_ in ("comment_top", "comment_bottom", "comment_bottom_2",
               "comment_bottom_3", "comment_bottom_4"):
        _maxlen(f_, str(inv.get(f_, "") or ""), 130)

    # Spots (R51) — at least one required
    if not spots:
        err("spots", "no spots — an invoice needs at least one R51 record")
    for i, s in enumerate(spots):
        if not _YYMMDD.match(str(s.get("run_date", ""))):
            err("spots", f"spot {i+1}: run_date '{s.get('run_date')}' not YYMMDD")
            break
    for i, s in enumerate(spots):
        if not _HHMM.match(str(s.get("time_hhmm", ""))):
            err("spots", f"spot {i+1}: airtime '{s.get('time_hhmm')}' not HHMM")
            break
    for i, s in enumerate(spots):
        if len(str(s.get("copy_id", ""))) > 30:
            err("spots", f"spot {i+1}: copy id '{s.get('copy_id')}' over 30 chars")
            break
    for i, s in enumerate(spots):
        if not isinstance(s.get("rate_cents"), int):
            err("spots", f"spot {i+1}: rate must be integer cents")
            break

    return issues


# ---------------------------------------------------------------------------
# Spot-level diff: affidavit PDF vs post-log CSV — moved verbatim from edi.py
# ---------------------------------------------------------------------------

def _norm_date(s: str) -> str:
    """Normalise M/D/YY or M/D/YYYY → MM/DD/YYYY."""
    parts = s.strip().split("/")
    m, d = parts[0].zfill(2), parts[1].zfill(2)
    y = parts[2] if len(parts[2]) == 4 else f"20{parts[2]}"
    return f"{m}/{d}/{y}"


def diff_pdf_csv(pdf_bytes: bytes, csv_bytes: bytes) -> dict:
    """
    Compare individual spots between the affidavit PDF and the post-log CSV.
    Match key: (normalised air date, actual airtime ±10 min, rate).
    Returns missing_from_csv and extra_in_csv spot lists.
    """
    import pdfplumber

    SPOT_RE = re.compile(
        r'^(\d{1,2}/\d{1,2}/\d{2,4})'   # date
        r'\s+\w+'                          # day
        r'\s+\d+:\d+:\d+'                 # time_in
        r'\s+\d+:\d+:\d+'                 # time_out
        r'\s+\d+:\d+:\d+'                 # length
        r'\s+(\d+:\d+:\d+)'               # actual airtime
        r'\s+(\w+)'                        # language
        r'\s+\d+'                          # count
        r'\s+(\S+)'                        # type (COM/BNS)
        r'\s+\S+'                          # estimate (alphanumeric)
        r'\s+\$\s*([\d,]+\.?\d*)'         # rate
    )

    # --- PDF spots ---
    pdf_spots = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            if page_idx == 0:
                continue
            for line in (page.extract_text() or "").splitlines():
                m = SPOT_RE.match(line.strip())
                if m:
                    pdf_spots.append({
                        "date":      _norm_date(m.group(1)),
                        "airtime":   m.group(2),
                        "lang":      m.group(3),
                        "spot_type": m.group(4),
                        "rate":      float(m.group(5).replace(",", "")),
                    })

    # --- CSV spots ---
    text = csv_bytes.decode("utf-8-sig", errors="replace")
    lines = text.splitlines(keepends=True)
    header_idx = None
    for i, line in enumerate(lines):
        parts = next(csv_mod.reader([line]), [])
        if "dateschedule" in {p.strip().lower() for p in parts}:
            header_idx = i
            break

    csv_spots = []
    if header_idx is not None:
        reader = csv_mod.DictReader(io.StringIO("".join(lines[header_idx:])))
        hl = {(h or "").strip().lower(): h for h in (reader.fieldnames or [])}
        date_col = hl.get("dateschedule")
        time_col = hl.get("airtimep")
        code_col = hl.get("bookingcode2")
        rate_col = hl.get("importo2")
        desc_col = hl.get("rowdescription")
        for row in reader:
            if not any((v or "").strip() for v in row.values()):
                continue
            raw_date = (row.get(date_col) or "").strip()
            airtime  = (row.get(time_col) or "").strip()
            if not raw_date or not airtime:
                continue
            try:
                nd = _norm_date(raw_date)
            except (IndexError, ValueError):
                nd = raw_date
            try:
                rate = float((row.get(rate_col) or "0").replace(",", ""))
            except ValueError:
                rate = 0.0
            csv_spots.append({
                "date":        nd,
                "airtime":     airtime,
                "spot_code":   (row.get(code_col) or "").strip(),
                "rate":        rate,
                "description": (row.get(desc_col) or "").strip(),
            })

    # --- Diff (±10 min tolerance on airtime, rate must match) ---
    from collections import defaultdict

    def _secs(t: str) -> int:
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)

    pdf_by_date: dict[str, list] = defaultdict(list)
    for s in pdf_spots:
        pdf_by_date[s["date"]].append(s)

    csv_by_date: dict[str, list] = defaultdict(list)
    for s in csv_spots:
        csv_by_date[s["date"]].append(s)

    missing, extra = [], []
    for date_ in sorted(set(pdf_by_date) | set(csv_by_date)):
        pdf_day = list(pdf_by_date[date_])
        csv_day = list(csv_by_date[date_])
        used_csv = [False] * len(csv_day)
        used_pdf = [False] * len(pdf_day)

        for pi, ps in enumerate(pdf_day):
            try:
                ps_secs = _secs(ps["airtime"])
            except ValueError:
                continue
            best_dist, best_ci = 601, -1  # sentinel > 600 s (10 min)
            for ci, cs in enumerate(csv_day):
                if used_csv[ci]:
                    continue
                try:
                    dist = abs(_secs(cs["airtime"]) - ps_secs)
                except ValueError:
                    continue
                rate_match = abs(cs.get("rate", 0) - ps.get("rate", 0)) < 0.01
                if rate_match and dist <= 600 and dist < best_dist:
                    best_dist, best_ci = dist, ci
            if best_ci >= 0:
                used_csv[best_ci] = True
                used_pdf[pi] = True

        for pi, ps in enumerate(pdf_day):
            if not used_pdf[pi]:
                missing.append(dict(ps))
        for ci, cs in enumerate(csv_day):
            if not used_csv[ci]:
                extra.append(dict(cs))

    missing.sort(key=lambda s: (s["date"], s["airtime"]))
    extra.sort(key=lambda s:   (s["date"], s["airtime"]))

    return {
        "pdf_total":        len(pdf_spots),
        "csv_total":        len(csv_spots),
        "missing_from_csv": missing,
        "extra_in_csv":     extra,
    }


# ---------------------------------------------------------------------------
# Etere post-log fetch (single web session for the whole batch) — moved
# verbatim from edi.py. A leaked session locks the account: keep the logout
# in `finally` (see data-reference EtereClient rules).
# ---------------------------------------------------------------------------

def fetch_postlog_reports(contracts: list[dict], start_date: str, end_date: str) -> list[dict]:
    """
    Fetch post-log CSVs for [{contract_no, filename}, ...] in ONE Etere web
    session (limited license seats). Dates are M/D/YYYY strings. Returns
    [{name, data, error}] per contract; a failure on one contract does not
    abort the rest.
    """
    import sys
    for p in [str(_BASE), str(_BASE / "browser_automation")]:
        if p not in sys.path:
            sys.path.insert(0, p)

    from browser_automation.etere_direct_client import (
        ETERE_WEB_URL,
        etere_web_login,
        etere_web_logout,
    )

    session = etere_web_login()
    results = []
    try:
        for c in contracts:
            try:
                params = {
                    "reportCode": "R100018_C18236_postlog_with_contract_no",
                    "isSystem":   "False",
                    "reportType": "DOWNLOADCSV",
                    "customerid": 0,
                    "agencyid":   0,
                    "filters[0]": str(c["contract_no"]),
                    "filters[1]": "",
                    "filters[2]": "true",
                    "filters[3]": "true",
                    "filters[4]": start_date,
                    "filters[5]": end_date,
                }
                resp = session.get(
                    f"{ETERE_WEB_URL}/reportsetere/report",
                    params=params,
                    timeout=180,
                )
                resp.raise_for_status()
                stem = c["filename"].rsplit(".", 1)[0] if "." in c["filename"] else c["filename"]
                results.append({
                    "name":  f"{stem}_{c['contract_no']}_postlog.csv",
                    "data":  resp.content,
                    "error": None,
                })
            except Exception as e:
                results.append({"name": None, "data": None, "error": f"{c['filename']}: {e}"})
    finally:
        etere_web_logout(session)

    return results


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
