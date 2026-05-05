"""
EDI Tool routes: post-log CSV batch download and invoice reconciliation.
"""

import asyncio
import csv as csv_mod
import io
import re
import zipfile

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------

def _extract_contract_number(pdf_bytes: bytes) -> tuple[str, str]:
    """Return (invoice_id, contract_no) from the affidavit page (page 2)."""
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = [pdf.pages[1]] if len(pdf.pages) >= 2 else pdf.pages
        for page in pages:
            text = page.extract_text() or ""
            m = re.search(r'Contract\s+Number\s+(\d+)', text)
            if m:
                contract_no = m.group(1)
                inv_m = re.search(r'Affidavit\s+([\w-]+)', text)
                invoice_id = inv_m.group(1) if inv_m else "unknown"
                return invoice_id, contract_no

    raise ValueError("Contract number not found in affidavit (page 2)")


def _parse_pdf_affidavit(pdf_bytes: bytes) -> dict:
    """
    Extract total spots and gross amount from the CTV invoice affidavit.

    Primary source: 'COPY LIST Subtotals N $ X.XX' summary line.
    Fallback: sum individual spot rows.

    Returns: {invoice_id, contract_no, total_spots, gross_amount}
    """
    import pdfplumber

    SUBTOTAL_RE = re.compile(
        r'COPY LIST Subtotals\s+(\d+)\s+\$\s*([\d,]+\.?\d*)'
    )
    ROW_RE = re.compile(
        r'^\d{1,2}/\d{1,2}/\d{2,4}'
        r'\s+\w+'
        r'(?:\s+\d+:\d+:\d+){4}'
        r'\s+\w+'
        r'\s+(\d+)'
        r'\s+\S+'
        r'\s+\S+'          # estimate number — may be alphanumeric (e.g. 13931-SF)
        r'\s+\$\s*([\d,]+\.?\d*)'
    )

    total_spots = None
    gross_amount = None
    contract_no = None
    invoice_id = None
    row_spots = 0
    row_gross = 0.0

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            text = page.extract_text() or ""

            if page_idx == 1:
                m = re.search(r'Contract\s+Number\s+(\d+)', text)
                if m:
                    contract_no = m.group(1)
                inv_m = re.search(r'Affidavit\s+([\w-]+)', text)
                if inv_m:
                    invoice_id = inv_m.group(1)

            if page_idx >= 1:
                sub_m = SUBTOTAL_RE.search(text)
                if sub_m:
                    total_spots = int(sub_m.group(1))
                    gross_amount = float(sub_m.group(2).replace(',', ''))

                for line in text.splitlines():
                    row_m = ROW_RE.match(line.strip())
                    if row_m:
                        cnt = int(row_m.group(1))
                        rate = float(row_m.group(2).replace(',', ''))
                        row_spots += cnt
                        if rate > 0:
                            row_gross += cnt * rate

    return {
        "invoice_id": invoice_id or "unknown",
        "contract_no": contract_no,
        "total_spots": total_spots if total_spots is not None else row_spots,
        "gross_amount": round(gross_amount if gross_amount is not None else row_gross, 2),
    }


# ---------------------------------------------------------------------------
# CSV helper
# ---------------------------------------------------------------------------

