"""Content freeze classification (2026-10-05 NEWSTODAY rights slate, Lee 10/6)."""

import datetime as dt

from business_logic.services.content_freeze import (
    FPS,
    classify_rows,
    is_freeze,
    local_broadcast_time,
    on_air_row,
    station_market,
)


def _fr(h, m, s):
    return int(round((h * 3600 + m * 60 + s) * FPS))


# Market 1 (New York) on 2026-10-05, from TPALINSE: News Today part 1, its first break, part 4.
NYC_ROWS = [
    {
        "ORA": _fr(8, 0, 13.75),
        "DURATION": int(511.8 * FPS),
        "NEWTYPE": "PGM",
        "COD_PROGRA": "NEWSTODAY100526",
        "PART": 1,
    },
    {
        "ORA": _fr(8, 8, 45.59),
        "DURATION": int(15 * FPS),
        "NEWTYPE": "COM",
        "COD_PROGRA": "A1C-185XX161H",
        "PART": 0,
    },
    {
        "ORA": _fr(8, 9, 0.61),
        "DURATION": int(30 * FPS),
        "NEWTYPE": "COM",
        "COD_PROGRA": "AH2-231XXA61H",
        "PART": 0,
    },
    {
        "ORA": _fr(8, 38, 28.41),
        "DURATION": int(451.6 * FPS),
        "NEWTYPE": "PGM",
        "COD_PROGRA": "NEWSTODAY100526",
        "PART": 4,
    },
    {
        "ORA": _fr(8, 45, 59.99),
        "DURATION": int(30 * FPS),
        "NEWTYPE": "COM",
        "COD_PROGRA": "FU9-016XX196H",
        "PART": 0,
    },
]


def test_station_map_covers_every_feed_name():
    for name, code in [
        ("New York", "NYC"),
        ("Washington DC", "WDC"),
        ("National Multimarket", "MMT"),
        ("Chicago Minneapolis", "CMP"),
        ("Houston", "HOU"),
        ("San Francisco", "SFO"),
        ("SFO KQTA 15.3", "SFO"),
        ("Seattle", "SEA"),
        ("Los Angeles", "LAX"),
        ("Central Valley CA", "CVC"),
        ("CVC KBTV 8.2", "CVC"),
    ]:
        assert station_market(name)[0] == code
    assert station_market("Houston type:srt source:srt://10.0.0.32:6205 ei:0")[0] == "HOU"
    assert station_market("Mars") is None


def test_local_broadcast_time_uses_the_markets_zone():
    # 05:40:30 Pacific on 10/5 is 08:40:30 Eastern — New York's 8am hour
    day, ora = local_broadcast_time("2026-10-05T05:40:30-07:00", "America/New_York")
    assert (day, ora) == (dt.date(2026, 10, 5), _fr(8, 40, 30))
    # 06:05:42 Pacific is 08:05:42 Central
    assert local_broadcast_time("2026-10-05T06:05:42-07:00", "America/Chicago")[1] == _fr(8, 5, 42)
    # post-midnight belongs to the previous broadcast day at 24h+
    day, ora = local_broadcast_time("2026-10-06T01:30:00-07:00", "America/Los_Angeles")
    assert (day, ora) == (dt.date(2026, 10, 5), _fr(25, 30, 0))


def test_on_air_row_picks_the_latest_row_containing_the_instant():
    assert on_air_row(NYC_ROWS, _fr(8, 5, 40))["PART"] == 1
    assert on_air_row(NYC_ROWS, _fr(8, 8, 50))["COD_PROGRA"] == "A1C-185XX161H"
    assert on_air_row(NYC_ROWS, _fr(8, 20, 0)) is None


def test_the_10_5_freezes_are_content_freezes_in_every_region():
    # Eastern feed times from the Health Events page, Pacific server clock
    for since in (
        "2026-10-05T05:08:30-07:00",
        "2026-10-05T05:40:30-07:00",
        "2026-10-05T05:45:30-07:00",
    ):
        d = classify_rows(NYC_ROWS, since, "America/New_York")
        assert d and d["program"] == "NEWSTODAY100526"
    assert classify_rows(NYC_ROWS, "2026-10-05T05:08:30-07:00", "America/New_York") == {
        "program": "NEWSTODAY100526",
        "part": 1,
        "offset": 496,
    }
    assert classify_rows(NYC_ROWS, "2026-10-05T05:40:30-07:00", "America/New_York")["part"] == 4
    # the same playlist shape holds for Central and Pacific markets at their local 08:05 / 08:40
    assert classify_rows(NYC_ROWS, "2026-10-05T06:05:42-07:00", "America/Chicago")["part"] == 1
    assert classify_rows(NYC_ROWS, "2026-10-05T08:42:55-07:00", "America/Los_Angeles")["part"] == 4


def test_a_freeze_during_a_spot_or_a_hole_is_not_content():
    assert (
        classify_rows(NYC_ROWS, "2026-10-05T05:08:50-07:00", "America/New_York") is None
    )  # COM on air
    assert (
        classify_rows(NYC_ROWS, "2026-10-05T05:20:00-07:00", "America/New_York") is None
    )  # nothing on air


def test_is_freeze_only_for_freeze_titles():
    assert is_freeze(["Video freeze alarm"])
    assert not is_freeze(["Video black alarm", "Audio level - no data"])
