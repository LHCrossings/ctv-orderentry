"""Hiatus days on a contract: one implementation for the script, order entry and the Hiatus card.

Etere Web stores a hiatus day as one row in trf_contractdateexclude (line id, date at midnight);
the scheduler places nothing on that line on that day (confirmed 2026-11-20, contracts 3089/3091).

What `apply` does for every line whose flight and day pattern include a hiatus date:

1. Spots already placed on a hiatus day (`on_placed`):
   - "refuse": stop, nothing written (the script's default; unschedule first).
   - "skip":   leave that line/date alone and report it (order entry of a revision).
   - "remove": take the spots off BOTH tables (trafficPalinse and TPALINSE), after a backup of
     every row, refusing aired spots, and verify inside the transaction that no playlist row
     is left (a playlist row with no contract row is a ghost that airs unbilled).
2. Insert the exclusion rows.
3. Raise max per day so the short week still holds its week's spots (Lee 11/20: hiatus spots are
   made up, not dropped; the WEEKLY cap is what matters and is never touched; never lowered).
4. A Scheduled line that is now short goes back to Ready, so a reschedule places only the
   missing spots. The scheduler's own blacklist rows on marked lines are released.
5. The contract notes get a `Hiatus 12/24 and 12/25` line, once.

`apply` does not commit; the caller owns the transaction. Cursors are pymssql (%s).
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

DAY_COLS = ("LUNEDI", "MARTEDI", "MERCOLEDI", "GIOVEDI", "VENERDI", "SABATO", "DOMENICA")
AIRED = ("Q", "D", "A")
MARKETS = {1: "NYC", 2: "CMP", 3: "HOU", 4: "SFO", 5: "SEA", 6: "LAX", 7: "CVC", 8: "WDC", 9: "MMT", 10: "DAL"}


class HiatusError(Exception):
    """The hiatus cannot be applied as asked; nothing should be committed."""


# ── pure helpers ─────────────────────────────────────────────────────────────────


def hiatus_note(dates) -> str:
    """`Hiatus 12/24 and 12/25` / `Hiatus 12/24` / `Hiatus 12/24, 12/25 and 1/1`."""
    parts = [f"{d.month}/{d.day}" for d in sorted(dates)]
    return "Hiatus " + (parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1])


def needed_daily_cap(start, end, day_flags, excluded, total_spots, weekly_spots):
    """(cap the hiatus weeks need, [weeks with no day left]) for one line.

    Weeks run Monday-Sunday. Only weeks that lose a day to `excluded` are looked at.
    weekly_spots 0 means the line has no weekly cap: the flight total is spread over its weeks.
    """
    excluded = {d for d in excluded if start <= d <= end and day_flags[d.weekday()]}
    first_monday = start - dt.timedelta(days=start.weekday())
    weeks = (end - first_monday).days // 7 + 1
    per_week = weekly_spots or -(-total_spots // weeks)
    cap, dead = 0, []
    for monday in sorted({d - dt.timedelta(days=d.weekday()) for d in excluded}):
        days = [monday + dt.timedelta(days=i) for i in range(7)]
        left = [d for d in days if start <= d <= end and day_flags[d.weekday()] and d not in excluded]
        if not left:
            dead.append(monday)
            continue
        cap = max(cap, -(-per_week // len(left)))
    return cap, dead


_DATE = r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)"
_SPAN = re.compile(_DATE + r"(?:\s*(?:-|–|—|to|thru|through)\s*" + _DATE + r")?", re.IGNORECASE)


def _one_date(tok: str, flight_start, flight_end, today) -> dt.date:
    if "-" in tok:
        return dt.date.fromisoformat(tok)
    parts = [int(p) for p in tok.split("/")]
    if len(parts) == 3:
        year = parts[2] + 2000 if parts[2] < 100 else parts[2]
        return dt.date(year, parts[0], parts[1])
    # No year typed: the year that puts the date inside the flight; else the next occurrence.
    if flight_start and flight_end:
        for year in range(flight_start.year, flight_end.year + 1):
            try:
                d = dt.date(year, parts[0], parts[1])
            except ValueError:
                continue
            if flight_start <= d <= flight_end:
                return d
    d = dt.date(today.year, parts[0], parts[1])
    return d if d >= today else dt.date(today.year + 1, parts[0], parts[1])


def parse_hiatus_dates(text: str, flight_start=None, flight_end=None, today=None) -> list:
    """`12/24-12/25`, `12/24 and 12/25`, `12/24, 12/26/26`, `2026-12-24` -> sorted dates.

    A range is expanded day by day. Blank or `none` -> []. Text with no date raises ValueError.
    """
    today = today or dt.date.today()
    text = (text or "").strip()
    if not text or text.lower() in ("none", "no", "n"):
        return []
    # Buyers' shorthand: `12.24/25` (one month, two days), `12.24`, `12/24-25`.
    text = re.sub(r"\b(\d{1,2})\.(\d{1,2})\s*/\s*(\d{1,2})(?![\d/.])", r"\1/\2, \1/\3", text)
    text = re.sub(r"\b(\d{1,2})\.(\d{1,2})(?![\d.])", r"\1/\2", text)
    text = re.sub(r"\b(\d{1,2})/(\d{1,2})\s*[-–]\s*(\d{1,2})(?![\d/])", r"\1/\2-\1/\3", text)
    out = set()
    for m in _SPAN.finditer(text):
        a = _one_date(m.group(1), flight_start, flight_end, today)
        b = _one_date(m.group(2), flight_start, flight_end, today) if m.group(2) else a
        if b < a:
            raise ValueError(f"range {m.group(0)!r} ends before it starts")
        if (b - a).days > 62:
            raise ValueError(f"range {m.group(0)!r} is longer than two months")
        out.update(a + dt.timedelta(days=i) for i in range((b - a).days + 1))
    if not out:
        raise ValueError(f"no date found in {text!r} (use e.g. 12/24-12/25)")
    return sorted(out)


_MENTION = re.compile(r"hiatus|do\s+not\s+air|dark\s+(?:day|date|week)s?", re.IGNORECASE)


def hiatus_mentions(text: str) -> list:
    """Lines of an order's text that talk about a hiatus, for display above the prompt."""
    return [
        ln.strip()
        for ln in (text or "").splitlines()
        if _MENTION.search(ln)
    ]


