"""
Agency XML Export — /orders/agency-xml

Drop a Crossings TV proposal workbook (.xlsm/.xlsx), tick the tabs to export
(a workbook can carry several proposal variants, one XML each), confirm the
header values the sheet does not carry, download the TVB SpotTV Cable Proposal
XML the agency ingests — one .xml, or a .zip when several tabs are ticked.
Parsing, reconciliation and schema validation live in
`browser_automation.generators.agency_xml_export`; this module only moves bytes.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import zipfile
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
    remember_header,
    suggested_filename,
)
from browser_automation.parsers.crispin_parser import (  # noqa: E402
    parse_crispin_xlsx,
    proposal_sheet_names,
)

_ACCEPTED = {".xlsx", ".xlsm"}


async def _save_upload(upload: UploadFile) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in _ACCEPTED:
        raise HTTPException(status_code=400, detail="Drop the proposal workbook (.xlsm or .xlsx).")
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(await upload.read())
    return tmp_path


def _parse_sheet(path: str, sheet: str):
    try:
        return parse_crispin_xlsx(path, sheet)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Tab '{sheet}': {exc}")


def _lines_json(preview) -> list:
    return [
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
    ]


def build_agency_xml_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/orders/agency-xml", tags=["agency-xml"])

    @router.get("", response_class=HTMLResponse)
    async def page(request: Request):
        return templates.TemplateResponse(request, "agency_xml.html")

    @router.post("/preview")
    async def preview(workbook: UploadFile = File(...)):
        tmp_path = await _save_upload(workbook)
        try:
            names = proposal_sheet_names(tmp_path)
            if not names:
                raise HTTPException(
                    status_code=400, detail="No tab with a Language / Daypart grid was found."
                )
            sheets = []
            shared = None
            for name in names:
                order = _parse_sheet(tmp_path, name)
                try:
                    pv = build_preview(order, default_header(order))
                except AgencyXmlError as exc:
                    raise HTTPException(status_code=400, detail=f"Tab '{name}': {exc}")
                h = pv.header
                if shared is None:
                    shared = {
                        "sheet_agency": order.agency,
                        "advertiser": h.advertiser,
                        "buyer_company": h.buyer_company,
                        "buyer_name": h.buyer_name,
                        "call_letters": h.call_letters,
                        "salesperson": h.salesperson,
                        "send_date": (h.send_date or date.today()).isoformat(),
                    }
                sheets.append(
                    {
                        "sheet": name,
                        "subtitle": order.subtitle,
                        "proposal_id": h.proposal_id,
                        "proposal_name": h.proposal_name,
                        "product": h.product,
                        "flight_start": pv.flight_start.isoformat(),
                        "flight_end": pv.flight_end.isoformat(),
                        "market_label": pv.market_label,
                        "paid_total": round(
                            sum(ln.rate * sum(ln.weekly_spots) for ln in pv.lines), 2
                        ),
                        "notes": pv.notes,
                        "lines": _lines_json(pv),
                    }
                )
            return JSONResponse({"shared": shared, "sheets": sheets})
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    @router.post("/generate")
    async def generate(
        workbook: UploadFile = File(...),
        sheets: str = Form("[]"),
        advertiser: str = Form(""),
        buyer_company: str = Form(""),
        buyer_name: str = Form(""),
        call_letters: str = Form(""),
        salesperson: str = Form(""),
        send_date: str = Form(""),
        include_spots_per_week: str = Form(""),
    ):
        try:
            wanted = json.loads(sheets or "[]")
            assert isinstance(wanted, list) and all(isinstance(w, dict) for w in wanted)
        except (ValueError, AssertionError):
            raise HTTPException(status_code=400, detail="Bad tab selection.")
        if not wanted:
            raise HTTPException(status_code=400, detail="Tick at least one tab to export.")
        try:
            sd = date.fromisoformat(send_date) if send_date.strip() else date.today()
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Send date '{send_date}' is not a date.")
        ids = [str(w.get("proposal_id", "")).strip() for w in wanted]
        if len(set(ids)) != len(ids):
            raise HTTPException(
                status_code=400, detail="Each exported tab needs its own Proposal ID."
            )

        tmp_path = await _save_upload(workbook)
        outputs: list[tuple[str, bytes]] = []
        header = None
        try:
            for w in wanted:
                sheet = str(w.get("sheet", ""))
                order = _parse_sheet(tmp_path, sheet)
                header = ExportHeader(
                    proposal_id=str(w.get("proposal_id", "")).strip(),
                    proposal_name=str(w.get("proposal_name", "")).strip(),
                    advertiser=advertiser.strip(),
                    product=str(w.get("product", "")).strip(),
                    buyer_company=buyer_company.strip(),
                    buyer_name=buyer_name.strip(),
                    call_letters=call_letters.strip(),
                    salesperson=salesperson.strip() or "Charmaine Lane",
                    send_date=sd,
                    include_spots_per_week=include_spots_per_week.strip().lower()
                    in ("1", "true", "on", "yes"),
                )
                try:
                    outputs.append((suggested_filename(header), export_proposal(order, header)))
                except AgencyXmlError as exc:
                    raise HTTPException(status_code=400, detail=f"Tab '{sheet}': {exc}")
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        try:
            remember_header(header)
        except OSError:
            pass  # memory is a convenience; the export already succeeded

        if len(outputs) == 1:
            fname, data = outputs[0]
            return StreamingResponse(
                io.BytesIO(data),
                media_type="application/xml",
                headers={"Content-Disposition": f'attachment; filename="{fname}"'},
            )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname, data in outputs:
                zf.writestr(fname, data)
        buf.seek(0)
        stem = os.path.commonprefix([f[:-4] for f, _ in outputs]).rstrip("-_ ") or "proposals"
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{stem}-proposals.zip"'},
        )

    return router
