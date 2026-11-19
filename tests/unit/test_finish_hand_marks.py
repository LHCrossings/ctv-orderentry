"""Fill & Finish "done by hand" marks (Ashe 11/19).

An operator marks a show scheduled by hand; the row turns yellow for everyone. Lee's
three rules: the Finish button STAYS on a marked show, the mark names who set it, and
the mark clears itself when the show is changed afterwards.
"""

import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from src.business_logic.services import finish_marks as fm  # noqa: E402
from src.business_logic.services.finish_plan import FPS, Ev  # noqa: E402
from web.routes import finish  # noqa: E402

PAGE = (ROOT / "src/web/templates/master_control/finish.html").read_text()
LO, HI = 21 * 3600.0, 22.5 * 3600.0


def ev(i, filmati, dur=600.0, ora=LO, newtype="PGM"):
    return Ev(i, ora, dur, newtype, "", filmati, f"asset {filmati}", None)


def show():
    return [ev(1, 500), ev(2, 900, 30.0, newtype="COM"), ev(3, 501), ev(4, 77, 6.0, newtype="ID")]


class Cur:
    """A cursor that answers the table check and records every statement."""

    def __init__(self, exists=True, rows=()):
        self.exists, self.rows, self.sql = exists, list(rows), []
        self._last = ""

    def execute(self, sql, params=()):
        self.sql.append((" ".join(sql.split()), tuple(params)))
        self._last = sql

    def fetchone(self):
        if "OBJECT_ID" in self._last:
            return (12345,) if self.exists else (None,)
        return ("Ashe", datetime(2026, 11, 19, 21, 42))

    def fetchall(self):
        return self.rows


# --- the fingerprint decides when a mark clears itself ---------------------------------


def test_the_same_rows_give_the_same_fingerprint():
    assert fm.fingerprint(show()) == fm.fingerprint(show())


def test_air_times_are_not_part_of_the_fingerprint():
    # a rebuild of an earlier show moves every later ORA; this show did not change
    later = [ev(e.id, e.filmati, e.dur, e.ora + 2.5, e.newtype) for e in show()]
    assert fm.fingerprint(later) == fm.fingerprint(show())


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s + [ev(5, 600, 60.0, newtype="PER")],  # Finish added a PI
        lambda s: s[:-1],  # a row was removed
        lambda s: [s[0], s[1], ev(3, 777), s[3]],  # a file replaced on the same row
        lambda s: [s[0], s[1], ev(9, 501), s[3]],  # the piece was re-placed (new row)
        lambda s: [s[0], s[2], s[1], s[3]],  # the order changed
        lambda s: [ev(1, 500, 598.0), s[1], s[2], s[3]],  # a length changed
    ],
)
def test_a_change_to_the_show_changes_the_fingerprint(change):
    assert fm.fingerprint(change(show())) != fm.fingerprint(show())


def test_a_mark_shows_only_while_the_show_is_the_one_that_was_marked():
    marks = {
        fm.window_key(LO): {
            "by": "Ashe",
            "at": "11/19 9:42 PM",
            "fingerprint": fm.fingerprint(show()),
        }
    }
    assert fm.hand_for(marks, LO, show()) == {"by": "Ashe", "at": "11/19 9:42 PM"}
    assert fm.hand_for(marks, LO, show()[:-1]) is None  # changed after the mark
    assert fm.hand_for(marks, HI, show()) is None  # another show
    assert fm.hand_for({}, LO, show()) is None


def test_the_key_is_the_grid_start_in_whole_frames():
    assert fm.window_key(LO) == int(round(LO * FPS))
    assert fm.window_key(LO) != fm.window_key(LO + 6)  # a 6 s offset is another key


# --- the store -------------------------------------------------------------------------


def test_time_is_formatted_from_fields_for_windows():
    assert fm.fmt_at(datetime(2026, 11, 19, 21, 42)) == "11/19 9:42 PM"
    assert fm.fmt_at(datetime(2026, 1, 5, 0, 7)) == "1/5 12:07 AM"
    assert fm.fmt_at(datetime(2026, 1, 5, 12, 0)) == "1/5 12:00 PM"


def test_a_server_without_the_table_reports_no_marks_and_reads_nothing_else():
    cur = Cur(exists=False)
    assert fm.load_marks(cur, 1, "2026-11-19") == {}
    assert len(cur.sql) == 1 and "OBJECT_ID" in cur.sql[0][0]


