"""
EDI Tool routes: post-log CSV batch download.
"""

import asyncio
import io
import re
import zipfile

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates


def _extract_contract_number(pdf_bytes: bytes) -> tuple[str, str]:
    """
    Parse a CTV invoice PDF and return (invoice_id, contract_number).
    The affidavit page contains 'Contract Number <digits>' in the header block.
    """
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        # Affidavit is always page 2; fall back to full scan if layout changes
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


def build_edi_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/edi")

    @router.get("/post-log", response_class=HTMLResponse)
    async def post_log_page(request: Request):
        return templates.TemplateResponse(request, "edi/post_log.html")

    @router.post("/post-log/parse")
    async def parse_invoices(files: list[UploadFile] = File(...)):
        contracts = []
        errors = []
        for f in files:
            raw = await f.read()
            try:
                invoice_id, contract_no = _extract_contract_number(raw)
                contracts.append({
                    "filename": f.filename,
                    "invoice_id": invoice_id,
                    "contract_no": contract_no,
                })
            except Exception as e:
                errors.append({"filename": f.filename, "error": str(e)})
        return {"contracts": contracts, "errors": errors}

    @router.post("/post-log/generate")
    async def generate_post_logs(request: Request):
        body = await request.json()
        contracts = body.get("contracts", [])
        start_date = body.get("start_date", "")
        end_date = body.get("end_date", "")

        if not contracts:
            raise HTTPException(400, detail="No contracts provided")
        if not start_date or not end_date:
            raise HTTPException(400, detail="Date range required")

        async def fetch_one(c):
            try:
                csv_bytes = await asyncio.to_thread(
                    _fetch_report_sync, c["contract_no"], start_date, end_date
                )
                stem = c["filename"].rsplit(".", 1)[0] if "." in c["filename"] else c["filename"]
                return {
                    "name": f"{stem}_{c['contract_no']}_postlog.csv",
                    "data": csv_bytes,
                    "error": None,
                }
            except Exception as e:
                return {"name": None, "data": None, "error": f"{c['filename']}: {e}"}

        results = await asyncio.gather(*[fetch_one(c) for c in contracts])

        failures = [r["error"] for r in results if r["error"]]
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

    return router
