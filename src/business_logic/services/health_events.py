"""Broadcast Health event log — remember what the header dot showed, and when.

The header indicator is a 5-second in-memory cache of the Stirlitz alarm feed plus the
nightly media scan. Until 2026-09-09 nothing kept a record of it, so Maija's question after
the V-TOPNEWS090826 freeze ("did the dot turn yellow and we just missed it?") could not be
answered. `diff_events` turns two successive status snapshots into the transitions between
them; `EventLog` appends them as JSON lines and reads them back for the Health Events page.

A snapshot is {"unreachable": bool, "offair": [{stationId, stationName, titles, since}],
"media": [{id, code, kind, first}], "ghosts": <count>} — built by broadcast_health.py from
the payload it already serves. Only transitions are logged, so a quiet day writes nothing.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

MAX_LINES = 5000  # keep the file bounded; trim to KEEP_LINES when exceeded
KEEP_LINES = 4000


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def diff_events(prev: dict | None, cur: dict, at: str | None = None) -> list[dict]:
    """Transitions from `prev` to `cur`. `prev is None` = first snapshot after a server
    start: log the start itself plus whatever is already active, never a recovery.

    Event kinds: start, feed_lost, feed_back, offair, onair, media, media_clear,
    ghosts, ghosts_clear (ghost-spot count went from 0 to n / back to 0).
    """
    at = at or now_iso()
    ev: list[dict] = []

    def add(kind: str, **fields) -> None:
        ev.append({"at": at, "kind": kind, **fields})

    p_unreach = bool(prev and prev.get("unreachable"))
    c_unreach = bool(cur.get("unreachable"))
    p_off = {s["stationId"]: s for s in (prev or {}).get("offair", []) if s.get("stationId")}
    c_off = {s["stationId"]: s for s in cur.get("offair", []) if s.get("stationId")}
    p_med = {m["id"]: m for m in (prev or {}).get("media", []) if m.get("id") is not None}
    c_med = {m["id"]: m for m in cur.get("media", []) if m.get("id") is not None}

    if prev is None:
        add("start")
        if c_unreach:
            add("feed_lost", error=cur.get("error", ""))
    else:
        if c_unreach and not p_unreach:
            add("feed_lost", error=cur.get("error", ""))
        elif p_unreach and not c_unreach:
            add("feed_back")

    for sid, s in c_off.items():
        if sid not in p_off:
            add(
                "offair",
                station=s.get("stationName") or sid,
                titles=list(s.get("titles") or []),
                since=s.get("since"),
            )
    if prev is not None and not c_unreach:
        # While the feed is unreachable `offair` is empty for lack of data, not recovery.
        for sid, s in p_off.items():
            if sid not in c_off:
                add("onair", station=s.get("stationName") or sid)

    for mid, m in c_med.items():
        if mid not in p_med:
            add("media", code=m.get("code"), detail=m.get("kind"), first=m.get("first"), id=mid)
    if prev is not None:
        for mid, m in p_med.items():
            if mid not in c_med:
                add("media_clear", code=m.get("code"), id=mid)

    p_ghosts = int((prev or {}).get("ghosts") or 0)
    c_ghosts = int(cur.get("ghosts") or 0)
    if c_ghosts and not p_ghosts:
        add("ghosts", count=c_ghosts)
    elif prev is not None and p_ghosts and not c_ghosts:
        add("ghosts_clear", count=p_ghosts)
    return ev


class EventLog:
    """Append-only JSON-lines file, newest last; bounded by MAX_LINES."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def append(self, events: list[dict]) -> None:
        if not events:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        self._trim()

    def _trim(self) -> None:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        if len(lines) > MAX_LINES:
            self.path.write_text("\n".join(lines[-KEEP_LINES:]) + "\n", encoding="utf-8")

    def read(self, days: int = 7, now: dt.datetime | None = None) -> list[dict]:
        """Events from the last `days` days, newest first. Malformed lines are skipped."""
        if not self.path.is_file():
            return []
        now = now or dt.datetime.now().astimezone()
        cutoff = now - dt.timedelta(days=days)
        out: list[dict] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
                at = dt.datetime.fromisoformat(e["at"])
            except (ValueError, KeyError, TypeError):
                continue
            if at.tzinfo is None:
                at = at.astimezone()
            if at >= cutoff:
                out.append(e)
        out.reverse()
        return out
