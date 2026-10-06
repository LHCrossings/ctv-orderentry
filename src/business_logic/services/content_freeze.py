"""Content freeze — a Stirlitz "Video freeze" alarm that is the PROGRAM, not the playout chain.

2026-10-05: every market went "off air — Video freeze alarm" for 40-90 s bursts at the same
LOCAL clock time (05:xx Pacific for the Eastern feeds, 06:xx for Central, 08:xx for Pacific).
The 8am show (NEWSTODAY100526) held an MBC rights slate on screen with the anchors' audio
running; the multiviewer's freeze detector cannot tell a still card from a dead chain. Lee:
"let's do the content freeze colour on the dot to give us more information."

Rule: a freeze alarm on a station whose playlist row ON AIR at the alarm's start time is a
program piece (NEWTYPE 'PGM') is a content freeze — the picture is inside the file master
control should look at, not the AU/encoder/SRT path. A freeze during a spot, a filler or
with no row on air stays a plain off-air alarm. The on-air row is found in TPALINSE by the
market's LOCAL broadcast time (ORA is frame-of-day at 29.97 fps on a 06:00-30:00 day).
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

FPS = 29.97

# Stirlitz stationName → (Etere market code, COD_USER, zone). MMT's playlist runs on Central
# time (its 10/5 freezes lined up with Houston/Chicago, not New York/DC).
STATIONS: dict[str, tuple[str, int, str]] = {
    "New York": ("NYC", 1, "America/New_York"),
    "Chicago Minneapolis": ("CMP", 2, "America/Chicago"),
    "Houston": ("HOU", 3, "America/Chicago"),
    "San Francisco": ("SFO", 4, "America/Los_Angeles"),
    "SFO KQTA 15.3": ("SFO", 4, "America/Los_Angeles"),
    "Seattle": ("SEA", 5, "America/Los_Angeles"),
    "Los Angeles": ("LAX", 6, "America/Los_Angeles"),
    "Central Valley CA": ("CVC", 7, "America/Los_Angeles"),
    "CVC KBTV 8.2": ("CVC", 7, "America/Los_Angeles"),
    "Washington DC": ("WDC", 8, "America/New_York"),
    "National Multimarket": ("MMT", 9, "America/Chicago"),
    "Dallas KLEG 44.3 Dallas KFWD 52.4": ("DAL", 10, "America/Chicago"),
}


def station_market(station_name: str) -> tuple[str, int, str] | None:
    name = (station_name or "").strip()
    if name in STATIONS:
        return STATIONS[name]
    for key, val in STATIONS.items():  # "Houston type:srt source:…" style ids
        if name.startswith(key):
            return val
    return None


def is_freeze(titles) -> bool:
    return any("freeze" in (t or "").lower() for t in (titles or []))


def local_broadcast_time(since_iso: str, zone: str) -> tuple[dt.date, int]:
    """(broadcast DATA, ORA frames) of an instant in a market's zone. Hours before 06:00
    belong to the previous broadcast day at 24:00-29:59."""
    t = dt.datetime.fromisoformat(since_iso)
    if t.tzinfo is None:
        t = t.replace(tzinfo=ZoneInfo("America/Los_Angeles"))  # the feed's clock is Pacific
    loc = t.astimezone(ZoneInfo(zone))
    secs = loc.hour * 3600 + loc.minute * 60 + loc.second + loc.microsecond / 1e6
    day = loc.date()
    if loc.hour < 6:
        secs += 24 * 3600
        day = day - dt.timedelta(days=1)
    return day, int(round(secs * FPS))


def on_air_row(rows, ora: int) -> dict | None:
    """The row whose [ORA, ORA+DURATION) contains `ora`. `rows` are dicts with ORA,
    DURATION, NEWTYPE, COD_PROGRA, PART (the market's live rows for the day)."""
    best = None
    for r in rows:
        start = int(r["ORA"])
        end = start + int(r.get("DURATION") or 0)
        if start <= ora < end and (best is None or start > int(best["ORA"])):
            best = r
    return best


def describe(row: dict, ora: int) -> dict:
    """{program, part, offset} for a PGM row on air at `ora`."""
    off = max(0, int(ora) - int(row["ORA"]))
    return {
        "program": str(row.get("COD_PROGRA") or "").strip(),
        "part": int(row.get("PART") or 0),
        "offset": round(off / FPS),
    }


def classify_rows(rows, since_iso: str, zone: str) -> dict | None:
    """Pure core: content-freeze detail when the on-air row is a program, else None."""
    _day, ora = local_broadcast_time(since_iso, zone)
    row = on_air_row(rows, ora)
    if row is None or str(row.get("NEWTYPE") or "").strip().upper() != "PGM":
        return None
    return describe(row, ora)


def classify(conn, station_name: str, since_iso: str) -> dict | None:
    """DB-backed: the market's live rows around the alarm instant (±1 h) → classify_rows."""
    mk = station_market(station_name)
    if not mk or not since_iso:
        return None
    _code, cod_user, zone = mk
    day, ora = local_broadcast_time(since_iso, zone)
    lo, hi = ora - int(3600 * FPS), ora + int(3600 * FPS)
    cur = conn.cursor()
    cur.execute(
        """SELECT ORA, DURATION, NEWTYPE, COD_PROGRA, PART FROM TPALINSE
           WHERE DATA = %s AND COD_USER = %s AND LIVELLO = 0 AND ORA BETWEEN %s AND %s""",
        (day, cod_user, lo, hi),
    )
    cols = ["ORA", "DURATION", "NEWTYPE", "COD_PROGRA", "PART"]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return classify_rows(rows, since_iso, zone)
