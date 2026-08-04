# Admerasia Chinese IOs — language-aware colour matching

**Page:** `/traffic/assign-assets` (drop-first auto-colour assigner)
**Trigger order:** `TV-MD26-Chinese IO-Beverages August Window` = order `11-MD10-2608CT` = contract **2999** (SEA)

---

## Problem

A Chinese IO carries **two ISCI legend blocks** (Mandarin + Cantonese) that reuse the
**identical swatch colours**. Verified on the August Window IO — 4 colours ↔ 8 ISCIs:

| Colour | Creative | Mandarin | Cantonese |
|---|---|---|---|
| (242,220,219) pink | Core Portfolio 7 Drinks :15 | MCIM106526VH | MCIC087526VH |
| (235,241,222) green | Lifestyle Joy Ride :15 | MCIM107526VH | MCIC088526VH |
| (228,223,236) purple | Red Bull Dragonberry :15 | MCIM104526VH | MCIC086526VH |
| (253,233,217) peach | Lifestyle Anthem :30 | MCIM014326VH | MCIC010326VH |

**Root cause:** `admerasia_traffic._assign_clusters()` builds a one-to-one
`palette cluster -> single ISCI` map by minimising RGB distance to the legend. With
4 colours and 8 legend rows whose colours are *exactly equal in pairs*, the
permutation search picks a language **arbitrarily** — roughly half the spots get the
wrong language's creative. Language is never modelled anywhere in the pipeline.

## Design

1. **Colour identifies the creative** (title + duration), *not* the ISCI.
2. **Language comes from the spot's own airtime** — (weekday, `TPALINSE.ORA`) against the
   **day-aware** window table. Per `lessons.md`, windows are the ground truth; program-name
   text ("Mandarin Show", "(Cantonese)") is used only as a corroborating warning.
3. **ISCI = legend[(colour cluster, language)]**.

Why the day-aware table is required: `Cantonese` is weekday-only (19:00-20:00, 23:30-23:59)
while `Mandarin` weekend runs 20:00-23:59. The day-less mirror in `language_windows.py`
overlaps (Mandarin 20:00-23:59 vs Cantonese 23:30-23:59) and cannot disambiguate.

**Verified against all 6 grid rows and all 11 real spots on contract 2999 — every one
resolves to exactly ONE language, agreeing 11/11 with the entered line descriptions
(`... CAN` / `... MAN`).**

## Steps

- [ ] 1. **Consolidate the day-aware window tables.** Move `_CTV_LANG_WINDOWS` /
      `_DAL_LANG_WINDOWS` out of `src/web/routes/orders.py` into
      `browser_automation/language_windows.py` (already the parser-side mirror), add
      `classify_language(market, weekday, frames)`. `orders.py` imports and aliases the
      old private names so **no call site changes**. Kills the third-mirror drift risk
      that `lessons.md` already warns about; keeps the existing day-less
      `check_language_window()` for the paid-line daypart validator (different job).
- [ ] 2. **Deterministic legend reader** — new `admerasia_traffic_legend.py`: for each
      ISCI token on page 1, read `(language, duration, title)` from its text line and
      sample the row's fill RGB positionally. Verified on **all 7** available Admerasia
      IOs (incl. `Taglish`, and the ISCI-before-title variant in SFO 06-MD10-2603VT).
      Normalise letter `O` -> digit `0` in the numeric body — the Beverages Launch IO
      literally writes `MCIMO46526VH`. Fall back to the existing vision legend if the
      text read finds no ISCI/language.
- [ ] 3. **Language-aware cluster map** in `resolve_traffic`: `cluster -> {language: isci}`.
      Single-language IOs collapse to one entry per cluster -> **existing behaviour and
      output unchanged** (zero regression risk for the VT/FT orders).
- [ ] 4. **Per-spot language in `match_creatives`**: after the cell/cluster is resolved,
      pick the ISCI by that spot's language. Guardrails (report, never silently assign):
      spot time in no window; cluster missing that language; per-row language coherence
      warning; keep existing duration + cell-count reconciliation.
- [ ] 5. **Route/UI** — carry `language` through the `/api/traffic/admerasia/auto-color`
      legend + summary payload so the review table shows Mandarin vs Cantonese per creative.
- [ ] 6. **Tests** — `classify_language` edges (Sat 22:30 -> Mandarin; weekday 23:30 ->
      Cantonese; weekday 19:30 -> Cantonese); legend reader across all 7 fixtures;
      `match_creatives` with colour-colliding legend; live dry-run vs contract 2999.

## Verification oracle (contract 2999)

A human already trafficked 8 of the 11 spots. The colour+language model must reproduce
them exactly:

