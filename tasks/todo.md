# Admerasia Traffic Auto-Assigner (color-match) — design

## Goal
DROP-FIRST auto-parse: drop one Admerasia McDonald's IO on the assign-traffic page
→ it detects the format, FINDS THE CONTRACT ITSELF, and auto-assigns each scheduled
spot its creative (ISCI) by matching the spot's grid-cell color to the ISCI legend.
Replaces today's "search contract → pick Admerasia from dropdown → drop IO" AND the
all-manual per-spot dropdown picking. Highest-effort orders; accuracy > cost.

## Contract auto-match (VERIFIED against the 7 entered July contracts)
PRIMARY KEY = the IO Order Number, stored verbatim on the contract in
`CONTRATTITESTATA.CUSTOMERREF` (written at entry by `get_default_customer_order_ref`
→ `order.order_number`). The IO header prints it ("Order Number: 07-MD10-2607VT")
and `_extract_order_number` already parses it. Match = exact
`SELECT ... WHERE CUSTOMERREF = '<order#>'`. Verified: 2939=07-MD10-2607VT (SF),
2933=12-MD10-2607CT (CN-SEA), 2935=04-MD10-2607FT (FIL-NY), etc. Handles revisions
(same order# → same contract). No derivation needed; immune to code-format changes.
Fallbacks if CUSTOMERREF miss: (a) derived code `"Admerasia McD {estimate}"`
(`get_default_order_code`); (b) client McDonald's + market + campaign dates + total
spots. If >1 match or fallback used → show candidates, confirm with user.

## What already exists (reuse, don't rebuild)
- `POST /api/traffic/admerasia/parse-io` — extracts ISCIs grouped by duration.
- `GET /api/traffic/contract/{id}/tpalinse-spots` — scheduled spots w/ date/time/dur/line.
- `POST /api/traffic/contract/{id}/assign-spots` — 1:1 {tp_id→filmati_id} write
  (TPALINSE + CONTRATTIFILMATI + MaterialAddToAssetListC + air-check flag).
- Frontend Admerasia flow: drop IO → manual per-spot ISCI dropdowns → Apply.
- `admerasia_positional.read_grid` (row,date,count) and `parse_admerasia_io_iscis`.

## Proven by this session's testing (7 clean July IOs)
- **Per-cell color = pixel ring-median around each spot digit @300dpi**: 100% of
  cells read across all 7; clusters to the exact palette (2 or 5 colors); per-color
  totals reconcile to each order's spot total; every program row duration-coherent.
- **Legend via pixels is NOT reliable**: ISCI list position varies per file + codes
  are garbled Type3 → positional heuristics mislabeled (Chinese→address block,
  VN-Seattle→off-by-one, Filipino→grid header). Must not ship.

## Design — division of labor (mirrors the entry parser)
1. **Positional grid** (`read_grid`): (program_row, date) → spot count. WHERE spots are.
2. **Pixel color** (new `admerasia_traffic_color.py`): ring-median around each grid
   digit → cell RGB; cluster into the order's palette. WHICH color each cell is.
3. **High-res vision** (extend `admerasia_vision.py` or a small dedicated call):
   read the ISCI legend → ordered `[(isci_code, duration, swatch_color)]`. The
   authoritative color→creative map. Trivial for vision (2-5 rows), no dense-grid
   counting weakness. Codes are read from the rendered image (garble-free).
4. **Match**: grid cluster color → nearest vision legend color (one-to-one) → ISCI
   → FILMATI id (COD_PROGRA = ISCI). Grid row i ↔ contract line i (entered from this
   grid, same order); each line's TPALINSE spots on date D take cell (row i, D)'s ISCI.
5. Emit `{tp_id → filmati_id}` and feed the existing `assign-spots` endpoint.

## Guardrails (hard-fail → preview shows error, excluded from write)
1. **Assigned creative length == ordered spot length** (±5 frames) — per spot. [user req]
2. Palette colors map to DISTINCT ISCIs; #colors used == #creatives used.
3. Per-program-row duration coherence (a :15 row cannot contain a :30 color).
4. Reconciliation: per-color totals sum to order total; per-(row,date) spot counts
   match grid; line count == grid row count, per-line totals match.
5. Contract auto-matched by order#/estimate → code "Admerasia McD <est> <yymm>";
   confirm before assign.

## UI
- Reuse the existing Admerasia drop-zone. On drop: parse IO + color + vision legend
  → PRE-FILL the per-spot ISCI dropdowns (the ones done by hand today) with the
  color-matched creative, flagged with the swatch color + any guardrail failures.
- User eyeballs the preview (visually easy) → Apply. Never auto-commit.

## Build order
1. `admerasia_traffic_color.py`: cell-color sampler + palette clustering (done as
   validated scratch prototype — port it). Unit-check vs the 7 IOs' known totals.
2. Vision legend read (high-res) → ordered ISCI+color. Cross-check codes vs
   `parse_admerasia_io_iscis`.
3. Matcher + guardrails → produce `{tp_id→filmati_id}` + per-spot diagnostics.
4. New/extended endpoint: `POST /api/traffic/admerasia/auto-color` (contract_id +
   IO) → returns pre-filled assignments + warnings. Writes via existing assign-spots.
5. Frontend: pre-fill dropdowns + swatch/warning display.
6. Verify end-to-end on all 7 IOs against the entered contracts (per-line, per-date).

## Open confirmations
- Preview-then-Apply (recommended) vs auto-apply. → preview, given precision needs.
- One IO → one contract per drop (7 separate). OK?

## Review
(after implementation)
