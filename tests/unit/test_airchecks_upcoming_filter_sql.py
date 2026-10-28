"""The aircheck spot query is built with Python `%` formatting and executed with NO
parameters, so a `%%` inside an interpolated fragment reaches SQL Server verbatim
(Lee, 3189/3190, 2026-10-23: `Incorrect syntax near '86400000'`).

Since 2026-10-27 the "upcoming" cut-off is applied in Python, in Pacific time,
after the market-local air time is converted: the SQL compare of TPALINSE.ORA
(market-local) against GETDATE() (Pacific) kept a WDC 21:08 ET spot "upcoming"
until 20:58 PT, Lee scheduled it at 19:15 PT after it had aired, and the agent —
handed a start_time already in the past — recorded immediately (3189, a17e4d12).
"""

import sys
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from web.routes import airchecks  # noqa: E402


def _run(rows, **kwargs):
    captured = {}

    class _Cur:
        def execute(self, sql):
            captured["sql"] = sql

        def fetchall(self):
            return rows

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self, as_dict=False):
            return _Cur()

    with mock.patch("browser_automation.etere_direct_client.connect", lambda: _Conn()):
        out = airchecks._fetch_etere_spots(3189, **kwargs)
    return captured["sql"], out


def _row(isci, market, day, hhmm, dur_frames=450):
    h, m = hhmm.split(":")
    ms = (int(h) * 3600 + int(m) * 60) * 1000
    return {
        "isci_code": isci,
        "market_id": market,
        "air_date": day,
        "air_ora": int(ms / 1000 * 29.97),
        "air_ms": ms,
        "duration_frames": dur_frames,
        "customer_ref": "1-MD10-2611CT",
        "client_name": "McDonald's",
    }


# WDC is market 8 (Eastern). Rows arrive in (isci, market, date, ora) order, as the SQL orders them.
ROWS = [
    _row("MCIM136526VH", 8, date(2026, 10, 28), "21:08"),
    _row("MCIM136526VH", 8, date(2026, 10, 30), "06:19"),
    _row("MCIM137526VH", 8, date(2026, 10, 27), "21:08"),  # aired 18:08 PT
    _row("MCIM137526VH", 8, date(2026, 10, 29), "21:18"),
    _row("MCIM137526VH", 8, date(2026, 11, 1), "21:12"),
]
NOW_PT = datetime(2026, 10, 27, 19, 15)  # Lee's click, an hour after the 10/27 spot aired


@pytest.mark.parametrize("kwargs", [{}, {"first_only": False}])
def test_sql_never_compares_market_local_ora_with_the_pacific_clock(kwargs):
    sql, _ = _run([], now_pt=NOW_PT, **kwargs)
    assert "%%" not in sql, "a doubled percent reaches SQL Server verbatim (no params)"
    assert "86400000" not in sql and "DATEADD(MINUTE" not in sql, sql
    assert "ID_CONTRATTITESTATA = 3189" in sql
    assert "tp.DATA >= CAST(DATEADD(DAY, -1, GETDATE()) AS DATE)" in sql


def test_history_path_has_no_upcoming_filter():
    sql, out = _run(ROWS, upcoming_only=False, first_only=False, now_pt=NOW_PT)
    assert "GETDATE" not in sql
    assert len(out) == len(ROWS)


def test_an_eastern_spot_that_aired_an_hour_ago_is_not_upcoming():
    _, out = _run(ROWS, now_pt=NOW_PT)
    by_isci = {s["isci_code"]: s for s in out}
    assert set(by_isci) == {"MCIM136526VH", "MCIM137526VH"}
    # first UPCOMING airing, not first airing: the 10/27 21:08 ET row is skipped
    assert by_isci["MCIM137526VH"]["air_datetime"] == "2026-10-29T18:18:00"
    assert by_isci["MCIM136526VH"]["air_datetime"] == "2026-10-28T18:08:00"
    for s in out:
        assert datetime.fromisoformat(s["capture_start"]) > NOW_PT, s


def test_cutoff_is_ten_minutes_ahead_in_pacific():
    # 21:08 ET = 18:08 PT; at 17:59 PT it is still upcoming, at 17:59 + 1 min it is not
    _, out = _run(ROWS[2:3], now_pt=datetime(2026, 10, 27, 17, 57))
    assert [s["air_datetime"] for s in out] == ["2026-10-27T18:08:00"]
    _, out = _run(ROWS[2:3], now_pt=datetime(2026, 10, 27, 17, 59))
    assert out == []


def test_every_airing_when_not_first_only_still_drops_aired_rows():
    _, out = _run(ROWS, first_only=False, now_pt=NOW_PT)
    assert len(out) == 4 and all(s["air_datetime"] > "2026-10-27T19:25" for s in out)
