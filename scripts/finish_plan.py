"""Fill & Finish — READ-ONLY planner prototype (spec: tasks/finish-hour.md).

Given a market, date and hour window, load the packed TPALINSE timeline, strip
any existing PI/PSA/ID fill, recompute what Finish would place, and print the
plan next to what is actually there. Writes NOTHING.

    uv run python3 scripts/finish_plan.py --market 1 --date 2026-08-27 --hour 8
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field

sys.path.insert(0, ".")
from browser_automation.etere_direct_client import connect  # noqa: E402

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

    def campaigns_adjacent_ok(self, f: Filler) -> bool:
        last = self.items[-1] if self.items else None
        c = getattr(last, "campaign", None)
        return not (c and c == f.campaign)


def load_window(cur, market: int, date: str, lo: float, hi: float) -> list[Ev]:
    """Events of one hour window, walked in XORDER between the two type-F anchors.

    An ORA cut (`ORA < hour_end`) misses rows that SPILL past the top of the hour
    (SEA 8/28: PI-488-030 at 09:00:01 ahead of the 09:00 F event). The window is
    everything from this hour's F event up to (not including) the next F event in
    playlist order; the ORA cut is only the fallback when no F anchor exists.
    """
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
    rows = []
    seen = set()
    for r in cur.fetchall():
        if r[0] in seen:
            continue
        seen.add(r[0])
        rows.append(r)
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
            if str(rows[k][4] or "").strip() == "F":
                end = k
                break
        sel = rows[start:end]
    else:
        sel = [r for r in rows if lo_f <= r[1] < hi_f]
        sel.sort(key=lambda r: (r[1], r[8]))
    return [
        Ev(
            r[0],
            r[1] / FPS,
            (r[2] or 0) / FPS,
            str(r[3]).strip(),
            str(r[4] or "").strip(),
            r[5],
            r[6],
            r[7],
        )
        for r in sel
    ]


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


def plan(evs: list[Ev], inv: list[Filler], hour_end: float, market: int) -> tuple[list, list[str]]:
    notes: list[str] = []
    kept = [e for e in evs if not e.is_fill]
    stripped = [e for e in evs if e.is_fill]
    # Program pieces + interior breaks (COM/BNS/kept PER between pieces)
    pieces = [e for e in kept if e.is_program]
    # A break exists only where the COMS structure already has one — i.e. the gap
    # between two program pieces already holds spots (paid or Etere fill) — plus
    # the final break after the last program piece. Bumper→story and
    # close-bump→filler-program gaps are NOT breaks.
    breaks: list[Break] = []
    for i in range(len(pieces)):
        lo = pieces[i].end
        hi = pieces[i + 1].ora if i + 1 < len(pieces) else hour_end
        items = [e for e in kept if not e.is_program and lo - 0.5 <= e.ora < hi]
        had_any = any(not e.is_program and lo - 0.5 <= e.ora < hi for e in evs)
        if had_any or i == len(pieces) - 1:
            breaks.append(Break(i, items))
    final = breaks[-1]
    fixed = sum(e.dur for e in kept)
    R = hour_end - (pieces[0].ora if pieces else 0) - fixed  # true remainder once packed
    notes.append(f"stripped {len(stripped)} existing fill rows; packed remainder = {mmss(R)}")

    used: set[int] = set()

    def take(kind, sec, brk: Break):
        for f in pool(inv, kind, sec):
            if f.filmati in used or not brk.campaigns_adjacent_ok(f):
                continue
            used.add(f.filmati)
            return f
        return None

    # ── PI phase: distribute for evenness; final break ≤ 2:30 ──
    interior = breaks[:-1]
    while R - ID_MIN_AIR >= 30.0:  # largest PI that still leaves ≥5s for the ID
        # where does the next PI go? final break unless it would exceed the cap
        # Evenness (Lee): the next PI goes to the SHORTEST interior break. The
        # final break may take it only if it stays ≤ the longest interior break
        # ("the final break must never be the longest") and ≤ 2:30.
        sec = 60 if R - ID_MIN_AIR >= 60.0 else 30
        # The final break will also receive the ID (25s) and typically one PSA
        # (~15s) in the end game — count that reserve now, or it looks 40s
        # lighter than it will end up and steals PIs from interior breaks.
        end_reserve = 25.0 + 15.0

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
        for b in [final] + interior:
            pis = [
                x
                for x in b.items
                if isinstance(x, Filler) and x.kind == "PI" and abs(x.dur - 30) <= 0.5
            ]
            if pis:
                x = pis[-1]
                b.items.remove(x)
                R += x.dur
                for sec in (15, 10):
                    g = take("PSA", sec, b)
                    b.items.append(g)
                    R -= g.dur
                swaps.append(f"swapped {x.desc[:20]} → :15+:10 PSA in break {b.after_piece_idx}")
                break
        else:
            for b in [final] + interior:
                pis = [
                    x
                    for x in b.items
                    if isinstance(x, Filler) and x.kind == "PI" and abs(x.dur - 60) <= 0.5
                ]
                if pis:
                    x = pis[-1]
                    b.items.remove(x)
                    R += x.dur
                    for kind, sec in (("PI", 30), ("PSA", 15), ("PSA", 10)):
                        g = take(kind, sec, b)
                        b.items.append(g)
                        R -= g.dur
                    swaps.append(
                        f"swapped {x.desc[:20]} → :30 PI + :15 + :10 PSA in break {b.after_piece_idx}"
                    )
                    break
    notes.extend(swaps)
    id_asset = ID_ASSET.get(market, ID_GENERIC)
    if ID_MIN_AIR <= R <= 25.5:
        final.items.append(
            Filler(id_asset, f"STATION ID (airs {R:.1f}s of 25)", 25.09, "ID", "ID", 0)
        )
        notes.append(f"ID {id_asset} placed; airs {R:.1f}s before the top-of-hour F event")
    else:
        notes.append(f"⚠ cannot land ID: pre-ID gap {R:.1f}s outside [5,25]")
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
            print(f"  {hms(t)} {mmss(it.dur):>8} {kind:4}      {it.desc[:40]}")
            t += it.dur
        if b.items:
            print(f"           └ break {i}: {mmss(b.length)}")
    print(f"  ends {hms(t)}  → top-of-hour F event at {hms(hi)} cuts the ID")


if __name__ == "__main__":
    main()
