"""
EDI Export — generate TVB EDI .txt files from Etere post-log CSVs.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BASE = Path(__file__).resolve().parent.parent.parent.parent
TMPL_DIR    = _BASE / "data" / "edi_templates"
INCOMING    = _BASE / "incoming" / "EDI"

TMPL_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def _parse_export_csv(csv_bytes: bytes) -> dict:
    """Parse an Etere post-log CSV; return spots + auto-fill fields."""
    text = csv_bytes.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))

    # Structure: row0=labels, row1=values, row2=blank, row3=data-headers, row4+=data
    meta = rows[1] if len(rows) > 1 else []
    est_desc  = meta[3].strip() if len(meta) > 3 else ""

    m = re.search(r'\bEst\.?\s+(\S+)', est_desc, re.IGNORECASE)
    if m:
        estimate_code = m.group(1).rstrip(",.:")
    else:
        # Fallback: trailing number in meta[3], then meta[1]
        for src in [est_desc, meta[1].strip() if len(meta) > 1 else ""]:
            fb = re.search(r'(\d+)\s*$', src)
            if fb:
                estimate_code = fb.group(1)
                break
        else:
            estimate_code = ""

    hdr = rows[3] if len(rows) > 3 else []
    col = {name.strip(): i for i, name in enumerate(hdr)}

    spots = []
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

        spots.append({
            "run_date":   run_date,
            "time_hhmm":  time_hhmm,
            "duration":   dur_secs,
            "copy_id":    copy_id,
            "rate_cents": rate_cents,
        })

    dates       = sorted(s["run_date"] for s in spots if s["run_date"])
    gross_cents = sum(s["rate_cents"] for s in spots)

    return {
        "spots":         spots,
        "spot_count":    len(spots),
        "gross_cents":   gross_cents,
        "bcast_start":   dates[0] if dates else "",
        "bcast_end":     dates[-1] if dates else "",
        "estimate_code": estimate_code,
    }


# ---------------------------------------------------------------------------
# EDI record builders
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
    f[3]  = t.get("advertiser_name", "")
    f[4]  = t.get("product_name", "")
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
    f[22] = inv.get("order_number", "")
    f[23] = inv.get("order_number", "")
    f[25] = t.get("agency_ad_code", "")
    f[27] = t.get("agency_prod_code", "")
    return ";".join(f) + ";"


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
    f[13] = str(spot_count)
    return ";".join(f) + ";"


# ---------------------------------------------------------------------------
# Full file generator
# ---------------------------------------------------------------------------

def _generate_edi(template: dict, inv: dict, spots: list[dict]) -> str:
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
    lines.append(_r34(template, gross, count))
    lines.append(f"12;1;{gross};")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------

def _slug(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


def _all_templates() -> list[dict]:
    out = []
    for p in sorted(TMPL_DIR.glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            pass
    return out


def _get_template(name: str) -> dict | None:
    p = TMPL_DIR / f"{_slug(name)}.json"
    return json.loads(p.read_text()) if p.exists() else None


# ---------------------------------------------------------------------------
# Invoice metadata from filename
# ---------------------------------------------------------------------------

def _invoice_info(filename: str) -> dict:
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


def _suggest_template(filename: str, templates: list[dict]) -> str:
    fn = filename.lower()
    for t in templates:
        words = re.findall(r'[a-z]{3,}', t.get("agency_name","").lower())
        if any(w in fn for w in words):
            return t.get("name", "")
    return templates[0].get("name", "") if templates else ""


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
            info["suggested_template"] = _suggest_template(p["csv"], tmpl_list)
            try:
                parsed = _parse_export_csv((INCOMING / p["csv"]).read_bytes())
                info.update({
                    "spot_count":    parsed["spot_count"],
                    "gross_cents":   parsed["gross_cents"],
                    "bcast_start":   parsed["bcast_start"],
                    "bcast_end":     parsed["bcast_end"],
                    "estimate_code": parsed["estimate_code"],
                })
            except Exception:
                info.update(spot_count=0, gross_cents=0,
                            bcast_start="", bcast_end="", estimate_code="")
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
