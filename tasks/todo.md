# Feature: Offer to add Added Value (AV) when a Hoffman Lewis order has no bonus

## Context
Hoffman Lewis (H/L Agency) used to include bonus on every order. CVC/Sacramento
still does; SFO stopped putting bonus on the IO. When an order has **no bonus at
all**, offer to add a single Added Value line.

Applies to **both** parsers (HL and HL_BDR) — both are Hoffman Lewis. CVC orders
that already carry bonus won't trigger the prompt, so applying it to both is safe.

## Ground truth (verified)
- **Bonus = `rate == 0.0`** — canonical in HL (`HLLine.is_bonus()`). BDR lines have
  no bonus field, so a BDR line with `rate == 0` is bonus.
- **AV spot type = booking code `1`** (`trf_bookingcode`: code `AV`, "Added Value",
  whitelist priority 70).
- `is_added_value=True` in `add_contract_line` already → NEWTYPE `AV;COMS` and
  forces Rotation (PRENOTAZIONE=1). No client change needed.
- Each estimate = its own contract; inject AV line once per contract after the
  paid-line loop.

## AV line spec (per estimate/contract)
- Days: **M-Su** (all 7) — required so 1/day across the flight = total.
- Total spots: `(flight_end - flight_start).days + 1` (one per calendar day,
  inclusive). e.g. 7/7–8/2 → 27; 7/7–7/10 → 4; 7/7–7/27 → 21.
- `max_daily_run=1`, `spots_per_week=7`.
- Scheduling: Rotation (auto, via `is_added_value=True`).
- Time window: **widest window across that estimate's paid lines**
  (min start → max end; this order → 16:00–19:00).
- Duration: match the order's spot duration (first paid line; default :30).
- Spot type: `booking_code=1`, `is_added_value=True`, `whitelist_priority=70`.
- Description: `"M-Su {time} AV ROS"` (mirrors HL bonus `"... BNS ROS"`).
- Rate: 0.0.

## Plan
- [ ] **New shared helper** `browser_automation/added_value.py`:
  - `SPOT_CODE_AV = 1`
  - `order_has_bonus(line_rates: list[float]) -> bool`
  - `prompt_add_av(has_bonus: bool) -> bool` — the y/N prompt (no bonus → ask)
  - `widest_window(times: list[str]) -> str` — min start–max end via
    `EtereClient.parse_time_range`, returns `"HH:MM-HH:MM"`
  - `add_av_line(client, *, contract_id, market, time_range, date_from, date_to,
    duration, separation) -> int` — calls `add_contract_line(is_added_value=True,
    booking_code=1, days="M-Su", max_daily_run=1, spots_per_week=7,
    whitelist_priority=70, total=days_in_flight)`
- [ ] **HL_BDR**: in `gather_hl_bdr_inputs`, set
      `inputs["add_av"] = prompt_add_av(any rate==0 across all estimates)`.
      In `_execute_order`, after the per-order line loop, if `add_av`, add one AV
      line using that order's flight dates + widest window + duration.
- [ ] **HL**: same two hooks in `gather_hl_inputs` / `_execute_order`
      (`estimate.lines`, `estimate.flight_start/_end`, `estimate.market`).
- [ ] Print the AV line in the same `[BDR]/[H&L] ✓ ...` style; count it.

## Verification
- [ ] Toyota CRSF-TV (no bonus): prompt fires; answer "no" → unchanged 3×5 lines;
      answer "yes" → each contract gets +1 AV line (Est 13934 → 27 spots, M-Su,
      16:00–19:00, Rotation, booking 1, desc contains "AV").
- [ ] A CVC HL order WITH bonus ($0 lines): prompt does NOT fire.
- [ ] Validate via DB read: AV line present, NEWTYPE AV, PRENOTAZIONE=1,
      max daily=1, total=days-in-flight.
- [ ] Existing hl/bdr tests still pass.

## Review
Done. Refined per request: description lists the **languages ordered** instead of
the time window (single → full name "Filipino"; multiple → comma abbreviations
"M,C,V"); falls back to the time window if no language is recognized.

Files:
- NEW `browser_automation/added_value.py` — shared helper: `prompt_add_av`,
  `widest_window`, `format_languages`, `av_total_spots`, `add_av_line`
  (booking_code=1, is_added_value=True, M-Su, max_daily=1, whitelist 70).
  Language map reused from `hl_bdr_parser._BLOCK_PREFIX`.
- `hl_bdr_automation.py` — gather prompt (no-bonus → ask) + per-contract AV line.
- `hl_automation.py` — same two hooks (languages from first word of program).
- NEW `tests/unit/test_added_value.py` — 16 tests.

Verified:
- [x] Toyota CRSF-TV (no bonus): prompt fires; each contract gets +1 AV line —
      Est 13934/13935 → 27 spots, Est 13936 → 30 spots, all "M-Su Filipino AV ROS",
      16:00–19:00, M-Su, booking 1, is_added_value (Rotation).
- [x] Bonus detection = any rate==0 line (CVC orders with $0 lines won't prompt).
- [x] Helper math: 7/7–8/2=27, 7/7–7/10=4, 7/7–7/27=21 (1/day inclusive).
- [x] 61 hl/bdr/detect tests + 16 new added_value tests pass.
- [ ] Live-DB confirmation deferred — would create real contracts; logic verified
      via fake client + real-PDF dry run instead.
