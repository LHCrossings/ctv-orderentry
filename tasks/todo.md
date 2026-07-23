# Marketplace long-form PI fill/replace (DAL) — via row-click modal

Marketplace behaves like any other program row on the Daily Programming utility.
Click a Marketplace row → a modal opens with PI options → applies to **that day
only** (DAL / cod_user 10).

## Decisions (confirmed with Lee 2026-07-23)
- **Row-click → modal**, single day. NOT a new card, NOT a multi-week view.
- **Auto-mirror**: assigning a half-hour applies to BOTH its noon (12:00) and
  late-night (23:00) copy that day. Mirror group = all Marketplace :30 blocks
  that day sharing the same in-hour offset (:00 → group A, :30 → group B).
- **Modal options**: 🎲 random-from-unused AND manual pick from the active PI list.
  Works the same whether the slot is blank (fill) or filled (replace).
- **One combined rotation pool** (PI-LF + WLPI-LF).
- **Rotation**: replaced-out PI stays "used"; reappears in the next cycle. Draw is
  random WITHOUT replacement until the pool is exhausted, then a new cycle starts.
- **Apply immediately** from the modal (DAL is one market — no batch run needed);
  random draw resolves + marks "used" on the placement commit, so rotation is exact.

## Data facts (verified against live DB)
- Identity token (`PI-LF-NNNN` / `WLPI-LF-NNNN`) is at the **start of
  `FILMATI.DESCRIZIO`** (`PI-LF-0002: Audien…`); `COD_PROGRA` is unrelated.
- Exclude by name marker in EITHER `DESCRIZIO` or `COD_PROGRA`: `DO NOT USE`,
  `HIATUS`; also drop expired (`DATA_SCAD < today`). `ARCHIVIATO` is `''` for all
  rows → not usable. 15 active today.
- Each Marketplace hour = 2 PRGS segments; `_split_marketplace` already yields the
  two :30 blocks. Slots derived from the grid per day (weekends may have none).
- Observed EVENT_TYPE: :00 block = `F`, :30 block = `T`.

## Plan

### 1. Persistence — `chat.pi_lf_rotation`
- [ ] `scripts/setup_pi_lf_rotation_table.py` (idempotent DDL, mirrors
  `setup_show_profiles_table.py`): `pi_token PK`, `used_at`, `cycle_no`.
  Row present = "used in current cycle."

### 2. Service — `src/business_logic/services/pi_rotation.py` (new)
- [ ] `active_pool(cur)` → `[{fid, token, code, desc, durata, family}]` (verified
  active query; parse token from DESCRIZIO).
- [ ] `draw(conn, exclude=())` → one token: `unused = active − used − exclude`;
  if empty, new cycle (bump `cycle_no`, clear used); pick random; mark used;
  return `{fid, token}`. (`exclude` lets a single action skip a just-drawn token.)
- [ ] `rotation_status(cur)` → `{used, remaining, cycle_no}` for the modal badge.

### 3. Placement — extend `daily_programming_run.py`
- [ ] `fill_marketplace(conn, d, windows, fid, pending)`: for each PRGS window in
  the mirror group (noon + late copies), insert-or-replace the whole PI file into
  that segment. Reuse `_slots`, `_insert_event` + `sch_UpdateSupportAndProperties`
  + EVENT_TYPE (:00→F, :30→T) for blanks; in-place re-point (`_replace_piece_once`
  style) for filled. Then `_rebuild` + `_sync_checksums` + `_verify_sequence`;
  **commit only on pass, else rollback**. DAL-only, single connection/transaction.

### 4. Routes — `src/web/routes/orders.py`
- [ ] `GET …/marketplace/options?date=&start=&end=` → for the clicked slot's mirror
  group that day: current occupant(s) (token/desc or blank) + active pool +
  rotation status. Populates the modal.
- [ ] `POST …/marketplace/assign` → `{date, start, end, fileId | random:true}`.
  Resolve the mirror-group windows for (date, in-hour offset of start); if random,
  `draw()`; `fill_marketplace(...)`; mark token used on commit. Returns the placed
  token/desc + per-window ok/message.

### 5. UI — `daily_programming.html`
- [ ] `isMarketplaceProgram(p)` (`title==='Marketplace'`, network TAC). In the
  row-click dispatcher, branch Marketplace rows → `openMarketplaceModal(idx)`
  instead of the file super-search.
- [ ] Marketplace modal: shows current occupant (this slot + its mirror), a
  🎲 "Assign random (unused)" button, and a "Pick specific" list of active PIs
  (reuse the search-modal list infra). On action → `POST …/assign` → refresh grid
  + placement badges. Small "X/Y used this cycle" line.

### Dropped vs earlier draft
- Separate "Marketplace card", two-week range, and cross-week "fill all blanks"
  bulk button — superseded by the per-row modal / single-day model. (Per-row
  random covers the fill case; revisit a per-day "randomize both" only if asked.)

### 6. Verify (live, safe window)
- [ ] Active-pool + exclusion query (done: 15 active; DO NOT USE / HIATUS excluded).
- [ ] Rotation: 15 draws distinct → 16th starts a new cycle; replaced-out stays used.
- [ ] Placement: on a known-blank DAL day, assign one slot → confirm insert,
  rebuild, checksum, verify-gate, and that BOTH mirror windows (noon + late) got
  the PI; then replace it (manual pick) → read-back occupancy. CTV untouched.

## Review (2026-07-23)
Built + verified against the live DB:
- `chat.pi_lf_rotation` created (idempotent script). `pi_rotation.py`: active pool
  = 15 (excludes DO NOT USE / HIATUS / expired); `pick` peeks without marking;
  full 15-draw cycle returns all-distinct, then draw #16 resets — confirmed.
- `fill_marketplace` reversible live test on 2026-07-27 offset-0 group
  (12:00–12:30 + 23:00–23:30): swapped PI → verify-gate committed → mirror both
  windows updated → swapped back; SAME row ids preserved (in-place re-point, no
  ghost rows), schedule left as found.
- Mirror-group resolution verified against the sample grid (offset 0 → noon+late
  top halves; offset 30 → bottom halves).
- Routes + UI modal wired (row-click → assign/replace, random + manual, mirror).
- ruff + py_compile clean.

NOT exercised in WSL (no K: mount here — runs on the Windows server): the grid
read in the two routes (`get_day_programs`) and therefore the end-to-end modal.
The blank-INSERT branch of `fill_marketplace` reuses the proven `_insert_event`
path (same as pieces/daily-ID placement); only the replace branch was live-tested.
Recommend one real assign on a blank DAL Marketplace slot on the server behind
the verify-gate.

Setup on the server: `uv run python scripts/setup_pi_lf_rotation_table.py`.
