"""
Backwrite routes: placement CSV → three-tab Excel download.
"""

import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

_src_path = Path(__file__).parent.parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from backwrite.transformer import (
    REVENUE_TYPES,
    generate_excel,
    parse_csv,
    read_existing_order_fields,
)

SALES_PEOPLE = [
    "Charmaine Lane",
    "Rod Malin",
    "House",
]


def build_backwrite_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/backwrite")

    @router.get("", response_class=HTMLResponse)
    async def backwrite_page(request: Request):
        return templates.TemplateResponse(request, "backwrite.html", {
            "revenue_types": REVENUE_TYPES,
            "sales_people":  SALES_PEOPLE,
        })

    @router.post("/parse-existing")
    async def backwrite_parse_existing(existing_file: UploadFile):
        """Read header fields from an existing Sales Confirmation Excel."""
        try:
            data = await existing_file.read()
            fields = read_existing_order_fields(data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not read file: {exc}")
        return JSONResponse(fields)

    @router.post("/parse-io")
    async def backwrite_parse_io(io_file: UploadFile):
        """Detect whether an IO file has net rates; return net rates for auto gross-up."""
        suffix = Path(io_file.filename).suffix.lower() or ".pdf"
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        try:
            data = await io_file.read()
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            from business_logic.services.pdf_order_detector import PDFOrderDetector
            from web.parser_bridge import get_order_detail
            detected = PDFOrderDetector().detect_order_type(tmp_path, silent=True)
            if not detected:
                return JSONResponse({"rates_are_net": False, "io_net_rates": []})
            detail = get_order_detail(Path(tmp_path), detected.value)
            if detail.get("error"):
                return JSONResponse({"rates_are_net": False, "io_net_rates": []})
            rates_are_net = bool(detail.get("rates_are_net", False))
            io_net_rates = sorted(set(
                round(float(ln.get("rate") or 0), 2)
                for ln in detail.get("lines", [])
                if ln.get("rate")
            ))
            return JSONResponse({"rates_are_net": rates_are_net, "io_net_rates": io_net_rates})
        except Exception as exc:
            return JSONResponse({"rates_are_net": False, "io_net_rates": [], "warning": str(exc)})
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    # ── WorldLink endpoints ───────────────────────────────────────────────────

    @router.post("/worldlink/parse-io")
    async def worldlink_parse_io(io_file: UploadFile):
        """Parse a WorldLink IO PDF and return preview data for the WL backwrite form."""
        suffix = Path(io_file.filename).suffix.lower() or ".pdf"
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        try:
            data = await io_file.read()
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            import sys as _sys
            _proj = Path(__file__).parent.parent.parent.parent
            for _p in [str(_proj), str(_proj / "browser_automation")]:
                if _p not in _sys.path:
                    _sys.path.insert(0, _p)
            from browser_automation.parsers.worldlink_parser import parse_worldlink_pdf  # noqa: I001
            from backwrite.worldlink_transformer import _compute_monthly_revenue, _MONTH_NAMES
            io_data = parse_worldlink_pdf(tmp_path)
            if not io_data:
                raise HTTPException(status_code=400, detail="Could not parse WorldLink PDF")
            lines = io_data.get("lines", [])
            monthly_rev = _compute_monthly_revenue(lines)
            monthly_breakdown = [
                {
                    "month":      _MONTH_NAMES[bm.month],
                    "gross":      gross,
                    "net":        round(gross * 0.85, 2),
                    "broker_fee": round(-gross * 0.85 * 0.10, 2),
                }
                for bm, gross in sorted(monthly_rev.items())
            ]
            total_gross = sum(monthly_rev.values())
            return JSONResponse({
                "agency":           io_data.get("agency", ""),
                "advertiser":       io_data.get("advertiser", ""),
                "tracking_number":  io_data.get("tracking_number", ""),
                "buyer":            io_data.get("buyer", ""),
                "order_comment":    io_data.get("order_comment", ""),
                "order_type":       io_data.get("order_type", "new"),
                "revision_hint":    0 if io_data.get("order_type") == "new" else 1,
                "line_count":       len(lines),
                "total_gross":      round(total_gross, 2),
                "monthly_breakdown": monthly_breakdown,
            })
        except HTTPException:
            raise
        except Exception as exc:
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=400, detail=f"Parse error: {exc}")
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    @router.post("/worldlink/generate")
    async def worldlink_generate(
        io_file:         UploadFile,
        contract_number: str = Form(""),
        revision:        str = Form("0"),
    ):
        """Generate WorldLink backwrite Excel from IO PDF + user inputs."""
        suffix = Path(io_file.filename).suffix.lower() or ".pdf"
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        try:
            data = await io_file.read()
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            import sys as _sys
            _proj = Path(__file__).parent.parent.parent.parent
            for _p in [str(_proj), str(_proj / "browser_automation")]:
                if _p not in _sys.path:
                    _sys.path.insert(0, _p)
            from browser_automation.parsers.worldlink_parser import parse_worldlink_pdf  # noqa: I001
            from backwrite.worldlink_transformer import generate_worldlink_excel
            io_data = parse_worldlink_pdf(tmp_path)
            if not io_data:
                raise HTTPException(status_code=400, detail="Could not parse WorldLink PDF")
            user_inputs = {
                "contract_number": contract_number,
                "revision":        int(revision) if str(revision).isdigit() else 0,
            }
            xlsx_bytes = generate_worldlink_excel(io_data, user_inputs)
        except HTTPException:
            raise
        except Exception as exc:
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Generation error: {exc}")
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        tracking    = io_data.get("tracking_number", "")
        agency      = io_data.get("agency", "")
        advertiser  = io_data.get("advertiser", "")
        agency_w1   = re.sub(r'[\\/:*?"<>|,]', "", (agency.split()[0]   if agency    else "")).strip()
        adv_w1      = re.sub(r'[\\/:*?"<>|,]', "", (advertiser.split()[0] if advertiser else "")).strip()
        lines       = io_data.get("lines", [])
        spot_length = lines[0].get("duration", "") if lines else ""
        revision    = user_inputs.get("revision", 0)
        parts       = ["WorldLink -", agency_w1, adv_w1, spot_length, tracking]
        if revision:
            parts.append(f"(Rev{revision})")
        filename = re.sub(r'[\\/:*?"<>|]', "", " ".join(p for p in parts if p)) + ".xlsx"

        return StreamingResponse(
            io.BytesIO(xlsx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.post("/preview")
    async def backwrite_preview(csv_file: UploadFile):
        """Parse uploaded CSV and return extracted fields as JSON."""
        try:
            data = await csv_file.read()
            header, spots = parse_csv(data)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"CSV parse error: {exc}")

        if not spots:
            raise HTTPException(status_code=400, detail="No spot data found in CSV")

        markets = sorted(set(s.market for s in spots))

        # Try to extract a numeric estimate from the description
        est_match = re.search(r'(\d{4,})', header.description)
        estimate_hint = est_match.group(1) if est_match else ""

        # Unique non-zero gross rates for gross-up UI
        unique_rates = sorted(set(
            s.gross_rate for s in spots if s.gross_rate > 0
        ))

        # Language info via EtereBridge (best-effort)
        language_counts: dict = {}
        language_details: list = []
        language_options: list = []
        try:
            from backwrite.eterebridge_runner import (
                get_language_counts,
                get_language_details,
                get_language_options,
            )
            language_counts  = get_language_counts(data)
            language_details = get_language_details(data)
            language_options = get_language_options()
        except Exception as _eb_err:
            import traceback
            traceback.print_exc()
            print(f"[backwrite/preview] EtereBridge language info failed: {_eb_err}")

        return JSONResponse({
            "agency":            header.agency,
            "client":            header.client,
            "contract_code":     header.contract_code,
            "description":       header.description,
            "order_date":        header.order_date,
            "address":           header.address,
            "city":              header.city,
            "markets":           markets,
            "spot_count":        len(spots),
            "line_count":        len(set(s.line_id for s in spots)),
            "date_range": {
                "start": min(s.air_date for s in spots).strftime("%m/%d/%Y"),
                "end":   max(s.air_date for s in spots).strftime("%m/%d/%Y"),
            },
            "estimate_hint":     estimate_hint,
            "unique_rates":      unique_rates,
            "language_counts":   language_counts,
            "language_details":  language_details,
            "language_options":  language_options,
        })

    @router.post("/generate")
    async def backwrite_generate(
        csv_file:       UploadFile,
        io_file:        Optional[UploadFile] = File(None),
        sales_person:   str = Form(...),
        billing_type:   str = Form(...),
        revenue_type:   str = Form(...),
        agency_flag:    str = Form(...),
        agency_fee:     str = Form("15"),
        estimate:       str = Form(""),
        estimate_run:   str = Form(""),
        contract:       str = Form(""),
        affidavit:      str = Form("Y"),
        order_date:     str = Form(""),
        contact_person: str = Form(""),
        phone:          str = Form(""),
        fax:            str = Form(""),
        email_1:        str = Form(""),
        email_2:        str = Form(""),
        email_3:        str = Form(""),
        email_4:        str = Form(""),
        address:        str = Form(""),
        city:           str = Form(""),
        state:          str = Form(""),
        zip_code:       str = Form(""),
        notes:                str = Form(""),
        gross_up_rates:       str = Form("{}"),
        language_corrections: str = Form("{}"),
    ):
        """Generate backwrite Excel from CSV + user inputs, return as download."""
        try:
            data = await csv_file.read()
            header, spots = parse_csv(data)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"CSV parse error: {exc}")

        if not spots:
            raise HTTPException(status_code=400, detail="No spot data found in CSV")

        # Normalise agency fee: accept 15 or 0.15
        try:
            fee = float(agency_fee) if agency_fee else 0.0
            if fee > 1:
                fee = fee / 100
        except ValueError:
            fee = 0.15

        try:
            gross_up_dict = json.loads(gross_up_rates) if gross_up_rates else {}
        except (ValueError, TypeError):
            gross_up_dict = {}

        try:
            lang_corrections_dict = json.loads(language_corrections) if language_corrections else {}
        except (ValueError, TypeError):
            lang_corrections_dict = {}
        if lang_corrections_dict:
            print(f"[backwrite/generate] Language corrections: {lang_corrections_dict}")

        user_inputs = {
            "sales_person":   sales_person,
            "billing_type":   billing_type,
            "revenue_type":   revenue_type,
            "agency_flag":    agency_flag,
            "agency_fee":     fee,
            "estimate":       estimate,
            "estimate_run":   estimate_run,
            "contract":       contract,
            "affidavit":      affidavit,
            "order_date":     order_date,
            "contact_person": contact_person,
            "phone":          phone,
            "fax":            fax,
            "email_1":        email_1,
            "email_2":        email_2,
            "email_3":        email_3,
            "email_4":        email_4,
            "address":        address,
            "city":           city,
            "state":          state,
            "zip":            zip_code,
            "notes":                notes,
            "gross_up_rates":       gross_up_dict,
            "language_corrections": lang_corrections_dict,
        }

        # ── Parse optional IO file for IO-sourced SC lines ────────────────────
        io_detail = None
        if io_file and io_file.filename:
            io_bytes = await io_file.read()
            if io_bytes:
                suffix = Path(io_file.filename).suffix.lower() or ".pdf"
                fd, tmp_path = tempfile.mkstemp(suffix=suffix)
                try:
                    with os.fdopen(fd, "wb") as f:
                        f.write(io_bytes)
                    from business_logic.services.pdf_order_detector import PDFOrderDetector
                    from web.parser_bridge import get_order_detail
                    detected = PDFOrderDetector().detect_order_type(tmp_path, silent=True)
                    if detected:
                        io_detail = get_order_detail(Path(tmp_path), detected.value)
                        if io_detail.get("error"):
                            print(f"[IO] Parse error: {io_detail['error']}")
                            io_detail = None
                        else:
                            print(f"[IO] Parsed {detected.value}: {len(io_detail.get('lines', []))} lines")
                except Exception as _io_exc:
                    print(f"[IO] Failed to parse IO file: {_io_exc}")
                    io_detail = None
                finally:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

                # Claude fallback when no registered parser handles this IO format
                if io_detail is None:
                    try:
                        from web.io_parser_claude import parse_io_with_claude
                        io_detail = parse_io_with_claude(io_bytes)
                        if io_detail:
                            print(f"[IO] Claude fallback: {len(io_detail.get('lines', []))} lines")
                        else:
                            print("[IO] Claude fallback returned nothing — SC will use CSV grouping")
                    except Exception as _claude_exc:
                        print(f"[IO] Claude fallback error: {_claude_exc}")
                        io_detail = None

        # Server-side fallback: auto-inject gross_up_rates when IO indicates net rates
        # and the user didn't manually provide them via the UI.
        if (io_detail and io_detail.get("rates_are_net")
                and user_inputs.get("agency_flag") == "Agency"
                and not gross_up_dict):
            io_nets = {
                round(float(ln.get("rate") or 0), 4)
                for ln in io_detail.get("lines", [])
                if ln.get("rate")
            }
            if io_nets:
                gross_up_dict = {r: r for r in io_nets}
                user_inputs["gross_up_rates"] = gross_up_dict
                print(f"[backwrite/generate] Auto gross-up from IO net rates: {gross_up_dict}")

        try:
            xlsx_bytes = generate_excel(header, spots, user_inputs, raw_csv=data, io_detail=io_detail)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Excel generation error: {exc}")

        # Build output filename: "MKT - Client Est NNNNN.xlsx"
        markets = sorted(set(s.market for s in spots))
        mkt = markets[0] if markets else "CTV"
        client_short = header.client[:30].strip()
        if estimate:
            raw_name = f"{mkt} - {client_short} Est {estimate}.xlsx"
        else:
            raw_name = f"{header.contract_code}.xlsx"
        filename = re.sub(r'[\\/:*?"<>|]', "", raw_name)

        return StreamingResponse(
            io.BytesIO(xlsx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.post("/fetch-report")
    async def fetch_report(
        contract_number: str = Form(...),
        report_code: str = Form("R100018_C18236_new_pc_with_contract_no"),
    ):
        """Fetch a report from Etere web by contract number and return as CSV download."""
        try:
            from web.etere_report_fetcher import fetch_etere_report
            is_system = "True" if "C0000" in report_code else "False"
            csv_bytes = fetch_etere_report(contract_number.strip(), report_code, is_system=is_system)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Report fetch error: {exc}")

        safe_num = "".join(c for c in contract_number if c.isdigit())
        report_short = report_code.rsplit("_", 1)[-1] if "_" in report_code else report_code
        filename = f"{report_short}_{safe_num}.csv"
        return StreamingResponse(
            io.BytesIO(csv_bytes),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return router
