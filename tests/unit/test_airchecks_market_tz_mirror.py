"""The air-checks page's MARKET_TZS mirror equals the server's MARKET_TZ (Lee 10/14).

MMT was moved to Central on 10/6 in airchecks.py only; the manual form on /airchecks
converts the typed market-local time with its own JavaScript table, which stayed on
Eastern, so a 23:59 MMT capture typed on 10/13 recorded at 20:59 PT instead of 21:59.
Two tables, one truth: this test fails the moment they drift."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from web.routes import airchecks  # noqa: E402


def _page_mirror() -> dict[str, str]:
    html = (ROOT / "src/web/templates/airchecks.html").read_text()
    m = re.search(r"const MARKET_TZS = \{(.*?)\};", html, re.S)
    assert m, "MARKET_TZS table not found in airchecks.html"
    body = re.sub(r"//[^\n]*", "", m.group(1))
    return dict(re.findall(r"(\w+):\s*'([^']+)'", body))


def test_page_market_tz_mirror_matches_server_table():
    server = {k: str(v.key) for k, v in airchecks.MARKET_TZ.items()}
    assert _page_mirror() == server


def test_mmt_is_central_on_both_sides():
    assert str(airchecks.MARKET_TZ["MMT"].key) == "America/Chicago"
    assert _page_mirror()["MMT"] == "America/Chicago"
