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

FPS = 29.97
ID_ASSET = {4: 67911, 7: 67909, 10: 83129}  # OTA markets; everyone else generic
ID_GENERIC = 67910
ID_MIN_AIR = 5.0
ID_TARGET_MAX = 10.0
FINAL_BREAK_MAX = 150.0  # 2:30 — Lee
FINAL_BREAK_SPILL = 180.0  # ≥3:00 → spill PIs into interior breaks
PI_RE = re.compile(r"^(PI|PSA)-(\d{3})-(\d{3})", re.I)


def hms(sec: float) -> str:
    sec = max(0.0, sec)
    return f"{int(sec // 3600):02d}:{int(sec % 3600 // 60):02d}:{sec % 60:05.2f}"


def mmss(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:05.2f}"


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

    @property
    def end(self) -> float:
        return self.ora + self.dur

    @property
    def is_program(self) -> bool:
        return self.newtype == "PGM"

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
               ISNULL(f.DESCRIZIO,''), tp.ID_ContrattiRighe, t.XORDER
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
    )


def window_from_day(rows: list[tuple], lo: float, hi: float) -> list[Ev]:
    """Events of one window, walked in XORDER between the two type-F anchors.

    An ORA cut (`ORA < hour_end`) misses rows that SPILL past the top of the hour
    (SEA 8/28: PI-488-030 at 09:00:01 ahead of the 09:00 F event). The window is
    everything from this window's F event up to (not including) the next F event in
    playlist order; the ORA cut is only the fallback when no F anchor exists.
    """
    lo_f, hi_f = int(lo * FPS), int(hi * FPS)
    start = next(
        (
            i
            for i, r in enumerate(rows)
            if str(r[4] or "").strip() == "F" and abs(r[1] - lo_f) <= FPS
        ),
        None,
    )
    if start is not None:
        end = len(rows)
        for k in range(start + 1, len(rows)):
            r = rows[k]
            is_f = str(r[4] or "").strip() == "F"
            # a guide window may end before the next F anchor (12:00-13:00 inside a
            # 12:00 F ... 14:00 F span): program/NOOP content at or past `hi` is the
            # next window; spilled spots (COM/PER past `hi`) still belong to this one
            next_pgm = r[1] >= hi_f - FPS and str(r[3]).strip() in ("PGM", "NOOP")
            if is_f or next_pgm:
                end = k
                break
        sel = rows[start:end]
    else:
        sel = sorted((r for r in rows if lo_f <= r[1] < hi_f), key=lambda r: (r[1], r[8]))
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
    R = hour_end - pieces[0].ora - sum(e.dur for e in kept)
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
    end_reserve = 25.0 + 15.0
    while interior and final.items:
        longest_interior = max(b.length for b in interior)
        if (
            final.length + end_reserve <= max(FINAL_BREAK_MAX, 0)
            and final.length + end_reserve <= longest_interior
        ):
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
            return b.length + (end_reserve if b is final else 0.0)

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
    id_asset = ID_ASSET.get(market, ID_GENERIC)
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
