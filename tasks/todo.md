# Crispin: read the official Brand Time Schedule IO (2026-08-10)

Source: `Order# 00212735-002 For  3131CA,BAAQ,BAAQ,0001,CASH.pdf`
(BAAQMD 2026 TV Summer Campaign, estimate 0001, revision 2)

We trained Crispin on the **Excel proposal**. The official IO is a **Brand Time
Schedule PDF** (same family as Daviselen/Intertrend) — a format nothing in the
repo reads. Dates moved back (7/27 → 8/10) because a spot wasn't ready.

## What the IO actually is

- 4 pages. p1 = cover (Order#/Client/Product/Estimate only, 14 words).
- **Two 13-week column regimes**, not two flights: JUN01→AUG24 and AUG31→NOV23.
  Every line appears once per regime and must be merged by line number.
- p3 carries **both regimes on one page** (regime-1 summary at top, `-----`
  divider, then a second column header mid-page). Column mapping must be
  per-region, not per-page.
- Zeros are not printed → coordinate-based cell reading is mandatory.
- Per-line flight dates from the DATES column, and they **differ by line**
  (001–004/006/007 end 11/01; 008/009 end 10/30; 005 ends 8/16).
- Line 005 `TRANSLATION COST` $2,447.06 × 1 unit = a production charge, not airtime.
- Rates are **gross** (141.18 / 117.65 = $120 / $100 ÷ 0.85). Lee is setting
  ANAGRAF Crispin commission to 15% → enter gross verbatim, no override.
- Reconciles: 324 spots / $23,529.94 (`3131CA TOT`).

## Plan

- [x] Read the IO; confirm structure, totals, weekday alignment, ANAGRAF state
- [x] `crispin_parser.py`: add `parse_crispin_pdf()` + `parse_crispin()` dispatcher
      - map fields by **header label** (`LINE#`/`DAY(S)`/…/`TOT`), never by index
      - cluster rows on **raw** `top` floats (tol 2.0), never `round()`
      - regime detection = month row + day row pair; each data row binds to the
        nearest preceding day row; dates asserted 7 days apart
      - cell match by word **center** vs column center, tol ±8 (pitch is 19)
      - non-language program row → charge (keyword-checked) else **raise**
      - reconcile 3 ways (line TOT, per-regime PTS/WEEK + cost, grand total) → raise
- [x] `CrispinLine`: per-line `date_from`/`date_to`; `CrispinCharge` on the order
- [x] `crispin_automation.py`: honour per-line dates; production via the line
      form's **Production box** (Lee's revision — see Review)
- [x] `parser_bridge.py`: point CRISPIN at the dispatcher
- [x] Detection: `Brand Time Schedule` + `CRISPIN` on page 2, **before**
      Intertrend/Daviselen (shared marker); `_SCAN_CACHE_VERSION` 2 → 3
- [x] Tests: fixture-backed parse + reconciliation-refuses-on-tamper (21 tests)
- [x] Verify: xlsx proposal path re-parses identically

## Review

**Money.** Lee set ANAGRAF Crispin (446) Commissione 0 → **15%** during the
session, so the IO's gross rates enter verbatim and `lookup_customer_defaults`
resolves 15% with no override in code (verified live: `get_client_defaults(448)`
→ `agency_pct 15.0`). The standing rule holds: if an IO's rates look grossed-up,
fix ANAGRAF, never add a multiplier to a parser.

**Production (Lee, mid-session).** Production money must **not** become a
contract line — backwrite is not tuned for one. It now goes in the line form's
**Production box** (`add_contract_line(production_cost=…)` → SP params
`@production`/`@productionLabel`, previously hardcoded 0/""), which Etere turns
into a CONTRATTISPESE row named 'Production' dated the carrier line's flight
start (8/10/26 — inside both the broadcast and calendar August window). It rides
the **first paid** line so it sits on billable airtime. The zero-spot
carrier-line pattern is reserved for **production-only** orders; that path is not
built and now refuses with a message saying so instead of "no airtime lines".

Because the SP is encrypted, "the Production box writes a charge" is an
observation about historical rows, not a guarantee — `_verify_production_charge`
re-reads CONTRATTISPESE inside the transaction and rolls back on a mismatch, so
the money can never silently vanish.

**Entry plan (dry-run, no DB writes):** 16 Etere lines, 323 airtime spots,
+1 production charge = **324 units / $23,529.94** — equal to the IO's own
`3131CA TOT` to the penny. M-F lines correctly end 10/30 while M-Su lines run to
11/01. The universal language-window pre-check reports no issues.

**Guards proven by tampering the word stream** (not just asserted): a dropped
week cell, a cell slid one column, a renamed `DATES` header, a blanked rate, and
an unclassifiable grid row each **refuse** with a specific message. A no-op
mutation still parses, so the negative tests can't pass for the wrong reason.

### Late start: the answer drives the dates AND the max-per-day

The IO arrived 8/10 for a flight starting 8/10, so entry asks **"What date should
this order start?"** (re-prompts until it parses and lands inside the flight;
Enter/`y`/`yes` keep the IO's date). **Lee's answer: Wednesday 8/12.**

A later start does not reduce a week's spot count — it compresses those spots
into fewer days, so the truncated first week needs a **higher max-per-day** than
the full weeks behind it. `add_contract_line`'s auto-calc can't see this: it
divides by the day PATTERN's width (M-F → 5) with no idea the line opens on a
Wednesday. `_plan_ranges` now computes the cap per range and **splits the range**
when the short week and the full weeks disagree.

At 8/12 that turns **16 Etere lines into 19, losing no spots** (323/323,
$23,529.94): each of the three M-F dayparts gains a short-week line
8/12–8/16 at 4/wk **max 2/day** (Wed–Fri = 3 of 5 days), while the M-Su lines
don't split because Wed–Sun still holds 4 spots at 1/day.

`_line_plan` is the single source of truth — the gather preview prints it and the
entry loop walks the same object, so what Lee approves is what gets written. When
a start date makes spots undeliverable (e.g. a Saturday start on an M-F line, or
skipping whole weeks) the preview lists them, shows `entered of ordered`, and
requires an explicit y before entering short.

78 tests in `tests/unit/test_crispin_late_start.py`, including two invariants over
every weekday × day-pattern combination: the cap is never < 1, and
`cap × available days >= spots_per_week` so Etere is never handed a line it
cannot fill.

### Known, not fixed (pre-existing, out of scope)
- The **web preview** leaves the Mandarin bonus line's days/time blank:
  `parser_bridge._apply_ros_overrides` reads the shared `ROS_SCHEDULES`, which
  folds Mandarin into "Chinese". Entry is unaffected — it uses
  `CRISPIN_ROS_WINDOWS`, which has Mandarin (M-Su 8p-11:59p). Fixing it means
  touching the table every parser shares.
- A gross-quoted IO cannot net to a round number: 141.18 × 0.85 = 120.003, so
  the order's net lands ~24¢ above 81 × $120. The gross figures are what the
  document says and what we store; there is no separate net on the IO to match.
