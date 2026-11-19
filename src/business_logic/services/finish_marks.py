"""Fill & Finish "done by hand" marks (Ashe 11/19).

An operator who schedules a show by hand ticks it so the next operator does not check it
again or run Finish over it. The page's states stay derived from the live playlist; this
is the one stored flag, and it is kept honest by a fingerprint of the show's content at
mark time: when a row of the show is added, removed, replaced or re-ordered, the
fingerprint no longer matches and the mark is not reported (Lee: "should clear itself").

The fingerprint leaves the air times out on purpose. A rebuild of an EARLIER show moves
every later ORA by a few frames; that is not a change to this show's content, and the
row's derived state text still reports any timing problem.

One shared table on the Etere SQL Server (the Bee, the Jumpbox and the local servers all
read it): `scripts/setup_finish_marks_table.py`. A server that runs before the table
exists reports no marks and refuses a new mark with that script's name.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from .finish_plan import FPS

TABLE = "dbo.CTV_FinishHandMarks"
KEEP_DAYS = 30  # marks for older schedule days are purged on the next mark

DDL = f"""IF OBJECT_ID('{TABLE}', 'U') IS NULL
CREATE TABLE {TABLE} (
    COD_USER    INT           NOT NULL,   -- market (TPALINSE.COD_USER)
    DATA        DATE          NOT NULL,   -- broadcast day
    LO_FRAMES   INT           NOT NULL,   -- the show's grid start, frame-of-day
    HI_FRAMES   INT           NOT NULL,
    MARKED_BY   NVARCHAR(60)  NOT NULL,
    MARKED_AT   DATETIME      NOT NULL CONSTRAINT DF_CTV_FinishHandMarks_at DEFAULT GETDATE(),
    FINGERPRINT CHAR(40)      NOT NULL,   -- sha1 of the show's rows at mark time
    CONSTRAINT PK_CTV_FinishHandMarks PRIMARY KEY (COD_USER, DATA, LO_FRAMES)
)"""


class MarksTableMissing(RuntimeError):
    def __init__(self):
        super().__init__(
            f"{TABLE} does not exist yet; run scripts/setup_finish_marks_table.py once"
        )


def window_key(lo: float) -> int:
    """The show's grid start as a whole frame-of-day: the key the page and the day list share."""
    return int(round(lo * FPS))


def fingerprint(evs) -> str:
    """sha1 over the show's rows in playlist order: row id, asset, length in frames.

    A row added, removed, replaced (new asset on the same row or a new row) or moved to
    another position changes it. Air times are not part of it (module docstring)."""
    h = hashlib.sha1()
    for e in evs:
        h.update(f"{e.id}:{e.filmati}:{int(round(e.dur * FPS))};".encode())
    return h.hexdigest()


def table_exists(cur) -> bool:
    cur.execute(f"SELECT OBJECT_ID('{TABLE}', 'U')")
    row = cur.fetchone()
    return bool(row and row[0])


def fmt_at(at: datetime) -> str:
    """'11/19 9:42 PM' from fields (no strftime %-: the servers run on Windows)."""
    h12 = at.hour % 12 or 12
    return f"{at.month}/{at.day} {h12}:{at.minute:02d} {'AM' if at.hour < 12 else 'PM'}"


def load_marks(cur, market: int, date: str) -> dict[int, dict]:
    """{LO_FRAMES: {by, at, fingerprint}} for one market-day; {} when the table is not there."""
    if not table_exists(cur):
        return {}
    cur.execute(
        f"SELECT LO_FRAMES, RTRIM(MARKED_BY), MARKED_AT, FINGERPRINT FROM {TABLE} WITH (NOLOCK)"
        " WHERE COD_USER=%s AND DATA=%s",
        (market, date),
    )
    return {
        int(r[0]): {"by": r[1], "at": fmt_at(r[2]), "fingerprint": (r[3] or "").strip()}
        for r in cur.fetchall()
    }


def hand_for(marks: dict[int, dict], lo: float, evs) -> dict | None:
    """The mark to show on a show's row, or None when there is none or the show changed."""
    m = marks.get(window_key(lo))
    if not m or m["fingerprint"] != fingerprint(evs):
        return None
    return {"by": m["by"], "at": m["at"]}


def set_mark(cur, market: int, date: str, lo: float, hi: float, by: str, evs) -> dict:
    """Mark one show done by hand as it stands now. The caller commits."""
    by = (by or "").strip()[:60]
    if not by:
        raise ValueError("a name is required to mark a show done by hand")
    if not table_exists(cur):
        raise MarksTableMissing()
    key = window_key(lo)
    cur.execute(
        f"DELETE FROM {TABLE} WHERE (COD_USER=%s AND DATA=%s AND LO_FRAMES=%s)"
        f" OR DATA < DATEADD(day, -{KEEP_DAYS}, CAST(GETDATE() AS DATE))",
        (market, date, key),
    )
    cur.execute(
        f"INSERT INTO {TABLE} (COD_USER, DATA, LO_FRAMES, HI_FRAMES, MARKED_BY, FINGERPRINT)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (market, date, key, window_key(hi), by, fingerprint(evs)),
    )
    cur.execute(
        f"SELECT RTRIM(MARKED_BY), MARKED_AT FROM {TABLE}"
        " WHERE COD_USER=%s AND DATA=%s AND LO_FRAMES=%s",
        (market, date, key),
    )
    row = cur.fetchone()
    return {"by": row[0], "at": fmt_at(row[1])}


def clear_mark(cur, market: int, date: str, lo: float) -> None:
    """Remove a show's mark (no-op when there is none). The caller commits."""
    if not table_exists(cur):
        return
    cur.execute(
        f"DELETE FROM {TABLE} WHERE COD_USER=%s AND DATA=%s AND LO_FRAMES=%s",
        (market, date, window_key(lo)),
    )
