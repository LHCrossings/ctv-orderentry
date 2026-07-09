"""
EDI Billing — the unified monthly billing page (Phase 3 of
tasks/edi-billing-redesign.md).

Flow: drop the month's affidavit PDFs → intake rows appear (no Etere
touched) → deliberate [Fetch post logs] (ONE Etere web session for the
batch) → rows carry reconcile badges, template match, validation issues →
export ZIP of TVB EDI .txt files, gated on green/rounding + no errors
unless the row is explicitly forced.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from business_logic.services.edi_billing import (
    INCOMING_DIR as INCOMING,
)
from business_logic.services.edi_billing import (
    all_templates,
    broadcast_month_range,
    diff_pdf_csv,
    fetch_postlog_reports,
    generate_edi,
    get_template,
    invoice_info,
    lookup_contract_customers,
    match_template,
    parse_affidavit,
    parse_postlog_csv,
    reconcile_status,
    resolve_market,
    slug,
    validate_invoice,
)

logger = logging.getLogger(__name__)

_INVOICE_RE = re.compile(r'^(\d{4}-\d{3})')


def _safe_name(filename: str) -> str:
    name = Path(filename).name
    return re.sub(r'[^\w .&()+\'-]', '_', name)


def _pairs() -> dict[str, dict]:
    """Pair files in incoming/EDI by MMYY-NNN invoice prefix."""
    pairs: dict[str, dict] = {}
    if not INCOMING.exists():
        return pairs
    for f in INCOMING.iterdir():
        if f.is_dir():
            continue
        m = _INVOICE_RE.match(f.name)
        if not m:
            continue
        key = m.group(1)
        pairs.setdefault(key, {"csv": None, "pdf": None})
        if f.suffix.lower() == ".csv":
            pairs[key]["csv"] = f.name
        elif f.suffix.lower() == ".pdf":
            pairs[key]["pdf"] = f.name
    return pairs


def _assemble_rows() -> list[dict]:
    """
    One row per invoice in incoming/EDI: parsed totals, reconcile status,
    template match, prefilled invoice fields, validation issues.
    """
    pairs = _pairs()
    tmpl_list = all_templates()

    contract_nos: list[str] = []
    parsed_pdfs: dict[str, object] = {}
    for key, p in pairs.items():
        if p["pdf"]:
            a = parse_affidavit((INCOMING / p["pdf"]).read_bytes(), source=p["pdf"])
            parsed_pdfs[key] = a
            if a.contract_no:
                contract_nos.append(a.contract_no)
        if p["csv"] and (m := re.search(r'_(\d+)_postlog', p["csv"])):
            contract_nos.append(m.group(1))
    customer_by_contract, lookup_err = lookup_contract_customers(contract_nos)

    # Batch month = majority MMYY prefix; off-month rows get flagged (never
    # rejected — Lee's call 2026-07-09).
    months = [k[:4] for k in pairs]
    batch_month = max(set(months), key=months.count) if months else ""

    rows = []
    for key in sorted(pairs):
        p = pairs[key]
        a = parsed_pdfs.get(key)

        row: dict = {
            "invoice_number": key,
            "pdf_filename": p["pdf"] or "",
            "csv_filename": p["csv"] or "",
            "warnings": [],
        }
        if lookup_err:
            row["warnings"].append(lookup_err)
        if batch_month and key[:4] != batch_month:
            row["warnings"].append(
                f"invoice month {key[:4]} differs from the batch ({batch_month})")

        # --- affidavit side ---
        contract_no = ""
        pdf_market = ""
        advertiser = ""
        if a:
            row["warnings"].extend(a.warnings)
            contract_no = a.contract_no or ""
            pdf_market = a.market
            advertiser = a.advertiser
            row["pdf_spots"] = a.total_spots
            row["pdf_gross"] = a.gross_amount

        # --- post-log side ---
        d = None
        csv_market = ""
        if p["csv"]:
            d = parse_postlog_csv((INCOMING / p["csv"]).read_bytes(), p["csv"])
            row["warnings"].extend(d.warnings)
            csv_market = d.market
            advertiser = advertiser or d.advertiser
            contract_no = contract_no or d.contract_no or ""
            row["csv_spots"] = d.totals_row_spots if d.totals_row_spots is not None else d.spot_count
            row["csv_gross"] = d.totals_row_gross if d.totals_row_gross is not None else round(d.gross_cents / 100, 2)

        row["contract_no"] = contract_no
        market = resolve_market(csv_market, pdf_market)

        # --- reconcile ---
        row["reconcile"] = reconcile_status(
            row.get("pdf_spots"), row.get("pdf_gross"),
            row.get("csv_spots"), row.get("csv_gross"),
        ) if (a and d) else {"status": "missing",
                             "detail": "post-log not fetched yet" if a else "no affidavit"}

        # --- customer + template match ---
        cust = customer_by_contract.get(int(contract_no)) if contract_no.isdigit() else None
        m = match_template(
            tmpl_list,
            customer_id=cust["customer_id"] if cust else None,
            agency_id=cust["agency_id"] if cust else None,
            market=market,
            filename=p["csv"] or p["pdf"] or "",
            advertiser=advertiser,
        )
        row["suggested_template"] = m.name
        row["match_confidence"]   = m.confidence
        row["match_candidates"]   = m.candidates
        row["match_detail"]       = m.detail
        row["etere_customer_id"]   = cust["customer_id"] if cust else None
        row["etere_customer_name"] = cust["customer_name"] if cust else ""

        # --- prefilled invoice fields (same shape /generate expects) ---
        inv = invoice_info(p["csv"] or f"{key}.csv")
        inv["order_number"] = inv["order_number"] or contract_no
        if d:
            inv.update({
                "spot_count":    d.spot_count,
                "gross_cents":   d.gross_cents,
                "bcast_start":   d.bcast_start,
                "bcast_end":     d.bcast_end,
                "estimate_code": d.estimate_code,
            })
        if a:
            for f_ in ("rep_order_number", "agency_ad_code", "agency_prod_code",
                       "product_name", "comment_top", "comment_bottom"):
                v = getattr(a, f_)
                if v:
                    inv[f_] = v
        if not inv.get("comment_top") and market and m.name:
            tmpl = next((t for t in tmpl_list if t["name"] == m.name), {})
            if market in tmpl.get("comment_top_by_market", {}):
                inv["comment_top"] = tmpl["comment_top_by_market"][market]
        row["invoice_fields"] = inv

        # --- validation (with the suggested template if any) ---
        tmpl = next((t for t in tmpl_list if t["name"] == m.name), None)
        row["issues"] = validate_invoice(tmpl or {}, inv, d.spots if d else [])
        row["has_errors"] = any(i["level"] == "error" for i in row["issues"])
        rows.append(row)

    return rows


def _default_fetch_range(rows: list[dict]) -> dict:
    months = [r["invoice_number"][:4] for r in rows if _INVOICE_RE.match(r["invoice_number"])]
    if not months:
        return {"start_date": "", "end_date": ""}
    batch = max(set(months), key=months.count)
    yy, mm = int(batch[:2]), int(batch[2:])
    try:
        start, end = broadcast_month_range(yy, mm)
    except ValueError:
        return {"start_date": "", "end_date": ""}
    return {"start_date": f"{start.month}/{start.day}/{start.year}",
            "end_date":   f"{end.month}/{end.day}/{end.year}"}


def build_edi_billing_router(jinja: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/edi/billing", tags=["edi-billing"])

    # ── Page ──────────────────────────────────────────────────────────────
    @router.get("", response_class=HTMLResponse)
    async def billing_page(request: Request):
        return jinja.TemplateResponse(request, "edi/billing.html")

    # ── Rows (assembly; also called after intake/fetch) ───────────────────
    @router.get("/rows")
    async def rows():
        assembled = await asyncio.to_thread(_assemble_rows)
        return {"rows": assembled, "fetch_range": _default_fetch_range(assembled)}

    # ── Intake: drop the month's affidavit PDFs ───────────────────────────
    @router.post("/intake")
    async def intake(files: list[UploadFile] = File(...)):
        INCOMING.mkdir(parents=True, exist_ok=True)
        saved, errors = [], []
        for f in files:
            raw = await f.read()
            name = _safe_name(f.filename or "upload.pdf")
            if not name.lower().endswith(".pdf"):
                errors.append({"filename": name, "error": "not a PDF"})
                continue
            a = parse_affidavit(raw, source=name)
            if a.warnings:
                errors.append({"filename": name, "error": "; ".join(a.warnings)})
                continue
            inv_id = a.invoice_id or ""
            if not _INVOICE_RE.match(inv_id):
                errors.append({"filename": name,
                               "error": f"no MMYY-NNN invoice id found (got {inv_id!r})"})
                continue
            if not _INVOICE_RE.match(name):
                name = f"{inv_id} {Path(name).stem}.pdf"
            (INCOMING / name).write_bytes(raw)
            saved.append({"filename": name, "invoice_id": inv_id,
                          "contract_no": a.contract_no})
        return {"saved": saved, "errors": errors}

    # ── Fetch post logs (deliberate button; ONE Etere session) ────────────
    @router.post("/fetch")
    async def fetch(request: Request):
        body = await request.json()
        contracts  = body.get("contracts", [])
        start_date = body.get("start_date", "")
        end_date   = body.get("end_date", "")
        if not contracts:
            raise HTTPException(400, "No contracts to fetch")
        if not start_date or not end_date:
            raise HTTPException(400, "Date range required")

        results = await asyncio.to_thread(
            fetch_postlog_reports, contracts, start_date, end_date
        )
        out = []
        for r in results:
            if r["error"]:
                out.append({"name": r["name"], "ok": False, "error": r["error"]})
                continue
            (INCOMING / _safe_name(r["name"])).write_bytes(r["data"])
            out.append({"name": r["name"], "ok": True, "error": None})
        return {"results": out}

    # ── Re-validate one row (live, after edits) ───────────────────────────
    @router.post("/validate")
    async def validate(request: Request):
        body = await request.json()
        tmpl = get_template(body.get("template_name", "")) or {}
        inv  = body.get("invoice_fields", {})
        csv_fn = body.get("csv_filename", "")
        spots = []
        if csv_fn and Path(csv_fn).name == csv_fn and (INCOMING / csv_fn).exists():
            spots = parse_postlog_csv((INCOMING / csv_fn).read_bytes(), csv_fn).spots
        issues = validate_invoice(tmpl, inv, spots)
        return {"issues": issues,
                "has_errors": any(i["level"] == "error" for i in issues)}

    # ── Spot-level diff (red-badge drill-down) ────────────────────────────
    @router.post("/diff")
    async def diff(request: Request):
        body = await request.json()
        inv_no = body.get("invoice_number", "")
        p = _pairs().get(inv_no)
        if not p or not p["pdf"] or not p["csv"]:
            raise HTTPException(404, f"Need both PDF and CSV for {inv_no}")
        return await asyncio.to_thread(
            diff_pdf_csv,
            (INCOMING / p["pdf"]).read_bytes(),
            (INCOMING / p["csv"]).read_bytes(),
        )

    # ── Export ZIP (gated) ────────────────────────────────────────────────
    @router.post("/export")
    async def export(request: Request):
        items = (await request.json()).get("items", [])
        if not items:
            raise HTTPException(400, "No rows selected")

        refused = []
        prepared = []
        for item in items:
            csv_fn  = item.get("csv_filename", "")
            tmpl_nm = item.get("template_name", "")
            inv     = item.get("invoice_fields", {})
            force   = bool(item.get("force"))
            label   = inv.get("invoice_number") or csv_fn

            if not csv_fn or Path(csv_fn).name != csv_fn or not (INCOMING / csv_fn).exists():
                refused.append({"row": label, "reason": f"post-log CSV not found: {csv_fn}"})
                continue
            template = get_template(tmpl_nm)
            if not template:
                refused.append({"row": label, "reason": f"template not found: {tmpl_nm}"})
                continue

            d = parse_postlog_csv((INCOMING / csv_fn).read_bytes(), csv_fn)
            inv.setdefault("spot_count",  d.spot_count)
            inv.setdefault("gross_cents", d.gross_cents)
            inv.setdefault("bcast_start", d.bcast_start)
            inv.setdefault("bcast_end",   d.bcast_end)

            problems = []
            issues = validate_invoice(template, inv, d.spots)
            if any(i["level"] == "error" for i in issues):
                problems.append("validation errors: " + "; ".join(
                    f"{i['field']}: {i['message']}" for i in issues if i["level"] == "error"))

            # Server-side reconcile gate — recomputed, not trusted from the client
            m = _INVOICE_RE.match(csv_fn)
            pair = _pairs().get(m.group(1)) if m else None
            if pair and pair["pdf"]:
                a = parse_affidavit((INCOMING / pair["pdf"]).read_bytes(), source=pair["pdf"])
                rec = reconcile_status(
                    a.total_spots, a.gross_amount,
                    d.totals_row_spots if d.totals_row_spots is not None else d.spot_count,
                    d.totals_row_gross if d.totals_row_gross is not None else round(d.gross_cents / 100, 2),
                )
                if rec["status"] == "mismatch":
                    problems.append(f"reconcile mismatch: {rec['detail']}")

            if problems and not force:
                refused.append({"row": label, "reason": "; ".join(problems)})
                continue
            prepared.append((label, tmpl_nm, template, inv, d.spots))

        if refused:
            raise HTTPException(409, detail=json.dumps({"refused": refused}))

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for label, tmpl_nm, template, inv, spots in prepared:
                zf.writestr(f"{label}_{slug(tmpl_nm)}.txt",
                            generate_edi(template, inv, spots))
        buf.seek(0)
        return Response(
            content=buf.read(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="edi_billing.zip"'},
        )

    return router
