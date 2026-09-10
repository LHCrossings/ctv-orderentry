"""Fill & Finish — READ-ONLY planner prototype (spec: tasks/finish-hour.md).

Given a market, date and hour window, load the packed TPALINSE timeline, strip
any existing PI/PSA/ID fill, recompute what Finish would place, and print the
plan next to what is actually there. Writes NOTHING.

    uv run python3 scripts/finish_plan.py --market 1 --date 2026-08-27 --hour 8
(the script is a thin wrapper around this module)
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field

from browser_automation.etere_direct_client import connect

from .filler_rotation import FILLER_CODE_PREFIXES, POOL_PATTERNS, active_pool

FPS = 29.97
ID_ASSET = {4: 67911, 7: 67909, 10: 83129}  # OTA markets; everyone else generic
ID_GENERIC = 67910
# The once-daily FCC ID (children's-records notice) the Daily Programming sweep places in
# the last COMS break before midnight on the OTA markets. Lee 9/7: "FCC ID requirements
# take precedence over that final hour regular station ID" — in the hour that ends at
# midnight the FCC ID IS the hour's ID (seated last, cut by the midnight F event like any
# ID), never a second generic one beside it. An FCC ID master control placed in any other
# hour is accepted as that hour's ID too — one ID per hour, no churn.
FCC_ID_ASSET = {4: 142948, 7: 142947, 10: 83128}
MIDNIGHT = 24 * 3600.0
ID_MIN_AIR = 5.0
ID_TARGET_MAX = 10.0
FINAL_BREAK_MAX = 150.0  # 2:30 — Lee
FINAL_BREAK_SPILL = 180.0  # ≥3:00 → spill PIs into interior breaks
PI_RE = re.compile(r"^(PI|PSA)-(\d{3})-(\d{3})", re.I)


def hms(sec: float) -> str:
    sec = max(0.0, sec)
    return f"{int(sec // 3600):02d}:{int(sec % 3600 // 60):02d}:{sec % 60:05.2f}"


def mmss(sec: float) -> str:
    sign = "-" if sec < 0 else ""
    sec = abs(sec)
    return f"{sign}{int(sec // 60)}:{sec % 60:05.2f}"


@dataclass
class Ev:
    id: int
    ora: float  # seconds from 00:00 (broadcast frame-of-day / fps)
    dur: float
    newtype: str
    event_type: str
    filmati: int
    desc: str
    contract_line: int | None  # trafficPalinse.ID_ContrattiRighe (-1 = freestanding)
    # trafficPalinse.offset: frame-of-day of the traffic break the row is BOOKED into —
    # the block column Executive Editor shows. None = unbooked (Finish's own fill, NOOPs,
    # hand-dropped rows). Decides which window owns the row (Maija 9/10).
    blk: int | None = None
    code: str = ""  # TPALINSE.COD_PROGRA

    @property
    def end(self) -> float:
        return self.ora + self.dur

    @property
    def is_program(self) -> bool:
        return self.newtype == "PGM"

    @property
    def is_filler(self) -> bool:
        """A language filler program piece (K-FILLER, CHINESEFILLER, UNIAE/UNIAM) — the one
        piece of a show Finish may swap for a shorter one when program + paid overrun."""
        return self.is_program and self.code.upper().startswith(FILLER_CODE_PREFIXES)

    @property
    def campaign(self) -> str | None:
        m = PI_RE.match(self.desc)
        return f"{m.group(1).upper()}-{m.group(2)}" if m else None

    @property
    def is_fill(self) -> bool:
        """Existing filler we would own: PI/PSA/ID with no real contract line."""
        if self.newtype == "ID":
            return True
        if self.newtype in ("PER", "PSA") and (self.contract_line in (None, -1)):
            return True
        return False


@dataclass
class Filler:
    filmati: int
    desc: str
    dur: float
    kind: str  # PI | PSA
    campaign: str
    last_aired: float  # seconds since epoch-ish; lower = longer ago


@dataclass
class Break:
    after_piece_idx: int  # index of the program piece this break follows
    items: list = field(default_factory=list)  # Ev or Filler (planned)

    @property
    def length(self) -> float:
        return sum(i.dur for i in self.items)

    def campaign_ok(self, f: Filler) -> bool:
        """Never the :30 and :60 (or any two cuts) of the same campaign in the SAME
        BREAK (Lee 8/28). Anywhere else in the show is fine — :30s are scarce and
        rotate more often, so the same file may well air twice in one show."""
        return not any(getattr(it, "campaign", None) == f.campaign for it in self.items)


def load_day(cur, market: int, date: str) -> list[tuple]:
    """Every live TPALINSE row of the broadcast day, in playlist (XORDER) order."""
    cur.execute(
        """
        SELECT t.ID_TPALINSE, t.ORA, t.DURATION, t.NEWTYPE, t.EVENT_TYPE, t.ID_FILMATI,
               ISNULL(f.DESCRIZIO,''), tp.ID_ContrattiRighe, t.XORDER, tp.offset,
               RTRIM(ISNULL(t.COD_PROGRA,''))
        FROM TPALINSE t
        LEFT JOIN FILMATI f ON f.ID_FILMATI = t.ID_FILMATI
        LEFT JOIN trafficPalinse tp ON tp.id_tpalinse = t.ID_TPALINSE
        WHERE t.COD_USER=%s AND t.DATA=%s AND t.LIVELLO=0
        ORDER BY t.XORDER, t.ORA
        """,
        (market, date),
    )
    rows, seen = [], set()
    for r in cur.fetchall():
        if r[0] in seen:
            continue
        seen.add(r[0])
        rows.append(r)
    return rows


def _ev(r) -> Ev:
    return Ev(
        r[0],
        r[1] / FPS,
        (r[2] or 0) / FPS,
        str(r[3]).strip(),
        str(r[4] or "").strip(),
        r[5],
        r[6],
        r[7],
        r[9] if len(r) > 9 else None,
        str(r[10]).strip() if len(r) > 10 and r[10] is not None else "",
    )


def _booked_in(r, lo_f: int, hi_f: int) -> bool | None:
    """Is the row booked into a traffic break inside [lo, hi)? trafficPalinse.offset is
    the nominal frame-of-day of the segment the scheduler (or a hand placement) booked
    the row under — Executive Editor's block column. True/False for a booked row, None
    for an unbooked one (Finish's own fill, NOOPs, hand-dropped IDs)."""
    off = r[9] if len(r) > 9 else None
    if off is None:
        return None
    return lo_f - FPS <= off < hi_f - FPS


def window_from_day(rows: list[tuple], lo: float, hi: float) -> list[Ev]:
    """Events of one window: the rows BOOKED into its traffic blocks, plus the unbooked
    rows (Finish's own fill, NOOPs) that sit with them in playlist order.

    The block a row is booked into (trafficPalinse.offset, EE's block column) is the
    truth about which show it belongs to — never its clock position, which drifts as
    the hour packs (Maija 9/10: DAL 17:30 DART :15 booked in the 17:30 block sat at
    18:00:14 and was cut as "the next hour's", so Finish seated the ID ahead of it;
    the PIs booked in DAL's empty 21:30 block sat after the 20:00 show's last piece
    and were stripped as "this window's fill", emptying the block in EE). Anchors and
    ORA only decide the UNBOOKED rows:
      * walk XORDER from this window's F anchor; stop at the next F, at a program piece
        booked past `hi`, or at unbooked program/NOOP content past `hi`
      * a booked row of another window is skipped — and once one has been seen, unbooked
        fill past `hi` behind it is that window's hand fill, not ours
      * an unbooked paid row past `hi` with no F anchor at `hi` is the next hour's (old
        rule, kept for rows with no trafficPalinse — a ghost spot)
    Without an F anchor at `lo` the fallback is the ORA cut for unbooked rows plus every
    row booked inside the window wherever it sits.
    """
    lo_f, hi_f = int(lo * FPS), int(hi * FPS)

    def _is_f(r) -> bool:
        return str(r[4] or "").strip() == "F"

    start = next((i for i, r in enumerate(rows) if _is_f(r) and abs(r[1] - lo_f) <= FPS), None)
    anchor_at_hi = any(_is_f(r) and abs(r[1] - hi_f) <= FPS for r in rows)
    if start is None:
        sel = [
            r
            for r in rows
            if _booked_in(r, lo_f, hi_f) is True
            or (_booked_in(r, lo_f, hi_f) is None and lo_f <= r[1] < hi_f)
        ]
        sel.sort(key=lambda r: (r[1], r[8]))
        return [_ev(r) for r in sel]
    sel, seen_foreign = [rows[start]], False
    for r in rows[start + 1 :]:
        if _is_f(r):
            break
        own = _booked_in(r, lo_f, hi_f)
        kind = str(r[3]).strip()
        if own is False:
            if kind == "PGM" and r[9] >= hi_f - FPS:
                break  # the next show's piece: nothing behind it is ours
            seen_foreign = True
            continue
        if own is True:
            sel.append(r)
            continue
        ev = _ev(r)
        past_hi = r[1] >= hi_f - FPS
        if past_hi and kind in ("PGM", "NOOP"):
            break
        if past_hi and not anchor_at_hi and not ev.is_fill and not ev.is_program:
            break  # unbooked paid row past the top with no anchor: the next hour's
        if past_hi and seen_foreign and ev.is_fill:
            continue  # hand fill behind the next block's booked rows is that block's
        sel.append(r)
    return [_ev(r) for r in sel]


def load_window(cur, market: int, date: str, lo: float, hi: float) -> list[Ev]:
    return window_from_day(load_day(cur, market, date), lo, hi)


def day_programs(rows: list[tuple]) -> list[dict]:
    """Program windows of the day = consecutive EVENT_TYPE='F' anchors (a fixed-time
    event starts a program; the next one ends it). Title = first non-bumper PGM."""
    anchors = [i for i, r in enumerate(rows) if str(r[4] or "").strip() == "F"]
    out = []
    for n, i in enumerate(anchors):
        j = anchors[n + 1] if n + 1 < len(anchors) else len(rows)
        lo = rows[i][1] / FPS
        hi = rows[j][1] / FPS if j < len(rows) else lo + 3600.0
        pgms = [r for r in rows[i:j] if str(r[3]).strip() == "PGM"]
        title = next(
            (r[6] for r in pgms if "BUMP" not in (r[6] or "").upper()), pgms[0][6] if pgms else ""
        )
        out.append({"lo": lo, "hi": hi, "title": title, "n_events": j - i})
    return out


def load_inventory(cur, market: int, date: str) -> list[Filler]:
    cur.execute(
        """
        SELECT f.ID_FILMATI, f.DESCRIZIO, f.DURATA,
               ISNULL((SELECT MAX(CAST(t.DATA AS float) * 86400 + t.ORA / 29.97) FROM TPALINSE t
                       WHERE t.ID_FILMATI = f.ID_FILMATI AND t.COD_USER = %s AND t.LIVELLO = 0
                         AND t.DATA BETWEEN DATEADD(day,-14,%s) AND %s), 0)
        FROM FILMATI f
        WHERE (f.DESCRIZIO LIKE 'PI-%%' OR f.DESCRIZIO LIKE 'PSA-%%') AND f.DESCRIZIO NOT LIKE 'DO NOT%%'
          AND (f.DATA_SCAD IS NULL OR f.DATA_SCAD > GETDATE()) AND f.DURATA < 29.97*120
        """,
        (market, date, date),
    )
    out = []
    for r in cur.fetchall():
        m = PI_RE.match(r[1] or "")
        if not m:
            continue
        out.append(
            Filler(
                r[0],
                r[1],
                r[2] / FPS,
                m.group(1).upper(),
                f"{m.group(1).upper()}-{m.group(2)}",
                float(r[3]),
            )
        )
    return out


def pool(inv: list[Filler], kind: str, sec: int) -> list[Filler]:
    """Least-recently-aired first."""
    return sorted(
        [f for f in inv if f.kind == kind and abs(f.dur - sec) <= 0.5], key=lambda f: f.last_aired
    )


def _is_pi(x, sec: int) -> bool:
    kind = getattr(x, "kind", None) or (
        "PI" if getattr(x, "newtype", "") == "PER" else getattr(x, "newtype", "")
    )
    return kind == "PI" and abs(x.dur - sec) <= 0.5


def id_asset_for(market: int, hour_end: float) -> int:
    """The ID Finish plants at the end of a window: the FCC asset for an OTA market's
    pre-midnight hour (so the daily sweep, which dedupes on any live copy of the asset,
    has nothing left to add), else the market's regular one."""
    fcc = FCC_ID_ASSET.get(market)
    if fcc and abs(hour_end - MIDNIGHT) <= 1.0:
        return fcc
    return ID_ASSET.get(market, ID_GENERIC)


def accepted_id_assets(market: int) -> set[int]:
    """Every asset that counts as this market's hour ID (regular + FCC)."""
    out = {ID_ASSET.get(market, ID_GENERIC)}
    if market in FCC_ID_ASSET:
        out.add(FCC_ID_ASSET[market])
    return out


def is_end_game(x) -> bool:
    """PSA or ID — the top-of-hour end game, budgeted by `end_reserve` in plan()."""
    kind = getattr(x, "kind", None) or getattr(x, "newtype", "")
    return kind in ("PSA", "ID")


def packed_remainder(evs: list[Ev], hour_end: float) -> float:
    """Seconds left before `hour_end` once every program, paid spot and existing
    PI/PSA in the window has aired back-to-back from the first program piece.
    ID rows and NOOP gap-fillers are empty time, not content. Negative = overage."""
    pieces = [e for e in evs if e.is_program]
    if not pieces:
        return hour_end
    return hour_end - pieces[0].ora - sum(e.dur for e in evs if e.newtype not in ("ID", "NOOP"))


def missing_pieces(cur, evs: list[Ev]) -> dict[str, list[str]]:
    """Catalog pieces of each show in the window that are NOT placed: {base: [letters]}.

    A show's pieces share a base code and differ by a trailing letter (THEPOINT090926A..D);
    the FILMATI catalog says how many there are. Fillers and bumpers carry no letter and
    are skipped. THIS is what "programming placed" means — not the size of the remainder:
    To the Point runs 46:00 in a 60:00 slot and Vietnamese Past & Present 18:00 in 30:00,
    and both read "programming not placed" on a 5-minute remainder rule (Maija 9/10)."""
    present: dict[str, set[str]] = {}
    for e in evs:
        if not e.is_program or e.is_filler or "BUMP" in e.desc.upper():
            continue
        c = e.code.strip()
        if not (c[-1:].isalpha() and c[-1:].isupper()):
            continue
        present.setdefault(c[:-1], set()).add(c[-1])
    out: dict[str, list[str]] = {}
    for base, letters in present.items():
        pat = base.replace("[", "[[]").replace("%", "[%]").replace("_", "[_]") + "_"
        cur.execute(
            """SELECT RTRIM(COD_PROGRA) FROM FILMATI WITH(NOLOCK)
               WHERE NEWTYPE='PGM' AND COD_PROGRA LIKE %s
                 AND COD_PROGRA NOT LIKE '%%DO NOT USE%%' AND DESCRIZIO NOT LIKE '%%DO NOT USE%%'
                 AND COD_PROGRA NOT LIKE '%%HIATUS%%'""",
            (pat,),
        )
        catalog = {
            r[0][-1]
            for r in cur.fetchall()
            if len(r[0]) == len(base) + 1 and r[0][-1].isalpha() and r[0][-1].isupper()
        }
        miss = sorted(catalog - letters)
        if miss:
            out[base] = miss
    return out


# filler code prefix -> filler_rotation pool key (same language, same shows)
FILLER_POOL = {
    "K-FILLER": "korean",
    "CHINESEFILLER": "chinese",
    "UNIAM": "chinese",
    "UNIAE": "filipino",
}


def filler_swaps(cur, evs: list[Ev], hour_end: float) -> list[tuple[Ev, dict]]:
    """Overrun cure (Maija 9/10, Korean Drama): program + paid spill past the slot and the
    window holds a language filler piece → swap it for the LONGEST filler of the same pool
    that lets the ID land (program + paid end ≥ ID_MIN_AIR before the top), last filler
    first, never a code already in the window. Returns (piece, {fid, code, frames}) pairs;
    plan_window plans with the new duration and apply_window re-points the row."""
    hard = packed_remainder([e for e in evs if not e.is_fill], hour_end)
    if hard >= ID_MIN_AIR:
        return []
    present = {e.code.upper() for e in evs if e.is_program}
    swaps: list[tuple[Ev, dict]] = []
    for f in reversed([e for e in evs if e.is_filler]):
        key = next((k for p, k in FILLER_POOL.items() if f.code.upper().startswith(p)), None)
        if key is None:
            continue
        budget = f.dur + hard - ID_MIN_AIR  # the most the replacement may run
        fits = [
            c
            for c in active_pool(cur, POOL_PATTERNS[key])
            if c["frames"] and c["frames"] / FPS <= budget and c["code"].upper() not in present
        ]
        if not fits:
            continue
        best = max(fits, key=lambda c: c["frames"])
        swaps.append((f, best))
        present.add(best["code"].upper())
        hard += f.dur - best["frames"] / FPS
        if hard >= ID_MIN_AIR:
            break
    return swaps


def plan(evs: list[Ev], inv: list[Filler], hour_end: float, market: int) -> tuple[list, list[str]]:
    """Fix it or finish it (Lee 8/28): existing fill stays as given; remove only
    what the end game needs (spill-over first, final break next, then the
    longest interior break), move a PI only when the final break would be the
    longest, then add PI → PSA → ID. Existing ID rows are re-placed (same asset)."""
    notes: list[str] = []
    old_ids = [e for e in evs if e.newtype == "ID"]
    # NOOPs are Etere/EE gap-fillers, i.e. empty time — never content (MMT bare test 8/28)
    kept = [e for e in evs if e.newtype not in ("ID", "NOOP")]
    pieces = [e for e in kept if e.is_program]

    def _is_bump(p: Ev, kind: str) -> bool:
        return "BUMP" in p.desc.upper() and kind in p.desc.upper()

    breaks: list[Break] = []
    for i in range(len(pieces)):
        lo = pieces[i].end
        hi = pieces[i + 1].ora if i + 1 < len(pieces) else hour_end
        items = [e for e in kept if not e.is_program and lo - 0.5 <= e.ora < hi]
        # anything after the last piece belongs to the final break even if it spilled past hour_end
        if i == len(pieces) - 1:
            items = [e for e in kept if not e.is_program and e.ora >= lo - 0.5]
        # A break exists after the last piece, wherever spots already sit, and between
        # two story pieces (never open-bump→story or story→close-bump) even if it is
        # empty right now — a break that held only fillers must not vanish with them.
        structural = (
            i + 1 < len(pieces)
            and not _is_bump(pieces[i], "OPEN")
            and not _is_bump(pieces[i + 1], "CLOSE")
        )
        if items or i == len(pieces) - 1 or structural:
            breaks.append(Break(i, items))
    final = breaks[-1]
    interior = breaks[:-1]
    R = packed_remainder(evs, hour_end)
    notes.append(
        f"existing fill kept as given ({sum(1 for e in kept if e.is_fill)} rows); packed remainder = {mmss(R)}"
        + (f"; {len(old_ids)} existing ID" if old_ids else "")
    )

    used: set[int] = {e.filmati for e in kept if e.is_fill}
    deletes: list[str] = []
    moves: list[str] = []

    # ── FIX phase: remove existing fill until the ID can land (R ≥ 5s) ──
    def removable():
        """Fewest edits: the smallest single existing fill item that covers the
        deficit (final break first, then longest interior); if none covers it,
        the largest available so the next pass gets closer."""
        need = ID_MIN_AIR - R
        cands = []
        for order, b in enumerate([final] + sorted(interior, key=lambda b: -b.length)):
            for pos, x in enumerate(b.items):
                if isinstance(x, Ev) and x.is_fill:
                    cands.append((b, x, order, -pos))
        if not cands:
            return None, None
        enough = [c for c in cands if c[1].dur >= need]
        if enough:
            b, x, *_ = min(enough, key=lambda c: (c[1].dur, c[2], c[3]))
        else:
            b, x, *_ = max(cands, key=lambda c: (c[1].dur, -c[2], -c[3]))
        return b, x

    while R < ID_MIN_AIR:
        b, x = removable()
        if x is None:
            break
        b.items.remove(x)
        R += x.dur
        deletes.append(f"remove {x.desc[:28]} ({mmss(x.dur)}) from break {b.after_piece_idx}")

    # ── final break must never be the longest: move its PIs into the shortest interior break ──
    # The reserve stands in for the end game (a PSA + the 25s ID). Measure the final
    # break's CORE against it — PIs and paid spots only — never the PSAs already sitting
    # there, or a finished hour re-reads as "final break 15s too long → move a PI"
    # (LAX MBuhay 9/4 18:00, Maija: "doesn't gray out after Finished").
    end_reserve = 25.0 + 15.0

    def final_eff() -> float:
        return sum(x.dur for x in final.items if not is_end_game(x)) + end_reserve

    while interior and final.items:
        longest_interior = max(b.length for b in interior)
        if final_eff() <= max(FINAL_BREAK_MAX, 0) and final_eff() <= longest_interior:
            break
        pis = [x for x in final.items if isinstance(x, Ev) and x.is_fill and x.newtype == "PER"]
        if not pis:
            break
        x = pis[-1]
        target = min(interior, key=lambda b: b.length)
        final.items.remove(x)
        target.items.append(x)
        moves.append(f"move {x.desc[:28]} ({mmss(x.dur)}) final → break {target.after_piece_idx}")

    def take(kind, sec, brk: Break):
        # Fair rotation: prefer a file not yet in this show, but a repeat within the
        # show is allowed (Lee 8/28) — the only hard rule is campaign_ok per break.
        for allow_repeat in (False, True):
            for f in pool(inv, kind, sec):
                if (f.filmati in used and not allow_repeat) or not brk.campaign_ok(f):
                    continue
                used.add(f.filmati)
                return f
        return None

    # ── FINISH phase: PIs for evenness; final break ≤ 2:30 and never the longest ──
    while R - ID_MIN_AIR >= 30.0:
        sec = 60 if R - ID_MIN_AIR >= 60.0 else 30

        def eff(b: Break) -> float:
            return final_eff() if b is final else b.length

        longest_interior = max((b.length for b in interior), default=0.0)
        final_ok = eff(final) + sec <= min(FINAL_BREAK_MAX, longest_interior) if interior else True
        candidates = list(interior) + ([final] if final_ok else [])
        target = min(candidates, key=eff)
        f = take("PI", sec, target) or (take("PI", 30, target) if sec == 60 else None)
        if not f:
            break
        target.items.append(f)
        R -= f.dur
    # ── PSA phase: bring the pre-ID gap into [5, 10] ──
    while R > ID_TARGET_MAX:
        f = None
        for sec in (15, 10):
            if R - sec >= ID_MIN_AIR:
                f = take("PSA", sec, final)
                if f:
                    break
        if not f:
            break
        final.items.append(f)
        R -= f.dur
    swaps = []
    if R < ID_MIN_AIR:
        # swap a :30 PI → :15 + :10 PSA (+5s); else a :60 → :30 + :15 + :10
        for sec, repl in (
            (30, (("PSA", 15), ("PSA", 10))),
            (60, (("PI", 30), ("PSA", 15), ("PSA", 10))),
        ):
            done = False
            for b in [final] + interior:
                pis = [x for x in b.items if _is_pi(x, sec)]
                if pis:
                    x = pis[-1]
                    b.items.remove(x)
                    R += x.dur
                    for kind, s2 in repl:
                        g = take(kind, s2, b)
                        if g:
                            b.items.append(g)
                            R -= g.dur
                    swaps.append(
                        f"swap {x.desc[:20]} → {'+'.join(f':{s2}' for _, s2 in repl)} in break {b.after_piece_idx}"
                    )
                    done = True
                    break
            if done:
                break
    notes.extend(deletes + moves + swaps)
    id_asset = id_asset_for(market, hour_end)
    if ID_MIN_AIR <= R <= 25.5:
        final.items.append(
            Filler(id_asset, f"STATION ID (airs {R:.1f}s of 25)", 25.09, "ID", "ID", 0)
        )
        notes.append(f"ID {id_asset} placed; airs {R:.1f}s before the top-of-hour F event")
    else:
        notes.append(f"⚠ cannot land ID: pre-ID gap {R:.1f}s outside [5,25]")
    n_new = sum(1 for b in breaks for x in b.items if isinstance(x, Filler))
    notes.append(f"edits: {len(deletes)} delete, {len(moves)} move, {n_new} insert (incl. ID)")
    return breaks, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", type=int, required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--hour", type=int, required=True)
    ap.add_argument("--minutes", type=int, default=60)
    a = ap.parse_args()
    lo, hi = a.hour * 3600.0, a.hour * 3600.0 + a.minutes * 60
    conn = connect()
    cur = conn.cursor()
    evs = load_window(cur, a.market, a.date, lo, hi)
    inv = load_inventory(cur, a.market, a.date)
    print(
        f"Inventory: {len(pool(inv, 'PI', 60))}×PI:60  {len(pool(inv, 'PI', 30))}×PI:30  {len(pool(inv, 'PSA', 15))}×PSA:15  {len(pool(inv, 'PSA', 10))}×PSA:10"
    )
    print(f"\nACTUAL  ({len(evs)} events)")
    for e in evs:
        tag = "fill" if e.is_fill else ("PGM " if e.is_program else "paid")
        print(f"  {hms(e.ora)} {mmss(e.dur):>8} {e.newtype:4} {tag} {e.desc[:40]}")
    print(f"  ends {hms(evs[-1].end)}  (window end {hms(hi)})")

    breaks, notes = plan(evs, inv, hi, a.market)
    print("\nPLAN")
    for n in notes:
        print("  •", n)
    pieces = [e for e in evs if e.is_program and not e.is_fill]
    t = pieces[0].ora
    for i, p in enumerate(pieces):
        print(f"  {hms(t)} {mmss(p.dur):>8} PGM  {p.desc[:40]}")
        t += p.dur
        b = next((x for x in breaks if x.after_piece_idx == i), None)
        if b is None:
            continue
        for it in b.items:
            kind = getattr(it, "newtype", None) or getattr(it, "kind", "")
            tag = "NEW " if isinstance(it, Filler) else ("keep" if it.is_fill else "    ")
            print(f"  {hms(t)} {mmss(it.dur):>8} {kind:4} {tag} {it.desc[:40]}")
            t += it.dur
        if b.items:
            print(f"           └ break {i}: {mmss(b.length)}")
    print(f"  ends {hms(t)}  → top-of-hour F event at {hms(hi)} cuts the ID")


if __name__ == "__main__":
    main()
