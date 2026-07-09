"""
EDI Export — generate TVB EDI .txt files from Etere post-log CSVs.

Parsing, template store, and EDI generation live in
business_logic.services.edi_billing (Phase 1 of tasks/edi-billing-redesign.md);
this module is the thin route layer for the /edi/export page.
"""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from business_logic.services.edi_billing import (
    INCOMING_DIR as INCOMING,
    TEMPLATE_DIR as TMPL_DIR,
    all_templates as _all_templates,
    generate_edi as _generate_edi,
    get_template as _get_template,
    invoice_info as _invoice_info,
    parse_affidavit,
    parse_postlog_csv,
    slug as _slug,
    suggest_template as _suggest_template,
)

logger = logging.getLogger(__name__)

TMPL_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Back-compat dict shapes over the service dataclasses
# ---------------------------------------------------------------------------

def _parse_export_csv(csv_bytes: bytes) -> dict:
    d = parse_postlog_csv(csv_bytes)
    return {
        "spots":         d.spots,
        "spot_count":    d.spot_count,
        "gross_cents":   d.gross_cents,
        "bcast_start":   d.bcast_start,
        "bcast_end":     d.bcast_end,
        "estimate_code": d.estimate_code,
        "advertiser":    d.advertiser,
        "market":        d.market,
        "warnings":      d.warnings,
    }


