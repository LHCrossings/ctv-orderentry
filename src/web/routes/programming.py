"""Programming — Etere Weekly Schedules (program grid) in Control Room.

Phase 1: read-only week x station grid viewer with per-day validation, a
coverage strip (each station's programming horizon), and a block-identity
panel that flags ID churn (the order-picker hazard).

Phase 2: copy week / extend range — the productized rebuild of the 2026-08-07
gridcopy SQL write, dry-run by default, with every guard from that session.

Spec + verified data model: tasks/weekly-schedule-control-room.md
Key invariants (all verified against production):
  * broadcast day = frames 647352 (06:00) -> 3236760 (30:00), span 2589408
  * a 2-frame overlap exists on Sat/Sun in every market — tolerated, not a defect
  * blocks/segments are shared station assets; a copy re-points at the SAME
    ID_TrafficBlock and never creates Traffic_Block rows
  * one block ID appears at most ONCE per day's schedule (Etere's rule)
  * traffic_schedlog only records "Put in trash" — Etere does not log copies,
    so ours owes it nothing
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Constants (verified 2026-08-07 / 2026-08-12; see spec)
# ---------------------------------------------------------------------------

FRAMES_PER_HOUR = 107892          # 29.97 fps * 3600, exact
DAY_START = 647352                # 06:00
DAY_END = 3236760                 # 30:00
DAY_SPAN = DAY_END - DAY_START    # 2589408 = 24h
OVERLAP_TOL = 2                   # universal Sat/Sun 2-frame overlap — normal

STATIONS = [
    (1, "NYC", "New York"),
    (2, "CMP", "Chicago / Minneapolis"),
    (3, "HOU", "Houston"),
    (4, "SFO", "San Francisco"),
    (5, "SEA", "Seattle"),
    (6, "LAX", "Los Angeles"),
    (7, "CVC", "Central Valley"),
    (8, "WDC", "Washington DC"),
    (9, "MMT", "Multimarket"),
    (10, "DAL", "Dallas (TAC)"),
]
_STATION_IDS = {s[0] for s in STATIONS}

# Undo scripts + audit records for committed copies live here (persistent,
# not scratchpad — an 8/07 rule).
_UNDO_DIR = Path(__file__).resolve().parents[3] / "logs" / "program_grid"


def _connect():
    from browser_automation.etere_direct_client import connect

    return connect()


# ---------------------------------------------------------------------------
# Frame helpers
# ---------------------------------------------------------------------------

def _frames_to_bcast(frames: int) -> str:
    """Frame-of-day -> clock HH:MM for display.

    Internally the broadcast day runs 06:00-30:00 (post-midnight stored as
    24:00-29:59), but Master Control reads clock time — so viewing wraps
    hours >= 24 back to 00:00-05:59. Display only; all math stays in frames.
    """
    total_min = round(frames * 60 / FRAMES_PER_HOUR) % 1440
    return f"{total_min // 60:02d}:{total_min % 60:02d}"


def _dur_disp(frames: int) -> str:
    total_min = round(frames * 60 / FRAMES_PER_HOUR)
    h, m = divmod(total_min, 60)
    return f"{h}h{m:02d}" if h else f"{m}m"


def _parse_date(s: str) -> date:
    return datetime.strptime(s.strip()[:10], "%Y-%m-%d").date()


# ---------------------------------------------------------------------------
# Day validation
# ---------------------------------------------------------------------------

def _day_issues(blocks: list[dict]) -> list[str]:
    """blocks: offset-sorted [{'offset': int, 'duration': int, 'name': str}]."""
    issues: list[str] = []
    if not blocks:
        return ["day is empty"]
    if blocks[0]["offset"] != DAY_START:
        issues.append(
            f"day starts at {_frames_to_bcast(blocks[0]['offset'])} (expected 06:00)"
        )
    seen: dict[int, int] = {}
    for b in blocks:
        seen[b["id"]] = seen.get(b["id"], 0) + 1
    for bid, n in seen.items():
        if n > 1:
            nm = next(b["name"] for b in blocks if b["id"] == bid)
            issues.append(f"block {bid} '{nm}' appears {n}x in one day")
    for prev, nxt in zip(blocks, blocks[1:]):
        prev_end = prev["offset"] + prev["duration"]
        delta = nxt["offset"] - prev_end
        if delta > 0:
            issues.append(
                f"gap of {_dur_disp(delta) if delta >= FRAMES_PER_HOUR // 60 else str(delta) + ' frames'}"
                f" after '{prev['name']}' at {_frames_to_bcast(prev_end)}"
            )
        elif delta < -OVERLAP_TOL:
            issues.append(
                f"overlap of {-delta} frames: '{nxt['name']}' starts before"
                f" '{prev['name']}' ends at {_frames_to_bcast(prev_end)}"
            )
    last = blocks[-1]
    day_end = last["offset"] + last["duration"]
    if abs(day_end - DAY_END) > OVERLAP_TOL:
        issues.append(f"day ends at {_frames_to_bcast(day_end)} (expected 06:00)")
    return issues


# ---------------------------------------------------------------------------
# Week loading (shared by the viewer and the copy engine)
# ---------------------------------------------------------------------------

def _load_days(cur, station: int, d_from: date, d_to: date) -> dict[date, dict]:
    """All Level-0 programmed days for a station in [d_from, d_to].

    Returns {date: {cal_id, schedule_id, blocks: [{id,name,offset,duration,
    expired,locked}]}} with blocks offset-sorted.
    """
    cur.execute(
        """
        SELECT tc.Date, tc.ID_TrafficCalendar, tc.ID_TrafficSchedule,
               sb.ID_TrafficBlock, sb.Offset, sb.Locked, b.Name, b.Duration, b.Expired
        FROM Traffic_Calendar tc
        JOIN Traffic_ScheduleBlock sb ON sb.ID_TrafficSchedule = tc.ID_TrafficSchedule
        JOIN Traffic_Block b ON b.ID_TrafficBlock = sb.ID_TrafficBlock
        WHERE tc.Cod_User = %s AND tc.Level = 0 AND tc.Date >= %s AND tc.Date <= %s
        ORDER BY tc.Date, sb.Offset
        """,
        (station, d_from.isoformat(), d_to.isoformat()),
    )
    days: dict[date, dict] = {}
    for row in cur.fetchall():
        d = row[0].date() if hasattr(row[0], "date") else row[0]
        day = days.setdefault(
            d, {"cal_id": row[1], "schedule_id": row[2], "blocks": []}
        )
        day["blocks"].append(
            {
                "id": row[3],
                "offset": row[4],
                "locked": bool(row[5]),
                "name": (row[6] or "").strip(),
                "duration": row[7],
                "expired": bool(row[8]),
            }
        )
    return days


def _segment_counts(cur, block_ids: list[int]) -> dict[int, dict]:
    if not block_ids:
        return {}
    ids = ",".join(str(int(b)) for b in set(block_ids))
    cur.execute(
        f"""
        SELECT ID_TrafficBlock, Type, COUNT(*)
        FROM traffic_segment
        WHERE ID_TrafficBlock IN ({ids}) AND visible = 1
        GROUP BY ID_TrafficBlock, Type
        """
    )
    out: dict[int, dict] = {}
    for bid, typ, n in cur.fetchall():
        out.setdefault(bid, {})[(typ or "").strip()] = n
    return out


# ---------------------------------------------------------------------------
# Copy engine (Phase 2) — rebuild of the 2026-08-07 gridcopy write
# ---------------------------------------------------------------------------

def _copy_station(
    conn, station: int, source_monday: date, t_from: date, t_to: date, commit: bool
) -> dict:
    """Copy a station's source week onto [t_from, t_to], weekday -> weekday.

    Dry-run (commit=False) returns the full plan + guard results and writes
    nothing. Commit runs one transaction, verifies INSIDE it, and rolls back
    on any mismatch. Never creates Traffic_Block rows — inserts re-point at
    the source week's existing block IDs (the identity rule).
    """
    label = next((s[1] for s in STATIONS if s[0] == station), str(station))
    result: dict = {
        "station": station,
        "label": label,
        "ok": False,
        "issues": [],
        "days": [],
        "committed": False,
    }
    issues = result["issues"]
    cur = conn.cursor()

    # --- source week -------------------------------------------------------
    src_days = _load_days(cur, station, source_monday, source_monday + timedelta(days=6))
    for i in range(7):
        d = source_monday + timedelta(days=i)
        if d not in src_days:
            issues.append(f"source week is missing {d.isoformat()}")
        else:
            for msg in _day_issues(src_days[d]["blocks"]):
                issues.append(f"source {d.isoformat()}: {msg}")

    # Weekday-pattern-varies guard: catches Etere's own "one day copied to all
    # seven" failure mode. Mon vs Sat and Mon vs Sun block layouts must differ.
    if not issues:
        def _layout(d: date):
            return sorted((b["id"], b["offset"]) for b in src_days[d]["blocks"])

        mon = _layout(source_monday)
        sat = _layout(source_monday + timedelta(days=5))
        sun = _layout(source_monday + timedelta(days=6))
        if mon == sat and mon == sun:
            issues.append(
                "source week has identical Mon/Sat/Sun layouts — refusing "
                "(the 'one day copied to all seven' signature)"
            )

    # --- target range ------------------------------------------------------
    if t_from > t_to:
        issues.append("target 'from' is after target 'to'")
    n_days = (t_to - t_from).days + 1 if t_from <= t_to else 0
    if n_days > 400:
        issues.append(f"target range is {n_days} days — refusing (>400)")

    # Targets must be empty (v1 has no replace mode — deleting a grid under
    # placed spots is how ghost spots are born).
    if not issues:
        cur.execute(
            """
            SELECT Date FROM Traffic_Calendar
            WHERE Cod_User = %s AND Level = 0 AND Date >= %s AND Date <= %s
            ORDER BY Date
            """,
            (station, t_from.isoformat(), t_to.isoformat()),
        )
        existing = [str(r[0])[:10] for r in cur.fetchall()]
        if existing:
            head = ", ".join(existing[:5]) + ("…" if len(existing) > 5 else "")
            issues.append(
                f"{len(existing)} target day(s) already programmed ({head}) — "
                "targets must be empty"
            )

    # Placed-spot guard (both tables; see the ghost-spot lesson).
    if not issues:
        cur.execute(
            """
            SELECT COUNT(*) FROM TPALINSE
            WHERE COD_USER = %s AND LIVELLO = 0 AND DATA >= %s AND DATA <= %s
            """,
            (station, t_from.isoformat(), t_to.isoformat()),
        )
        n_tp = cur.fetchone()[0]
        cur.execute(
            """
            SELECT COUNT(*) FROM trafficPalinse
            WHERE Cod_User = %s AND Date >= %s AND Date <= %s
            """,
            (station, t_from.isoformat(), t_to.isoformat()),
        )
        n_trp = cur.fetchone()[0]
        if n_tp or n_trp:
            issues.append(
                f"placed spots exist in target range (TPALINSE={n_tp}, "
                f"trafficPalinse={n_trp}) — aborting"
            )

    # --- plan ---------------------------------------------------------------
    if not issues:
        for i in range(n_days):
            d = t_from + timedelta(days=i)
            src_d = source_monday + timedelta(days=d.weekday())
            result["days"].append(
                {
                    "target": d.isoformat(),
                    "source": src_d.isoformat(),
                    "weekday": d.strftime("%a"),
                    "blocks": len(src_days[src_d]["blocks"]),
                }
            )

    if issues:
        return result
    result["ok"] = True
    if not commit:
        return result

    # --- commit: one transaction, verify inside, roll back on mismatch ------
    new_sched_ids: list[int] = []
    new_cal_ids: list[int] = []
    try:
        for i in range(n_days):
            d = t_from + timedelta(days=i)
            src = src_days[source_monday + timedelta(days=d.weekday())]

            cur.execute(
                """
                INSERT INTO Traffic_Schedule (Name, Flag, Notes, Cod_User, Insert_Date, Expired)
                SELECT Name, Flag, Notes, Cod_User, Insert_Date, Expired
                FROM Traffic_Schedule WHERE ID_TrafficSchedule = %s;
                SELECT CAST(SCOPE_IDENTITY() AS int)
                """,
                (src["schedule_id"],),
            )
            new_sched = cur.fetchone()[0]
            new_sched_ids.append(new_sched)

            cur.execute(
                """
                INSERT INTO Traffic_ScheduleBlock (ID_TrafficSchedule, ID_TrafficBlock, Locked, Offset)
                SELECT %s, ID_TrafficBlock, Locked, Offset
                FROM Traffic_ScheduleBlock WHERE ID_TrafficSchedule = %s
                """,
                (new_sched, src["schedule_id"]),
            )

            cur.execute(
                """
                INSERT INTO Traffic_Calendar
                    (Date, Cod_User, Level, ID_TrafficSchedule, Notes,
                     JingleInserted, blockautoinsert, blockautoinsertUser)
                SELECT %s, Cod_User, 0, %s, Notes,
                       JingleInserted, blockautoinsert, blockautoinsertUser
                FROM Traffic_Calendar WHERE ID_TrafficCalendar = %s;
                SELECT CAST(SCOPE_IDENTITY() AS int)
                """,
                (d.isoformat(), new_sched, src["cal_id"]),
            )
            new_cal_ids.append(cur.fetchone()[0])

        # Verify INSIDE the transaction: re-read everything just written and
        # diff against the source, both directions, plus the day invariants.
        written = _load_days(cur, station, t_from, t_to)
        for i in range(n_days):
            d = t_from + timedelta(days=i)
            src = src_days[source_monday + timedelta(days=d.weekday())]
            got = written.get(d)
            if got is None:
                raise RuntimeError(f"verify: {d.isoformat()} missing after insert")
            src_set = sorted((b["id"], b["offset"]) for b in src["blocks"])
            got_set = sorted((b["id"], b["offset"]) for b in got["blocks"])
            if src_set != got_set:
                raise RuntimeError(
                    f"verify: {d.isoformat()} block layout != source "
                    f"({len(got_set)} vs {len(src_set)} rows)"
                )
            day_problems = _day_issues(got["blocks"])
            if day_problems:
                raise RuntimeError(f"verify: {d.isoformat()}: {day_problems[0]}")

        # Undo script + audit record, persisted BEFORE the commit.
        _UNDO_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        sched_csv = ",".join(str(x) for x in new_sched_ids)
        cal_csv = ",".join(str(x) for x in new_cal_ids)
        undo_path = _UNDO_DIR / f"undo_{label}_{stamp}.sql"
        undo_path.write_text(
            f"-- Undo Etere Weekly Schedules copy: {label} "
            f"{t_from.isoformat()}..{t_to.isoformat()} (source week {source_monday.isoformat()})\n"
            f"DELETE FROM Traffic_Calendar WHERE ID_TrafficCalendar IN ({cal_csv});\n"
            f"DELETE FROM Traffic_ScheduleBlock WHERE ID_TrafficSchedule IN ({sched_csv});\n"
            f"DELETE FROM Traffic_Schedule WHERE ID_TrafficSchedule IN ({sched_csv});\n"
        )
        (_UNDO_DIR / f"copy_{label}_{stamp}.json").write_text(
            json.dumps(
                {
                    "station": station,
                    "label": label,
                    "source_week": source_monday.isoformat(),
                    "target_from": t_from.isoformat(),
                    "target_to": t_to.isoformat(),
                    "days": n_days,
                    "schedule_ids": new_sched_ids,
                    "calendar_ids": new_cal_ids,
                    "at": datetime.now().isoformat(timespec="seconds"),
                },
                indent=2,
            )
        )

        conn.commit()
        result["committed"] = True
        result["undo_file"] = str(undo_path)
    except Exception as exc:  # verify failure or SQL error -> full rollback
        conn.rollback()
        result["ok"] = False
        issues.append(f"rolled back: {exc}")
    return result


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class CopyRequest(BaseModel):
    stations: list[int]
    source_week: str            # Monday, YYYY-MM-DD
    target_from: str | None = None  # blank -> each station's horizon + 1 day
    target_to: str
    commit: bool = False


def build_programming_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    # -- pages ---------------------------------------------------------------

    @router.get("/programming")
    def programming_hub(request: Request):
        return templates.TemplateResponse(request, "programming/programming.html")

    @router.get("/programming/weekly-schedules")
    def weekly_schedules_page(request: Request):
        return templates.TemplateResponse(
            request, "programming/weekly_schedules.html"
        )

    # -- read APIs ------------------------------------------------------------

    @router.get("/programming/weekly-schedules/stations")
    def stations_coverage():
        """Station list + programming horizon (last Level-0 calendar date)."""
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT Cod_User, MAX(Date) FROM Traffic_Calendar "
                "WHERE Level = 0 GROUP BY Cod_User"
            )
            horizon = {r[0]: str(r[1])[:10] for r in cur.fetchall()}
        return JSONResponse(
            {
                "stations": [
                    {
                        "id": sid,
                        "code": code,
                        "name": name,
                        "last_programmed": horizon.get(sid),
                    }
                    for sid, code, name in STATIONS
                ]
            }
        )

    @router.get("/programming/weekly-schedules/load")
    def load_week(station: int, week: str):
        """One station's week (Mon-Sun) with per-day validation.

        Only blocks actually scheduled are returned — the expired graveyard
        (~1,500 blocks/station) never appears here.
        """
        if station not in _STATION_IDS:
            return JSONResponse({"error": "unknown station"}, status_code=400)
        monday = _parse_date(week)
        monday -= timedelta(days=monday.weekday())  # snap to Monday
        with _connect() as conn:
            cur = conn.cursor()
            days = _load_days(cur, station, monday, monday + timedelta(days=6))
            all_ids = [b["id"] for d in days.values() for b in d["blocks"]]
            segs = _segment_counts(cur, all_ids)
        out_days = []
        for i in range(7):
            d = monday + timedelta(days=i)
            day = days.get(d)
            blocks = day["blocks"] if day else []
            out_days.append(
                {
                    "date": d.isoformat(),
                    "weekday": d.strftime("%a"),
                    "programmed": day is not None,
                    "issues": _day_issues(blocks) if day else [],
                    "blocks": [
                        {
                            "id": b["id"],
                            "name": b["name"],
                            "from": _frames_to_bcast(b["offset"]),
                            "to": _frames_to_bcast(b["offset"] + b["duration"]),
                            "dur": _dur_disp(b["duration"]),
                            # minutes from 06:00, for proportional rendering
                            "start_min": round((b["offset"] - DAY_START) * 60 / FRAMES_PER_HOUR),
                            "dur_min": round(b["duration"] * 60 / FRAMES_PER_HOUR),
                            "prgs": segs.get(b["id"], {}).get("PRGS", 0),
                            "coms": segs.get(b["id"], {}).get("COMS", 0),
                            "expired": b["expired"],
                        }
                        for b in blocks
                    ],
                }
            )
        return JSONResponse({"station": station, "week": monday.isoformat(), "days": out_days})

    @router.get("/programming/weekly-schedules/identity")
    def identity_report(station: int):
        """ID-churn report: slots (weekday, offset) whose block ID changes
        across future weeks. A stable N-ID set for a multi-airing show is
        correct and is NOT flagged (the once-per-day rule)."""
        if station not in _STATION_IDS:
            return JSONResponse({"error": "unknown station"}, status_code=400)
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT tc.Date, sb.Offset, sb.ID_TrafficBlock, b.Name
                FROM Traffic_Calendar tc
                JOIN Traffic_ScheduleBlock sb ON sb.ID_TrafficSchedule = tc.ID_TrafficSchedule
                JOIN Traffic_Block b ON b.ID_TrafficBlock = sb.ID_TrafficBlock
                WHERE tc.Cod_User = %s AND tc.Level = 0 AND tc.Date >= GETDATE()
                ORDER BY tc.Date
                """,
                (station,),
            )
            rows = cur.fetchall()
        slots: dict[tuple[int, int], dict] = {}
        for dt, offset, bid, name in rows:
            d = dt.date() if hasattr(dt, "date") else dt
            slot = slots.setdefault((d.weekday(), offset), {})
            ent = slot.setdefault(bid, {"name": (name or "").strip(), "first": d, "last": d, "days": 0})
            ent["days"] += 1
            ent["first"] = min(ent["first"], d)
            ent["last"] = max(ent["last"], d)
        churn = []
        wd_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for (wd, offset), by_id in sorted(slots.items()):
            if len(by_id) < 2:
                continue
            names = {e["name"] for e in by_id.values()}
            churn.append(
                {
                    "weekday": wd_names[wd],
                    "time": _frames_to_bcast(offset),
                    "same_show": len(names) == 1,
                    "ids": [
                        {
                            "id": bid,
                            "name": e["name"],
                            "first": e["first"].isoformat(),
                            "last": e["last"].isoformat(),
                            "days": e["days"],
                        }
                        for bid, e in sorted(by_id.items())
                    ],
                }
            )
        # same-show churn (the real picker hazard) sorts first
        churn.sort(key=lambda c: (not c["same_show"], c["weekday"], c["time"]))
        return JSONResponse({"station": station, "churn": churn})

    # -- copy (Phase 2) --------------------------------------------------------

    @router.post("/programming/weekly-schedules/copy")
    def copy_weeks(req: CopyRequest):
        bad = [s for s in req.stations if s not in _STATION_IDS]
        if bad or not req.stations:
            return JSONResponse({"error": f"bad station list: {bad}"}, status_code=400)
        source_monday = _parse_date(req.source_week)
        source_monday -= timedelta(days=source_monday.weekday())
        t_to = _parse_date(req.target_to)
        results = []
        with _connect() as conn:
            cur = conn.cursor()
            for station in req.stations:
                if req.target_from:
                    t_from = _parse_date(req.target_from)
                else:
                    # extend mode: start the day after this station's horizon
                    cur.execute(
                        "SELECT MAX(Date) FROM Traffic_Calendar WHERE Level = 0 AND Cod_User = %s",
                        (station,),
                    )
                    last = cur.fetchone()[0]
                    if last is None:
                        results.append(
                            {
                                "station": station,
                                "ok": False,
                                "issues": ["station has no programmed days at all"],
                                "days": [],
                                "committed": False,
                            }
                        )
                        continue
                    t_from = (last.date() if hasattr(last, "date") else last) + timedelta(days=1)
                results.append(
                    _copy_station(conn, station, source_monday, t_from, t_to, req.commit)
                )
        return JSONResponse({"results": results, "commit": req.commit})

    return router