def test_load_marks_reads_one_market_day():
    at = datetime(2026, 11, 19, 21, 42)
    cur = Cur(rows=[(fm.window_key(LO), "Ashe", at, "ab" * 20)])
    marks = fm.load_marks(cur, 9, "2026-11-19")
    assert marks == {
        fm.window_key(LO): {"by": "Ashe", "at": "11/19 9:42 PM", "fingerprint": "ab" * 20}
    }
    sql, params = cur.sql[-1]
    assert "COD_USER=%s AND DATA=%s" in sql and params == (9, "2026-11-19")
    assert "NOLOCK" in sql  # the day list never waits on a mark being written


def test_set_mark_replaces_the_show_s_row_and_stores_the_fingerprint():
    cur = Cur()
    assert fm.set_mark(cur, 9, "2026-11-19", LO, HI, "  Ashe ", show()) == {
        "by": "Ashe",
        "at": "11/19 9:42 PM",
    }
    delete = next(s for s in cur.sql if s[0].startswith("DELETE"))
    insert = next(s for s in cur.sql if s[0].startswith("INSERT"))
    assert delete[1] == (9, "2026-11-19", fm.window_key(LO))
    assert insert[1] == (
        9,
        "2026-11-19",
        fm.window_key(LO),
        fm.window_key(HI),
        "Ashe",
        fm.fingerprint(show()),
    )
    assert all("TPALINSE" not in s[0].upper() for s in cur.sql)  # the playlist is never written


def test_set_mark_needs_a_name_and_the_table():
    with pytest.raises(ValueError):
        fm.set_mark(Cur(), 9, "2026-11-19", LO, HI, "   ", show())
    with pytest.raises(fm.MarksTableMissing, match="setup_finish_marks_table.py"):
        fm.set_mark(Cur(exists=False), 9, "2026-11-19", LO, HI, "Ashe", show())


def test_clear_mark_removes_one_show_only():
    cur = Cur()
    fm.clear_mark(cur, 9, "2026-11-19", LO)
    sql, params = cur.sql[-1]
    assert sql.startswith("DELETE") and "LO_FRAMES=%s" in sql
    assert params == (9, "2026-11-19", fm.window_key(LO))


def test_the_ddl_is_idempotent_and_keyed_on_market_day_show():
    assert "IF OBJECT_ID('dbo.CTV_FinishHandMarks', 'U') IS NULL" in fm.DDL
    assert "PRIMARY KEY (COD_USER, DATA, LO_FRAMES)" in fm.DDL


# --- the day list, the endpoint and the page ---------------------------------------------


def test_the_day_list_reports_the_mark_and_the_writer_stays_off_the_playlist():
    src = (ROOT / "src/business_logic/services/finish_service.py").read_text()
    assert '"hand": hand_for(marks, p["lo"], evs)' in src
    body = src[src.index("def mark_window") : src.index("def apply_window(")]
    assert "set_mark(" in body and "clear_mark(" in body and "conn.commit()" in body
    assert "UPDATE" not in body and "_insert_event" not in body


def test_mark_body_defaults_to_setting_the_mark():
    b = finish.FinishMarkBody(market="MMT", date="2026-11-19", lo=LO, hi=HI, by="Ashe")
    assert b.on is True
    assert finish.FinishMarkBody(market="MMT", date="2026-11-19", lo=LO, hi=HI, on=False).by == ""


def test_a_marked_show_keeps_its_finish_button():
    # Lee 11/19: "I would want the button to still be there just in case"
    can = PAGE[PAGE.index("const canFinish = p =>") :].split("\n", 1)[0]
    assert "hand" not in can
    header = PAGE[PAGE.index("function headerHtml") : PAGE.index("function lastOperator")]
    assert "const fin = canFinish(p);" in header
    assert 'id="prg-apply-${pi}" style="display:${fin || inflight.has(p.lo)' in header


def test_the_page_marks_names_and_warns():
    assert "'/api/master-control/finish/mark'" in PAGE
    assert "${p.hand ? 'hand' : ''}" in PAGE and ".prg-block.hand .prg-header" in PAGE
    assert "done by hand · ${esc(p.hand.by)} · ${esc(p.hand.at)}" in PAGE
    # Finish on a marked show says whose hand work it changes, single and batch
    assert "${verb} changes what was set by hand." in PAGE
    assert "⚠ ${handWarning(p)}` : ''}`).join" in PAGE
    # the handler gets an index, never interpolated text (7/20 inline-onclick lesson)
    assert "markHand(${pi}, true)" in PAGE and "markHand(${pi}, false)" in PAGE