| Date | Day | Time | Dur | Colour | Language | Expected filmati |
|---|---|---|---|---|---|---|
| 8/19 | Wed | 19:44 | 30 | peach | Cantonese | 144721 MCIC010326VH |
| 8/20 | Thu | 22:10 | 15 | green | Mandarin | *(Joy Ride — not ingested)* |
| 8/21 | Fri | 19:42 | 15 | green | Cantonese | *(Joy Ride — not ingested)* |
| 8/22 | Sat | 22:40 | 30 | peach | Mandarin | 144722 MCIM014326VH |
| 8/24 | Mon | 23:05 | 30 | peach | Mandarin | 144722 |
| 8/27 | Thu | 22:10 | 15 | green | Mandarin | *(Joy Ride — not ingested)* |
| 8/29 | Sat | 22:40 | 30 | peach | Mandarin | 144722 |
| 8/31 | Mon | 19:44 | 15 | pink | Cantonese | 144715 MCIC087526VH |
| 9/02 | Wed | 19:44 | 30 | peach | Cantonese | 144721 |
| 9/03 | Thu | 22:10 | 30 | peach | Mandarin | 144722 |
| 9/05 | Sat | 21:20 | 15 | purple | Mandarin | 144718 MCIM104526VH |

Model reproduces all 8 human assignments and predicts the 3 blocked spots are exactly
the Joy Ride ones.

## Blocker to flag (not a code issue)

**`Beverages Lifestyle Joy Ride` :15 is still not in the media library** —
`MCIM107526VH` and `MCIC088526VH` (MD seq **410**). The other 6 landed as
144715-144718 / 144721-144722 (MD seq 409/411/412). Those 3 spots on contract 2999
cannot be trafficked until Joy Ride is ingested.

## Review

All 6 steps done.

**Changed**
- `browser_automation/language_windows.py` — now holds the day-aware
  `CTV_LANG_WINDOWS_BY_DAY` / `DAL_LANG_WINDOWS_BY_DAY` (moved out of `orders.py`) plus
  `classify_language()` / `classify_language_frames()` / `_win_bounds()`.
- `browser_automation/parsers/admerasia_traffic_legend.py` — **new**; deterministic
  text + positional-swatch legend read with language, title, and O→0 ISCI repair.
- `browser_automation/parsers/admerasia_traffic.py` — `_assign_clusters_by_language()`,
  text legend preferred (vision = fallback), per-spot language on `_contract_spots`,
  row duration from the legend, `res.legend` is now dicts.
- `browser_automation/parsers/admerasia_traffic_match.py` — `_pick_isci()` + language-keyed
  cluster map, `SpotAssignment.language`, row language-coherence warning.
- `src/web/routes/orders.py` — imports the shared tables; payload carries `languages`
  and per-creative/per-spot `language`.
- `src/web/templates/traffic/asset_assignment.html` — Language column, shown only on
  multi-language IOs.
- Tests: `tests/unit/test_admerasia_chinese_language.py` (36),
  `tests/integration/test_admerasia_legend_pdf.py` (11).

**Verification**
- **Contract 2999 dry-run: 8/8 of the human's existing assignments reproduced, 0
  mismatches.** The 3 unassignable spots are exactly the Joy Ride ones, each with the
  reason `no FILMATI for ISCI MCIM107526VH` / `MCIC088526VH`.
- The OLD code on this IO would have mapped every colour to its **Mandarin** ISCI — all
  3 assignable Cantonese spots (8/19, 8/31, 9/02) would have been trafficked wrong.
- **No regression:** all 6 single-language VT/FT fixtures produce byte-identical cluster
  maps to the old path, and SFO 06-MD10-2603VT still resolves **69/69 ok=True**.
- Legend reader parses all 8 available Admerasia IOs (incl. `Taglish` and the
  ISCI-before-title variant); the Launch IO's O-for-0 and duplicate-ISCI typos are
  surfaced as warnings.
- Suite: 366 passed. The single failure
  (`test_order_scanner.py::test_scan_ignores_non_pdf_files`) is **pre-existing** —
  reproduced on a clean tree; `tests/integration/test_customer_repository.py` calls
  `load_dotenv()`, which sets `CTV_AI_FALLBACK=1` from `.env` and leaks into the unit
  tests. Not touched here.
- No new ruff findings (the 2 `I001`s in `admerasia_traffic.py` pre-exist).

**Found a bug in my own design via an invariant test:** the `_TOL_MIN = 1` slop (correct
for the daypart *containment* validator) made 20:00 Monday classify as BOTH Cantonese
and Mandarin. Point-in-time lookups are now strictly half-open, with `"23:59"` meaning
24:00 and `< 06:00` shifted +24h. Captured in `lessons.md`.

**Still blocked on media, not code:** `Beverages Lifestyle Joy Ride` :15
(`MCIM107526VH` / `MCIC088526VH`, MD seq **410**) is not ingested — Lee confirmed. Those
3 spots on contract 2999 will assign themselves once it lands and the IO is re-dropped.
