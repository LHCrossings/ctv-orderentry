"""Ghost spots — commercial playlist rows with no contract behind them.

A placed spot is two rows: `trafficPalinse` (the contract side, what Strategic Editor
shows and what billing reads) and `TPALINSE` (the playlist side, what Exec Editor airs).
When the contract row goes and the playlist row stays, the spot still airs — unbilled,
and against the separation of the legitimate spots around it. Known producers: Etere's
own unscheduler utility (2026-09-09: 783 4imprint :30 rows aired double for two days after
the 9/3 hiatus revision on WL 215721), manual EE/SE deletes, and an old bug in the WL
automation's `_unschedule_spots` (fixed 2026-07-14).

Shared by `scripts/check_ghost_spots.py` (CLI) and the Broadcast Health nightly scan,
which shows the count in the site header next to the media-file findings. PER/PSA rows
without trafficPalinse are the daily filler mechanism and are excluded by design
(NEWTYPE = 'COM' only).
"""

from __future__ import annotations

import datetime as dt
from collections import Counter

FPS = 29.97
MARKETS = {
    1: "NYC",
    2: "CMP",
    3: "HOU",
    4: "SFO",
    5: "SEA",
    6: "LAX",
    7: "CVC",
    8: "WDC",
    9: "MMT",
    10: "DAL",
}
MAX_ROWS = 400  # rows carried in the payload; the count is always complete

GHOST_WHERE = (
    "t.LIVELLO = 0 AND t.NEWTYPE = 'COM' AND t.ID_FILMATI > 0 AND tp.id_trafficPalinse IS NULL"
)


def hms(frames: int) -> str:
    s = int(frames) / FPS
    return "%02d:%02d:%02d" % (s // 3600, (s % 3600) // 60, s % 60)


def summarize(rows: list[dict]) -> list[dict]:
    """Group ghost rows by creative title → [{title, count, markets, first}], most first."""
    groups: dict[str, dict] = {}
    for r in rows:
        g = groups.setdefault(
            r["title"], {"title": r["title"], "count": 0, "markets": Counter(), "first": None}
        )
        g["count"] += 1
        g["markets"][r["market"]] += 1
        key = (r["date"], r["time"])
        if g["first"] is None or key < (g["first"]["date"], g["first"]["time"]):
            g["first"] = {"market": r["market"], "date": r["date"], "time": r["time"]}
    out = []
    for g in groups.values():
        out.append(
            {
                "title": g["title"],
                "count": g["count"],
                "markets": ", ".join(f"{m} {n}" for m, n in sorted(g["markets"].items())),
                "first": g["first"],
            }
        )
    out.sort(key=lambda g: (-g["count"], g["title"]))
    return out


def scan(conn, today: dt.date | None = None) -> dict:
    """Future ghost spots: every COM playlist row dated after `today`. Today's own rows are
    left out — the day is already on air and master control fixes it by hand (Lee 9/9:
    the 27 kept 9/9 rows re-alerted all morning). Read-only."""
    today = today or dt.date.today()
    cur = conn.cursor()
    cur.execute(
        "SELECT t.ID_TPALINSE, t.COD_USER, t.DATA, t.ORA, RTRIM(t.COD_PROGRA), RTRIM(t.TITLE),"
        " t.DURATION, t.STATUS"
        " FROM TPALINSE t LEFT JOIN trafficPalinse tp ON tp.id_tpalinse = t.ID_TPALINSE"
        f" WHERE {GHOST_WHERE} AND t.DATA > '{today:%Y-%m-%d}'"
        " ORDER BY t.DATA, t.ORA, t.COD_USER"
    )
    rows = []
    for tid, cu, data, ora, code, title, dur, status in cur.fetchall():
        rows.append(
            {
                "id_tpalinse": int(tid),
                "market": MARKETS.get(int(cu), str(cu)),
                "date": data.strftime("%Y-%m-%d") if hasattr(data, "strftime") else str(data)[:10],
                "time": hms(ora),
                "code": code or "",
                "title": (title or code or "?").strip(),
                "dur_s": round(int(dur or 0) / FPS),
                "status": status,
            }
        )
    return {
        "state": "alert" if rows else "ok",
        "checked_at": dt.datetime.now().isoformat(timespec="seconds"),
        "from": today.isoformat(),
        "count": len(rows),
        "by_title": summarize(rows),
        "rows": rows[:MAX_ROWS],
    }


def format_report(result: dict) -> str:
    if not result["count"]:
        return "✓ Playlist clean — no future commercial spots without contract backing."
    lines = [f"⚠ {result['count']} future ghost spot(s) — these WILL AIR UNBILLED:"]
    for g in result["by_title"]:
        f = g["first"]
        lines.append(
            f"  {g['count']:>4}  {g['title']}  ({g['markets']}); first {f['market']} {f['date']} {f['time']}"
        )
    lines.append("")
    for r in result["rows"]:
        lines.append(
            f"  {r['date']}  {r['market']:4s} {r['time']}  dur={r['dur_s']}s  id_tpalinse={r['id_tpalinse']}  {r['code']}"
        )
    if result["count"] > len(result["rows"]):
        lines.append(f"  … {result['count'] - len(result['rows'])} more")
    return "\n".join(lines)