def mentioned_dates(mentions, flight_start=None, flight_end=None, today=None) -> list:
    """Dates named AFTER the hiatus wording on each line (a flight range printed before it on the
    same line is not a hiatus). Dates outside the flight are dropped. Never raises."""
    out = set()
    for ln in mentions:
        m = _MENTION.search(ln)
        try:
            out.update(parse_hiatus_dates(ln[m.end():] if m else ln, flight_start, flight_end, today))
        except ValueError:
            continue
    if flight_start and flight_end:
        out = {d for d in out if flight_start <= d <= flight_end}
    return sorted(out)


# ── database ─────────────────────────────────────────────────────────────────────


def _as_date(v):
    return v.date() if isinstance(v, dt.datetime) else v


def plan(cur, contract_id: int, dates) -> dict:
    """Read-only: what a hiatus on `dates` means for this contract."""
    dates = sorted(set(dates))
    if not dates:
        raise HiatusError("no hiatus dates given")
    cur.execute(
        """SELECT RTRIM(COD_CONTRATTO), RTRIM(DESCRIZIONE), CAST(NOTE AS NVARCHAR(MAX)), DATA_INIZIO, DATA_TERMINE
           FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = %s""",
        (contract_id,),
    )
    head = cur.fetchone()
    if not head:
        raise HiatusError(f"contract {contract_id} not found")
    cur.execute(
        f"""SELECT ID_CONTRATTIRIGHE, RTRIM(DESCRIZIONE), COD_USER, DATESTART, DATEEND, ROWSTATUS,
                   N_PASSAGGI, PASSAGGI_GIORNALIERI, PASSAGGI_SETTIMANALI, {", ".join(DAY_COLS)}
            FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA = %s ORDER BY ID_CONTRATTIRIGHE""",
        (contract_id,),
    )
    rows = cur.fetchall()
    cur.execute(
        """SELECT e.id_contrattirighe, e.dateexclude FROM trf_contractdateexclude e
           JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = e.id_contrattirighe
           WHERE cr.ID_CONTRATTITESTATA = %s""",
        (contract_id,),
    )
    marked: dict = {}
    for lid, d in cur.fetchall():
        marked.setdefault(lid, set()).add(_as_date(d))
    in_list = ",".join(f"'{d.isoformat()}'" for d in dates)
    cur.execute(
        f"""SELECT t.id_trafficPalinse, t.id_tpalinse, t.ID_ContrattiRighe,
                   CAST(COALESCE(p.DATA, t.Date) AS DATE), p.STATUS
            FROM trafficPalinse t
            JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = t.ID_ContrattiRighe
            LEFT JOIN TPALINSE p ON p.ID_TPALINSE = t.id_tpalinse
            WHERE cr.ID_CONTRATTITESTATA = %s AND CAST(COALESCE(p.DATA, t.Date) AS DATE) IN ({in_list})""",
        (contract_id,),
    )
    spots = [
        {"tp_id": int(a), "pl_id": int(b) if b else None, "line_id": int(c), "date": _as_date(d), "status": s}
        for a, b, c, d, s in cur.fetchall()
    ]
    placed: dict = {}
    for s in spots:
        placed.setdefault((s["line_id"], s["date"]), []).append(s)

    lines = []
    for lid, desc, mkt, d0, d1, status, n_tot, cap_day, cap_week, *days in rows:
        d0, d1 = _as_date(d0), _as_date(d1)
        hit = [d for d in dates if d0 <= d <= d1 and days[d.weekday()]]
        if not hit:
            continue
        have = marked.get(lid, set())
        need, dead = needed_daily_cap(d0, d1, days, have | set(hit), n_tot or 0, cap_week or 0)
        on_days = [s for d in hit for s in placed.get((lid, d), [])]
        lines.append(
            {
                "line_id": int(lid),
                "description": desc or "",
                "market": MARKETS.get(int(mkt or 0), str(mkt)),
                "status": status,
                "start": d0,
                "end": d1,
                "ordered": int(n_tot or 0),
                "weekly": int(cap_week or 0),
                "dates": hit,
                "to_mark": [d for d in hit if d not in have],
                "placed": len(on_days),
                "aired": sum(1 for s in on_days if s["status"] in AIRED),
                "cap_old": int(cap_day or 0),
                "cap_new": max(int(cap_day or 0), need),
                "dead_weeks": dead,
            }
        )
    return {
        "contract_id": contract_id,
        "code": head[0],
        "description": head[1],
        "note": (head[2] or "").rstrip(),
        "flight": (_as_date(head[3]), _as_date(head[4])),
        "dates": dates,
        "hiatus_note": hiatus_note(dates),
        "lines": lines,
        "spots": spots,
        "placed": sum(ln["placed"] for ln in lines),
        "aired": sum(ln["aired"] for ln in lines),
    }