def _parse_csv_totals(filename: str, csv_bytes: bytes) -> dict:
    """
    Extract total spots and gross from an Etere post-log CSV.

    The report's last non-empty line is a totals row:
      col 0 = gross amount  (e.g. "9,975.00")
      col 1 = spot count    (e.g. 549)

    Contract number comes from filename pattern *_12345_postlog.csv.
    """
    contract_no = None
    fn_match = re.search(r'_(\d+)_postlog', filename, re.IGNORECASE)
    if fn_match:
        contract_no = fn_match.group(1)

    text = csv_bytes.decode("utf-8-sig", errors="replace")
    # Find the last non-empty line
    last_line = None
    for line in reversed(text.splitlines()):
        if line.strip():
            last_line = line
            break

    if not last_line:
        return {
            "contract_no": contract_no,
            "total_spots": None,
            "gross_amount": None,
            "error": "CSV appears empty",
        }

    parts = next(csv_mod.reader([last_line]), [])

    def _clean_num(s):
        return s.replace(',', '').replace('$', '').strip()

    try:
        gross_amount = round(float(_clean_num(parts[0])), 2)
    except (ValueError, IndexError):
        gross_amount = None

    try:
        total_spots = int(_clean_num(parts[1]))
    except (ValueError, IndexError):
        total_spots = None

    return {
        "contract_no": contract_no,
        "total_spots": total_spots,
        "gross_amount": gross_amount,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Spot-level diff
# ---------------------------------------------------------------------------

def _norm_date(s: str) -> str:
    """Normalise M/D/YY or M/D/YYYY → MM/DD/YYYY."""
    parts = s.strip().split("/")
    m, d = parts[0].zfill(2), parts[1].zfill(2)
    y = parts[2] if len(parts[2]) == 4 else f"20{parts[2]}"
    return f"{m}/{d}/{y}"


def _diff_pdf_csv(pdf_bytes: bytes, csv_bytes: bytes) -> dict:
    """
    Compare individual spots between the affidavit PDF and the post-log CSV.
    Match key: (normalised air date, actual airtime HH:MM:SS).
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

    # --- Diff (±5 s tolerance on airtime) ---
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
    for date in sorted(set(pdf_by_date) | set(csv_by_date)):
        pdf_day = list(pdf_by_date[date])
        csv_day = list(csv_by_date[date])
        used_csv = [False] * len(csv_day)
        used_pdf = [False] * len(pdf_day)

        for pi, ps in enumerate(pdf_day):
            try:
                ps_secs = _secs(ps["airtime"])
            except ValueError:
                continue
            best_dist, best_ci = 6, -1  # sentinel > 5 s
            for ci, cs in enumerate(csv_day):
                if used_csv[ci]:
                    continue
                try:
                    dist = abs(_secs(cs["airtime"]) - ps_secs)
                except ValueError:
                    continue
                if dist <= 5 and dist < best_dist:
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
# Etere fetch (single session for whole batch)
# ---------------------------------------------------------------------------

def _fetch_report_sync(contract_no: str, start_date: str, end_date: str) -> bytes:
    from web.etere_report_fetcher import fetch_etere_report
    return fetch_etere_report(
        contract_number=int(contract_no),
        report_code="R100018_C18236_postlog_with_contract_no",
        is_system="False",
        use_date_range=True,
        start_date=start_date,
        end_date=end_date,
    )


def _fetch_all_reports_sync(contracts: list[dict], start_date: str, end_date: str) -> list[dict]:
    """
    Fetch all post-log reports in a single Etere session to avoid
    exhausting the limited concurrent license seats.
    """
    import sys
    from pathlib import Path
    root = Path(__file__).parent.parent.parent
    for p in [str(root), str(root / "browser_automation")]:
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
# Router
# ---------------------------------------------------------------------------

def build_edi_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/edi")

    @router.get("/post-log", response_class=HTMLResponse)
    async def post_log_page(request: Request):
        return templates.TemplateResponse(request, "edi/post_log.html")

    @router.post("/post-log/parse")
    async def parse_invoices(files: list[UploadFile] = File(...)):
        contracts, errors = [], []
        for f in files:
            raw = await f.read()
            try:
                invoice_id, contract_no = _extract_contract_number(raw)
                contracts.append({
                    "filename":    f.filename,
                    "invoice_id":  invoice_id,
                    "contract_no": contract_no,
                })
            except Exception as e:
                errors.append({"filename": f.filename, "error": str(e)})
        return {"contracts": contracts, "errors": errors}

    @router.post("/post-log/generate")
    async def generate_post_logs(request: Request):
        body = await request.json()
        contracts  = body.get("contracts", [])
        start_date = body.get("start_date", "")
        end_date   = body.get("end_date", "")

        if not contracts:
            raise HTTPException(400, detail="No contracts provided")
        if not start_date or not end_date:
            raise HTTPException(400, detail="Date range required")

        results = await asyncio.to_thread(
            _fetch_all_reports_sync, contracts, start_date, end_date
        )

        failures  = [r["error"] for r in results if r["error"]]
        successes = [r for r in results if r["data"]]

        if not successes:
            raise HTTPException(502, detail="All report fetches failed: " + "; ".join(failures))

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for r in successes:
                zf.writestr(r["name"], r["data"])
        zip_buf.seek(0)

        return StreamingResponse(
            zip_buf,
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=postlogs.zip"},
        )

    @router.post("/post-log/reconcile")
    async def reconcile(
        pdfs: list[UploadFile] = File(...),
        csvs: list[UploadFile] = File(...),
    ):
        # Parse PDFs
        pdf_data: dict[str, dict] = {}
        for f in pdfs:
            raw = await f.read()
            try:
                data = await asyncio.to_thread(_parse_pdf_affidavit, raw)
                data["pdf_filename"] = f.filename
                key = data.get("contract_no") or f.filename
                pdf_data[key] = data
            except Exception as e:
                pdf_data[f.filename] = {
                    "pdf_filename": f.filename,
                    "invoice_id": None,
                    "contract_no": None,
                    "total_spots": None,
                    "gross_amount": None,
                    "error": str(e),
                }

        # Parse CSVs
        csv_data: dict[str, dict] = {}
        for f in csvs:
            raw = await f.read()
            data = _parse_csv_totals(f.filename, raw)
            data["csv_filename"] = f.filename
            key = data.get("contract_no") or f.filename
            csv_data[key] = data

        # Reconcile by contract number
        all_keys = sorted(set(pdf_data) | set(csv_data))
        results = []
        for key in all_keys:
            pdf = pdf_data.get(key, {})
            csv = csv_data.get(key, {})

            pdf_spots  = pdf.get("total_spots")
            pdf_gross  = pdf.get("gross_amount")
            csv_spots  = csv.get("total_spots")
            csv_gross  = csv.get("gross_amount")

            spots_match = (
                pdf_spots is not None and csv_spots is not None
                and pdf_spots == csv_spots
            )
            gross_match = (
                pdf_gross is not None and csv_gross is not None
                and abs(pdf_gross - csv_gross) < 0.02
            )

            results.append({
                "contract_no":   key,
                "invoice_id":    pdf.get("invoice_id"),
                "pdf_filename":  pdf.get("pdf_filename"),
                "csv_filename":  csv.get("csv_filename"),
                "pdf_spots":     pdf_spots,
                "pdf_gross":     pdf_gross,
                "csv_spots":     csv_spots,
                "csv_gross":     csv_gross,
                "spots_match":   spots_match,
                "gross_match":   gross_match,
                "has_pdf":       bool(pdf),
                "has_csv":       bool(csv),
                "pdf_error":     pdf.get("error"),
                "csv_error":     csv.get("error"),
            })

        return {"results": results}

    @router.post("/post-log/diff")
    async def diff_spots(
        pdf: UploadFile = File(...),
        csv: UploadFile = File(...),
    ):
        pdf_bytes = await pdf.read()
        csv_bytes = await csv.read()
        return await asyncio.to_thread(_diff_pdf_csv, pdf_bytes, csv_bytes)

    return router