def _parse_affidavit_pdf(pdf_path: Path) -> dict:
    a = parse_affidavit(pdf_path.read_bytes(), source=pdf_path.name)
    return {
        "advertiser":       a.advertiser,
        "market":           a.market,
        "rep_order_number": a.rep_order_number,
        "agency_ad_code":   a.agency_ad_code,
        "agency_prod_code": a.agency_prod_code,
        "product_name":     a.product_name,
        "comment_top":      a.comment_top,
        "comment_bottom":   a.comment_bottom,
        "warnings":         a.warnings,
    }


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def build_edi_export_router(jinja: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/edi/export", tags=["edi-export"])

    # ── Page ──────────────────────────────────────────────────────────────
    @router.get("", response_class=HTMLResponse)
    async def export_page(request: Request):
        return jinja.TemplateResponse(request, "edi/export.html")

    # ── Scan incoming/EDI folder ──────────────────────────────────────────
    @router.get("/scan")
    async def scan():
        if not INCOMING.exists():
            return []
        pairs: dict[str, dict] = {}
        for f in INCOMING.iterdir():
            if f.is_dir():
                continue
            m = re.match(r'^(\d{4}-\d{3})', f.name)
            if not m:
                continue
            key = m.group(1)
            pairs.setdefault(key, {"csv": None, "pdf": None})
            if f.suffix.lower() == ".csv":
                pairs[key]["csv"] = f.name
            elif f.suffix.lower() == ".pdf":
                pairs[key]["pdf"] = f.name

        tmpl_list = _all_templates()
        result = []
        for key in sorted(pairs):
            p = pairs[key]
            if not p["csv"]:
                continue
            info = _invoice_info(p["csv"])
            info["csv_filename"] = p["csv"]
            info["pdf_filename"] = p.get("pdf") or ""
            info["warnings"] = []
            try:
                parsed = _parse_export_csv((INCOMING / p["csv"]).read_bytes())
                info.update({
                    "spot_count":    parsed["spot_count"],
                    "gross_cents":   parsed["gross_cents"],
                    "bcast_start":   parsed["bcast_start"],
                    "bcast_end":     parsed["bcast_end"],
                    "estimate_code": parsed["estimate_code"],
                })
                info["warnings"].extend(parsed["warnings"])
                advertiser = parsed.get("advertiser", "")
                market     = parsed.get("market", "")
            except Exception as e:
                logger.warning("Post-log CSV parse failed for %s: %s", p["csv"], e)
                info["warnings"].append(f"CSV parse failed: {e}")
                info.update(spot_count=0, gross_cents=0,
                            bcast_start="", bcast_end="", estimate_code="")
                advertiser = market = ""
            # PDF affidavit is authoritative for advertiser/market matching + pre-fill
            if p.get("pdf"):
                pdf = _parse_affidavit_pdf(INCOMING / p["pdf"])
                info["warnings"].extend(pdf["warnings"])
                if pdf["advertiser"]:
                    advertiser = pdf["advertiser"]
                if pdf["market"]:
                    market = pdf["market"]
                for key in ("rep_order_number", "agency_ad_code", "agency_prod_code",
                            "product_name", "comment_top", "comment_bottom"):
                    if pdf[key]:
                        info[key] = pdf[key]
            info["suggested_template"] = _suggest_template(p["csv"], tmpl_list, advertiser, market)
            # Apply market-based comment_top from template if not already set
            if not info.get("comment_top") and market:
                tmpl = next((t for t in tmpl_list if t["name"] == info["suggested_template"]), {})
                by_mkt = tmpl.get("comment_top_by_market", {})
                if market in by_mkt:
                    info["comment_top"] = by_mkt[market]
            result.append(info)
        return result

    # ── Templates CRUD ────────────────────────────────────────────────────
    @router.get("/templates")
    async def list_templates():
        return _all_templates()

    @router.post("/templates")
    async def save_template(request: Request):
        data = await request.json()
        name = data.get("name", "").strip()
        if not name:
            raise HTTPException(400, "name required")
        (TMPL_DIR / f"{_slug(name)}.json").write_text(json.dumps(data, indent=2))
        return {"ok": True}

    @router.delete("/templates/{slug}")
    async def del_template(slug: str):
        p = TMPL_DIR / f"{slug}.json"
        if p.exists():
            p.unlink()
        return {"ok": True}

    # ── Generate single .txt ──────────────────────────────────────────────
    @router.post("/generate")
    async def generate_one(request: Request):
        body    = await request.json()
        csv_fn  = body.get("csv_filename", "")
        tmpl_nm = body.get("template_name", "")
        inv     = body.get("invoice_fields", {})

        if not csv_fn or Path(csv_fn).name != csv_fn:
            raise HTTPException(400, f"Invalid filename: {csv_fn}")
        csv_path = INCOMING / csv_fn
        if not csv_path.exists():
            raise HTTPException(404, f"Not found: {csv_fn}")
        template = _get_template(tmpl_nm)
        if not template:
            raise HTTPException(404, f"Template not found: {tmpl_nm}")

        parsed = _parse_export_csv(csv_path.read_bytes())
        inv.setdefault("spot_count",  parsed["spot_count"])
        inv.setdefault("gross_cents", parsed["gross_cents"])
        inv.setdefault("bcast_start", parsed["bcast_start"])
        inv.setdefault("bcast_end",   parsed["bcast_end"])

        content  = _generate_edi(template, inv, parsed["spots"])
        inv_num  = inv.get("invoice_number", Path(csv_fn).stem)
        dl_name  = f"{inv_num}_{_slug(tmpl_nm)}.txt"
        return Response(
            content=content.encode("utf-8"),
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{dl_name}"'},
        )

    # ── Generate batch ZIP ────────────────────────────────────────────────
    @router.post("/generate-batch")
    async def generate_batch(request: Request):
        items = await request.json()
        buf   = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in items:
                csv_fn  = item.get("csv_filename", "")
                tmpl_nm = item.get("template_name", "")
                inv     = item.get("invoice_fields", {})
                if not csv_fn or Path(csv_fn).name != csv_fn:
                    logger.warning("Rejected invalid filename in batch: %r", csv_fn)
                    continue
                csv_path = INCOMING / csv_fn
                if not csv_path.exists():
                    continue
                template = _get_template(tmpl_nm)
                if not template:
                    continue
                parsed = _parse_export_csv(csv_path.read_bytes())
                inv.setdefault("spot_count",  parsed["spot_count"])
                inv.setdefault("gross_cents", parsed["gross_cents"])
                inv.setdefault("bcast_start", parsed["bcast_start"])
                inv.setdefault("bcast_end",   parsed["bcast_end"])
                content = _generate_edi(template, inv, parsed["spots"])
                inv_num = inv.get("invoice_number", Path(csv_fn).stem)
                zf.writestr(f"{inv_num}_{_slug(tmpl_nm)}.txt", content)
        buf.seek(0)
        return Response(
            content=buf.read(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="edi_export.zip"'},
        )

    return router