def _lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)) or type(v).__name__ == "Decimal":
        return str(v)
    if hasattr(v, "strftime"):
        return "'" + v.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "'"
    if isinstance(v, (bytes, bytearray)):
        return "0x" + v.hex()
    return "N'" + str(v).replace("'", "''") + "'"


def _remove_spots(cur, p: dict, spots: list, backup_dir: Path) -> Path:
    """Both sides of every spot, backed up first, verified gone. Raises (caller rolls back)."""
    tp_ids = ",".join(str(s["tp_id"]) for s in spots)
    pl_ids = ",".join(str(s["pl_id"]) for s in spots if s["pl_id"])
    now = dt.datetime.now()
    restore = [
        f"-- restore {len(spots)} spot(s) pulled for the hiatus on {p['code']} ({p['contract_id']}), "
        f"{p['hiatus_note']}, generated {now:%Y-%m-%d %H:%M} - run as one batch",
        "BEGIN TRAN;",
    ]
    for table, ids, idcol in (("TPALINSE", pl_ids, "ID_TPALINSE"), ("trafficPalinse", tp_ids, "id_trafficPalinse")):
        if not ids:
            continue
        cur.execute(f"SELECT * FROM {table} WHERE {idcol} IN ({ids})")
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        restore.append(f"SET IDENTITY_INSERT {table} ON;")
        restore += [f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join(_lit(v) for v in r)});" for r in rows]
        restore.append(f"SET IDENTITY_INSERT {table} OFF;")
    restore.append("COMMIT;")
    backup_dir.mkdir(exist_ok=True)
    path = backup_dir / f"hiatus-{p['contract_id']}-spots-restore-{now:%Y%m%d-%H%M%S}.sql"
    path.write_text("\n".join(restore) + "\n")

    cur.execute(f"DELETE FROM trafficPalinse WHERE id_trafficPalinse IN ({tp_ids})")
    gone_tp = cur.rowcount
    if pl_ids:
        cur.execute(f"DELETE FROM TPALINSE WHERE ID_TPALINSE IN ({pl_ids})")
    cur.execute(f"SELECT COUNT(*) FROM trafficPalinse WHERE id_trafficPalinse IN ({tp_ids})")
    left_tp = cur.fetchone()[0]
    left_pl = 0
    if pl_ids:
        cur.execute(f"SELECT COUNT(*) FROM TPALINSE WHERE ID_TPALINSE IN ({pl_ids})")
        left_pl = cur.fetchone()[0]
    if gone_tp != len(spots) or left_tp or left_pl:
        raise HiatusError(
            f"spot removal did not verify (removed {gone_tp}/{len(spots)}, contract rows left {left_tp}, "
            f"playlist rows left {left_pl}); nothing written"
        )
    return path


