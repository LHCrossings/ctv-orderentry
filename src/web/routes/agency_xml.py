"""
Agency XML Export — /orders/agency-xml

Drop a Crossings TV proposal workbook (.xlsm/.xlsx), confirm the handful of
header values the sheet does not carry, download the TVB SpotTV Cable Proposal
XML the agency ingests. Parsing, reconciliation and schema validation live in
`browser_automation.generators.agency_xml_export`; this module only moves bytes.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

_project_root = Path(__file__).resolve().parents[3]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from browser_automation.generators.agency_xml_export import (  # noqa: E402
    AgencyXmlError,
    ExportHeader,
    build_preview,
    default_header,
    export_proposal,
    suggested_filename,
)
from browser_automation.parsers.crispin_parser import parse_crispin_xlsx  # noqa: E402

_ACCEPTED = {".xlsx", ".xlsm"}


async def _parse_upload(upload: UploadFile):
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in _ACCEPTED:
        raise HTTPException(status_code=400, detail="Drop the proposal workbook (.xlsm or .xlsx).")
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(await upload.read())
        try:
            return parse_crispin_xlsx(tmp_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _preview_json(preview) -> dict:
    h = preview.header
    return {
        "header": {
            "proposal_id": h.proposal_id,
            "proposal_name": h.proposal_name,
            "advertiser": h.advertiser,
            "product": h.product,
            "buyer_company": h.buyer_company,
            "buyer_name": h.buyer_name,
            "call_letters": h.call_letters,
            "salesperson": h.salesperson,
            "send_date": (h.send_date or date.today()).isoformat(),
        },
        "flight_start": preview.flight_start.isoformat(),
        "flight_end": preview.flight_end.isoformat(),
        "market_label": preview.market_label,
        "notes": preview.notes,
        "lines": [
            {
                "program": ln.program,
                "daypart_name": ln.daypart_name,
                "windows": [
                    {
                        "start": dt.start,
                        "end": dt.end,
                        "days": "".join(
                            n[0] if on else "-"
                            for n, on in zip(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"), dt.days)
                        ),
                    }
                    for dt in ln.day_times
                ],
                "length_sec": ln.length_sec,
                "rate": ln.rate,
                "start": ln.start.isoformat(),
                "end": ln.end.isoformat(),
                "total_spots": sum(ln.weekly_spots),
            }
            for ln in preview.lines
        ],
    }


def build_agency_xml_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/orders/agency-xml", tags=["agency-xml"])

    @router.get("", response_class=HTMLResponse)
    async def page(request: Request):
        return templates.TemplateResponse(request, "agency_xml.html")

    @router.post("/preview")
    async def preview(workbook: UploadFile = File(...)):
        order = await _parse_upload(workbook)
        try:
            pv = build_preview(order, default_header(order))
        except AgencyXmlError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return JSONResponse(_preview_json(pv))

    @router.post("/generate")
    async def generate(
        workbook: UploadFile = File(...),
        proposal_id: str = Form(""),
        proposal_name: str = Form(""),
        advertiser: str = Form(""),
        product: str = Form(""),
        buyer_company: str = Form(""),
        buyer_name: str = Form(""),
        call_letters: str = Form(""),
        salesperson: str = Form(""),
        send_date: str = Form(""),
        include_spots_per_week: str = Form(""),
    ):
        order = await _parse_upload(workbook)
        try:
            sd = date.fromisoformat(send_date) if send_date.strip() else date.today()
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Send date '{send_date}' is not a date.")
        header = ExportHeader(
            proposal_id=proposal_id.strip(),
            proposal_name=proposal_name.strip(),
            advertiser=advertiser.strip(),
            product=product.strip(),
            buyer_company=buyer_company.strip(),
            buyer_name=buyer_name.strip(),
            call_letters=call_letters.strip(),
            salesperson=salesperson.strip() or "Charmaine Lane",
            send_date=sd,
            include_spots_per_week=include_spots_per_week.strip().lower()
            in ("1", "true", "on", "yes"),
        )
        try:
            xml_bytes = export_proposal(order, header)
        except AgencyXmlError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return StreamingResponse(
            io.BytesIO(xml_bytes),
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="{suggested_filename(header)}"'},
        )

    return router
