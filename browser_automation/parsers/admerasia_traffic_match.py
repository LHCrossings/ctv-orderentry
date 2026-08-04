"""
Match Admerasia scheduled spots to their creative (ISCI) by grid-cell colour.

Pure logic — no PDF/DB/vision I/O — so it is fully unit-testable. Callers supply:
  • color_grid   : ColorGrid from admerasia_traffic_color.read_color_grid
  • row_dur      : {grid_row_index: duration_sec}
  • row_window   : {grid_row_index: (win_start_frames, win_end_frames)}  (tie-break only)
  • cluster_lang_isci : {palette_cluster_index: {language|None: isci_code}}
  • flight_start : date of calendar-grid column 0
  • spots        : [{"id", "date": date, "ora": frames, "duration": frames,
                    "languages": [str]}]  (TPALINSE; `languages` from the day-aware
                   airing windows — see browser_automation/language_windows.py)
  • filmati_by_isci : {isci: {"filmati_id": int, "duration": frames}}

A grid cell (row, day) is one colour = one CREATIVE (title + length). On a Chinese IO
the Mandarin and Cantonese legend blocks reuse the SAME colours, so colour alone maps
to two ISCIs; the spot's own airtime picks the language (Cantonese is weekday-only,
Mandarin owns the weekend 20:00-23:59 slice). Single-language IOs have one ISCI per
colour and skip language resolution entirely.

Returns per-spot assignments + warnings; guardrail failures are reported, never
silently assigned.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

_FPS = 29.97
_DUR_TOL = 5           # frames; creative length must match ordered length within ±5


@dataclass
class SpotAssignment:
    tp_id: int
    filmati_id: int | None
    isci: str | None
    duration_ok: bool
    ok: bool
    reason: str = ""
    language: str | None = None


@dataclass
class MatchResult:
    assignments: list[SpotAssignment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def writable(self):
        return [a for a in self.assignments if a.ok]


def _pick_isci(lang_map, spot_languages):
    """(isci, language, reason) for a cell's colour given the spot's candidate languages.

    A single-entry map is language-agnostic (single-language IO, or the vision-legend
    fallback which cannot report language) and is used as-is."""
    if not lang_map:
        return None, None, "colour has no creative in the legend"
    if len(lang_map) == 1:
        (lang, isci), = lang_map.items()
        return isci, lang, ""
    # Colour is shared by >1 language → the spot's airtime must resolve it.
    if not spot_languages:
        return None, None, ("airtime falls in no programmed language window; colour is "
                            f"shared by {'/'.join(sorted(str(k) for k in lang_map))}")
    hits = [L for L in spot_languages if L in lang_map]
    if len(hits) == 1:
        return lang_map[hits[0]], hits[0], ""
    if not hits:
        return None, None, (f"airtime is {'/'.join(spot_languages)} but this colour only "
                            f"has {'/'.join(sorted(str(k) for k in lang_map))}")
    return None, None, (f"airtime is ambiguous between {'/'.join(hits)} — "
                        "overlapping language windows")


def match_creatives(color_grid, row_dur, row_window, cluster_lang_isci, flight_start, spots,
                    filmati_by_isci) -> MatchResult:
    """
    row_dur           : {grid_row_index: duration_sec}  (from the legend's own :15/:30)
    row_window        : {grid_row_index: (start_frames, end_frames)}  (daypart; tie-break only)
    cluster_lang_isci : {palette_cluster_index: {language|None: isci_code}}

    A spot is assigned the colour of the grid cell for the ONE program row that (a) has
    that spot's duration and (b) actually has a coloured cell on that spot's date. When
    two same-duration programs air the same day, the daypart window breaks the tie. The
    colour then gives the creative, and the spot's own language gives which language
    cut of that creative to use.
    """
    res = MatchResult()

    cell_by_rc = {(c.row, c.col): c for c in color_grid.cells}

    # Guardrail: each grid row must be single-duration (a :15 row can't hold :30 colour).
    # Uses the legend duration, so it still fires for a creative missing from FILMATI.
    row_secs = defaultdict(set)
    for c in color_grid.cells:
        for isci in (cluster_lang_isci.get(c.cluster) or {}).values():
            fil = filmati_by_isci.get(isci)
            if fil:
                row_secs[c.row].add(round(fil["duration"] / _FPS))
    for r, secs in row_secs.items():
        if len(secs) > 1:
            res.warnings.append(f"grid row {r} mixes creative durations {sorted(secs)}s — check colour legend")

    matched_counts = defaultdict(int)   # (row,col) -> spots matched, for reconciliation
    row_langs = defaultdict(set)        # (row) -> languages assigned, for coherence check

    # Process spots in time order so that when two programmes' windows overlap on a
    # day, earlier spots claim their cell first and a filled cell drops out of the
    # candidates for later spots (capacity-aware).
    for s in sorted(spots, key=lambda s: (s["date"], s["ora"])):
        dur_sec = round(s["duration"] / _FPS)
        col = (s["date"] - flight_start).days
        # rows of this duration with a coloured cell on this date that still has capacity
        cand = [r for r, d in row_dur.items()
                if d == dur_sec and (r, col) in cell_by_rc
                and matched_counts[(r, col)] < cell_by_rc[(r, col)].count]
        if len(cand) > 1:
            # two same-duration programmes on the same day → break by daypart window
            win = [r for r in cand
                   if r in row_window and row_window[r][0] <= s["ora"] < row_window[r][1]]
            if win:
                cand = win
        if len(cand) != 1:
            res.assignments.append(SpotAssignment(s["id"], None, None, False, False,
                f"spot dur={dur_sec}s on day-col {col} @{s['ora']}fr matched {len(cand)} grid rows"))
            continue
        cell = cell_by_rc[(cand[0], col)]
        langs = s.get("languages") or []
        isci, lang, why = _pick_isci(cluster_lang_isci.get(cell.cluster) or {}, langs)
        if not isci:
            res.assignments.append(SpotAssignment(s["id"], None, None, False, False, why))
            continue
        fil = filmati_by_isci.get(isci)
        if not fil:
            res.assignments.append(SpotAssignment(s["id"], None, isci, False, False,
                f"no FILMATI for ISCI {isci}", language=lang))
            continue
        # USER GUARDRAIL: assigned creative length must equal ordered spot length
        dur_ok = abs(fil["duration"] - s["duration"]) <= _DUR_TOL
        res.assignments.append(SpotAssignment(
            s["id"], fil["filmati_id"], isci, dur_ok, dur_ok,
            "" if dur_ok else f"length mismatch: creative {round(fil['duration']/_FPS)}s vs ordered {dur_sec}s",
            language=lang))
        matched_counts[(cand[0], col)] += 1
        if lang:
            row_langs[cand[0]].add(lang)

    # Coherence: one grid row is one program = one language. Mixed languages on a row
    # means a spot landed outside its program's daypart, or the row match is wrong.
    for r, langs in row_langs.items():
        if len(langs) > 1:
            res.warnings.append(
                f"grid row {r} resolved to multiple languages ({'/'.join(sorted(langs))}) "
                "— a spot may be scheduled outside its programme's daypart")

    # Reconciliation: each grid cell's printed count must equal spots matched to it
    for (r, col), cell in cell_by_rc.items():
        got = matched_counts.get((r, col), 0)
        if got != cell.count:
            res.warnings.append(
                f"row {r} day {color_grid.calendar_days[col]}: grid shows {cell.count} spot(s) "
                f"but {got} scheduled spot(s) matched")

    bad = [a for a in res.assignments if not a.ok]
    if bad:
        res.warnings.append(f"{len(bad)} spot(s) could not be assigned/validated (see per-spot reasons)")
    return res