def apply(conn, contract_id: int, dates, on_placed: str = "refuse", backup_dir: Path | str = "logs") -> dict:
    """Write the hiatus inside the caller's transaction. Returns a summary. Does NOT commit."""
    if on_placed not in ("refuse", "skip", "remove"):
        raise ValueError(on_placed)
    cur = conn.cursor()
    p = plan(cur, contract_id, dates)
    skipped, removed, backup = [], 0, None
    busy = [ln for ln in p["lines"] if ln["placed"]]
    if busy and on_placed == "refuse":
        raise HiatusError(
            "spots are still placed on the hiatus days: "
            + "; ".join(f"line {ln['line_id']} {ln['description']} ({ln['placed']})" for ln in busy)
        )
    if busy and on_placed == "skip":
        skipped = busy
    if busy and on_placed == "remove":
        if p["aired"]:
            raise HiatusError(f"{p['aired']} spot(s) on the hiatus days already aired; they are the as-run record")
        line_ids = {ln["line_id"] for ln in p["lines"]}
        spots = [s for s in p["spots"] if s["line_id"] in line_ids]
        backup = _remove_spots(cur, p, spots, Path(backup_dir))
        removed = len(spots)

    skip_ids = {ln["line_id"] for ln in skipped}
    live = [ln for ln in p["lines"] if ln["line_id"] not in skip_ids]
    marks = [(ln["line_id"], d) for ln in live for d in ln["to_mark"]]
    for lid, d in marks:
        cur.execute(
            "INSERT INTO trf_contractdateexclude (id_contrattirighe, dateexclude) VALUES (%s, %s)",
            (lid, d.isoformat()),
        )
    caps = [ln for ln in live if ln["cap_new"] > ln["cap_old"]]
    for ln in caps:
        cur.execute(
            "UPDATE CONTRATTIRIGHE SET PASSAGGI_GIORNALIERI = %s WHERE ID_CONTRATTIRIGHE = %s",
            (ln["cap_new"], ln["line_id"]),
        )
    # Scheduled (1) but short of its ordered spots = invisible to the scheduler; Ready (0) brings
    # the line back so only the missing spots are placed. Placed spots stay put.
    cur.execute(
        """UPDATE cr SET ROWSTATUS = 0 FROM CONTRATTIRIGHE cr
           WHERE cr.ID_CONTRATTITESTATA = %s AND cr.ROWSTATUS = 1
             AND EXISTS (SELECT 1 FROM trf_contractdateexclude e WHERE e.id_contrattirighe = cr.ID_CONTRATTIRIGHE)
             AND cr.N_PASSAGGI > (SELECT COUNT(*) FROM trafficPalinse t
                                  WHERE t.ID_ContrattiRighe = cr.ID_CONTRATTIRIGHE)""",
        (contract_id,),
    )
    to_ready = cur.rowcount
    # A reschedule that ran before the cap was raised parks what it could not place on the
    # blacklist ("Inserted from Server ..."); the line shows as blacklist-only until those go.
    cur.execute(
        """DELETE l FROM Traffic_ScheduleList l JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = l.ID_ContrattiRighe
           WHERE cr.ID_CONTRATTITESTATA = %s AND l.BlackList > 0 AND l.Notes LIKE 'Inserted from Server%%'
             AND EXISTS (SELECT 1 FROM trf_contractdateexclude e WHERE e.id_contrattirighe = cr.ID_CONTRATTIRIGHE)""",
        (contract_id,),
    )
    released = cur.rowcount
    has_marks = bool(marks) or any(ln["dates"] and not ln["to_mark"] for ln in live)
    note_added = False
    if has_marks and p["hiatus_note"].lower() not in p["note"].lower():
        new_note = f"{p['note']}\n{p['hiatus_note']}" if p["note"] else p["hiatus_note"]
        cur.execute("UPDATE CONTRATTITESTATA SET NOTE = %s WHERE ID_CONTRATTITESTATA = %s", (new_note, contract_id))
        note_added = True
    return {
        "contract_id": contract_id,
        "code": p["code"],
        "dates": p["dates"],
        "hiatus_note": p["hiatus_note"],
        "lines": len(live),
        "marked_rows": len(marks),
        "caps": [(ln["line_id"], ln["cap_old"], ln["cap_new"]) for ln in caps],
        "removed": removed,
        "backup": str(backup) if backup else None,
        "to_ready": to_ready,
        "released": released,
        "note_added": note_added,
        "skipped": [(ln["line_id"], ln["description"], ln["placed"]) for ln in skipped],
        "dead_weeks": [(ln["line_id"], ln["description"], m) for ln in live for m in ln["dead_weeks"]],
    }


