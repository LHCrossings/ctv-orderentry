"""
EDI Tool routes: post-log CSV batch download and invoice reconciliation.

Affidavit/CSV parsing lives in business_logic.services.edi_billing
(Phase 1 of tasks/edi-billing-redesign.md); the wrappers below preserve
this module's original dict/exception contracts for the routes.
"""

import asyncio
import io
import zipfile

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from business_logic.services.edi_billing import (
    diff_pdf_csv,
    fetch_postlog_reports,
    parse_affidavit,
    parse_postlog_csv,
)

# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------

def _extract_contract_number(pdf_bytes: bytes) -> tuple[str, str]:
    """Return (invoice_id, contract_no) from the affidavit page (page 2)."""
    a = parse_affidavit(pdf_bytes)
    if a.warnings:
        raise ValueError("; ".join(a.warnings))
    if not a.contract_no:
        raise ValueError("Contract number not found in affidavit (page 2)")
    return a.invoice_id or "unknown", a.contract_no


def _parse_pdf_affidavit(pdf_bytes: bytes) -> dict:
    """
    Extract total spots and gross amount from the CTV invoice affidavit.
    Returns: {invoice_id, contract_no, total_spots, gross_amount}
    Raises on unreadable PDFs (the reconcile route reports these per-file).
    """
    a = parse_affidavit(pdf_bytes)
    if a.warnings:
        raise ValueError("; ".join(a.warnings))
    return {
        "invoice_id":   a.invoice_id or "unknown",
        "contract_no":  a.contract_no,
        "total_spots":  a.total_spots,
        "gross_amount": a.gross_amount,
    }


# ---------------------------------------------------------------------------
# CSV helper
# ---------------------------------------------------------------------------

def _parse_csv_totals(filename: str, csv_bytes: bytes) -> dict:
    """
    Extract total spots and gross from an Etere post-log CSV's totals row
    (last non-empty line). Contract number comes from the *_12345_postlog
    filename pattern.
    """
    d = parse_postlog_csv(csv_bytes, filename)
    return {
        "contract_no":  d.contract_no,
        "total_spots":  d.totals_row_spots,
        "gross_amount": d.totals_row_gross,
        "error": "CSV appears empty" if "CSV appears empty" in d.warnings else None,
    }


# ---------------------------------------------------------------------------
# Spot-level diff + Etere fetch — logic lives in the edi_billing service
# ---------------------------------------------------------------------------

_diff_pdf_csv = diff_pdf_csv
_fetch_all_reports_sync = fetch_postlog_reports


# ---------------------------------------------------------------------------
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
