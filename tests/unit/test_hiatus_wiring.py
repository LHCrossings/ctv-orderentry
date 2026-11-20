"""The hiatus logic has ONE home (browser_automation/hiatus.py) and three callers: the script,
WorldLink order entry and the Hiatus card (Lee 11/20). These pin the wiring, not the SQL
(the SQL was rehearsed on 3091 in a rolled-back transaction)."""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

WL = (ROOT / "browser_automation/worldlink_automation.py").read_text()
HIATUS = (ROOT / "browser_automation/hiatus.py").read_text()
ROUTES = (ROOT / "src/web/routes/orders.py").read_text()


def test_worldlink_gather_asks_for_hiatus_dates_and_returns_them():
    assert "hiatus_dates = _ask_hiatus_dates(pdf_path, lines)" in WL
    assert "'hiatus_dates': hiatus_dates" in WL


def test_worldlink_entry_marks_the_days_before_the_commit():
    body = WL[WL.index("def process_worldlink_order_direct") :]
    assert body.index("_apply_hiatus_at_entry(conn, contract_id") < body.index("conn.commit()")


def test_entry_never_pulls_spots_and_the_card_does():
    assert 'hiatus_apply(conn, contract_id, dates, on_placed="skip")' in WL
    assert 'hiatus.apply(conn, cid, dates, on_placed="remove")' in ROUTES


def test_a_pulled_spot_leaves_both_tables_and_is_verified_gone():
    body = HIATUS[HIATUS.index("def _remove_spots") : HIATUS.index("def apply")]
    assert "DELETE FROM trafficPalinse" in body and "DELETE FROM TPALINSE" in body
    assert body.index("write_text") < body.index("DELETE FROM trafficPalinse"), "backup comes first"
    assert "left_pl" in body and "raise HiatusError" in body


def test_only_max_per_day_is_ever_written():
    assert "PASSAGGI_GIORNALIERI = %s" in HIATUS
    assert not re.search(r"SET[^;]*PASSAGGI_SETTIMANALI\s*=", HIATUS), "the weekly cap must not change"


def test_the_script_has_no_second_copy_of_the_logic():
    script = (ROOT / "scripts/hiatus_exclude.py").read_text()
    assert "from browser_automation.hiatus import" in script
    assert "INSERT INTO trf_contractdateexclude" not in script


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient

    from orchestration.config import ApplicationConfig
    from web.routes.orders import build_router

    d = tmp_path_factory.mktemp("cfg")
    cfg = ApplicationConfig(
        incoming_dir=d / "in", processed_dir=d / "out", error_dir=d / "err", customer_db_path=d / "c.db"
    )
    app = FastAPI()
    app.include_router(build_router(cfg, Jinja2Templates(directory=str(ROOT / "src/web/templates"))))
    return TestClient(app)


def test_the_card_is_on_the_orders_page_and_opens(client):
    assert 'href="/orders/hiatus"' in client.get("/orders").text
    page = client.get("/orders/hiatus")
    assert page.status_code == 200
    for step in ("1. Pick the contract", "2. Hiatus dates", "3. Review, then apply", "4. Reschedule in Etere"):
        assert step in page.text


def test_preview_and_apply_refuse_an_empty_selection(client):
    assert client.post("/api/orders/hiatus/preview", json={"contract_ids": [], "dates": "12/24"}).status_code == 400
    assert client.post("/api/orders/hiatus/apply", json={"contract_ids": [], "dates": "12/24"}).status_code == 400