def check(conn, contract_id: int) -> dict:
    """Read-only state of a contract's hiatus: use it from a FRESH connection after a commit,
    and again after the reschedule."""
    cur = conn.cursor()
    cur.execute(
        """SELECT RTRIM(COD_CONTRATTO), CAST(NOTE AS NVARCHAR(MAX)) FROM CONTRATTITESTATA
           WHERE ID_CONTRATTITESTATA = %s""",
        (contract_id,),
    )
    code, note = cur.fetchone()
    cur.execute(
        """SELECT CAST(e.dateexclude AS DATE), COUNT(*) FROM trf_contractdateexclude e
           JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = e.id_contrattirighe
           WHERE cr.ID_CONTRATTITESTATA = %s GROUP BY CAST(e.dateexclude AS DATE) ORDER BY 1""",
        (contract_id,),
    )
    excluded = [(_as_date(d), int(n)) for d, n in cur.fetchall()]
    cur.execute(
        """SELECT COUNT(*) FROM trafficPalinse t
           JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = t.ID_ContrattiRighe
           LEFT JOIN TPALINSE p ON p.ID_TPALINSE = t.id_tpalinse
           JOIN trf_contractdateexclude e ON e.id_contrattirighe = t.ID_ContrattiRighe
                AND CAST(e.dateexclude AS DATE) = CAST(COALESCE(p.DATA, t.Date) AS DATE)
           WHERE cr.ID_CONTRATTITESTATA = %s""",
        (contract_id,),
    )
    on_hiatus = int(cur.fetchone()[0])
    cur.execute(
        """SELECT cr.ID_CONTRATTIRIGHE, RTRIM(cr.DESCRIZIONE), cr.COD_USER, cr.ROWSTATUS, cr.N_PASSAGGI,
                  cr.PASSAGGI_GIORNALIERI,
                  (SELECT COUNT(*) FROM trafficPalinse t WHERE t.ID_ContrattiRighe = cr.ID_CONTRATTIRIGHE),
                  (SELECT ISNULL(SUM(l.PassageMiss), 0) FROM Traffic_ScheduleList l
                   WHERE l.ID_ContrattiRighe = cr.ID_CONTRATTIRIGHE AND l.BlackList > 0),
                  (SELECT COUNT(*) FROM trf_contractdateexclude e WHERE e.id_contrattirighe = cr.ID_CONTRATTIRIGHE)
           FROM CONTRATTIRIGHE cr WHERE cr.ID_CONTRATTITESTATA = %s""",
        (contract_id,),
    )
    rows = cur.fetchall()
    short = [
        {
            "line_id": int(r[0]),
            "description": r[1] or "",
            "market": MARKETS.get(int(r[2] or 0), str(r[2])),
            "status": r[3],
            "ordered": int(r[4] or 0),
            "placed": int(r[6]),
            "blacklisted": int(r[7]),
        }
        for r in rows
        if r[8] and int(r[4] or 0) > int(r[6])
    ]
    try:
        from business_logic.services.ghost_spots import scan
    except ImportError:  # the web app imports through the src package
        from src.business_logic.services.ghost_spots import scan
    ghosts = scan(conn)["count"]
    return {
        "contract_id": contract_id,
        "code": code,
        "note": (note or "").rstrip(),
        "excluded": excluded,
        "marked_lines": sum(1 for r in rows if r[8]),
        "ordered": sum(int(r[4] or 0) for r in rows),
        "placed": sum(int(r[6]) for r in rows),
        "blacklisted": sum(int(r[7]) for r in rows),
        "on_hiatus_days": on_hiatus,
        "short_lines": short,
        "ghosts": ghosts,
        "ok": not on_hiatus and not short and not ghosts,
    }
