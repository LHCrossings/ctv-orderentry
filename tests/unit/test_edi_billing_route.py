"""EDI billing route: a production affidavit is exported as production even when a
stray post-log sits beside it (BVK 2609-012, 2026-10-05 — the fetch had pulled the
contract's airtime post-log and the export reconciled 0 vs 240 spots and refused)."""

import io
import shutil
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

_root = Path(__file__).resolve().parents[2]
for p in (str(_root), str(_root / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from web.routes import edi_billing as route  # noqa: E402

_FIXTURES = _root / "tests" / "fixtures" / "edi"


@pytest.fixture
def client(tmp_path, monkeypatch, real_pdfplumber):
    incoming = tmp_path / "EDI"
    incoming.mkdir()
    shutil.copy(_FIXTURES / "2609-012_affidavit.pdf", incoming / "2609-012 BVK.pdf")
    # The stray post-log: the airtime of contract 3055, fetched under the old code.
    (incoming / "2609-012 BVK_3055_postlog.csv").write_text("a,b\n1,2\n\nh\n\nt\n")
    monkeypatch.setattr(route, "INCOMING", incoming)
    monkeypatch.setattr(route, "lookup_contract_customers", lambda ids: ({}, None))
    app = FastAPI()
    app.include_router(
        route.build_edi_billing_router(Jinja2Templates(directory=str(_root / "src/web/templates")))
    )
    return TestClient(app)


def _item(csv_fn: str) -> dict:
    return {
        "items": [
            {
                "csv_filename": csv_fn,
                "template_name": "BVK UC Davis Health",
                # What the page sends: the assembled invoice fields, not a bare number.
                "invoice_fields": {
                    **route.invoice_info("2609-012.csv"),
                    "broadcast_month": "2609",
                    "order_number": "3055",
                },
            }
        ]
    }


def test_export_production_ignores_stray_postlog(client):
    r = client.post("/edi/billing/export", json=_item("2609-012 BVK_3055_postlog.csv"))
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        txt = zf.read(zf.namelist()[0]).decode()
    lines = {ln[:2]: ln for ln in txt.splitlines()}
    assert lines["51"].startswith("51;Y;260920;;1200;30;PRODUCTION;317647")
    assert lines["34"].startswith("34;;317647;47647;270000")
    assert ";4828;2609-012;" in lines["31"]
    assert ";3055;" in lines["31"]
    assert ";UCD;;UCD" in lines["31"]


def test_export_production_without_csv_is_identical(client):
    with_csv = client.post("/edi/billing/export", json=_item("2609-012 BVK_3055_postlog.csv"))
    without = client.post("/edi/billing/export", json=_item(""))
    assert with_csv.status_code == without.status_code == 200
    read = lambda r: zipfile.ZipFile(io.BytesIO(r.content)).read("2609-012_bvk_uc_davis_health.txt")  # noqa: E731
    assert read(with_csv) == read(without)


def test_validate_production_ignores_stray_postlog(client):
    body = {**_item("2609-012 BVK_3055_postlog.csv")["items"][0]}
    r = client.post("/edi/billing/validate", json=body)
    assert r.status_code == 200
    assert r.json()["has_errors"] is False, r.json()
