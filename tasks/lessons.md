# Lessons Learned — Active Rulebook

Core lessons that apply to all new parsers and ongoing work. Parser-specific quirks and historical bugs are in `lessons-archive.md`.

---

## The Yellow Triangle Is NOT (Only) the Checksum — Diff the Flagged Row Against the SAME Asset in a Clean Market

**Session:** Finish on CVC 9/2 left triangles everywhere — Lee: "I don't think your findings are entirely correct" (2026-09-01)

**Rule:** For two months every triangle note said "stored SCHEDULE_CHECKSUM != live". Finish
refreshed checksums, the DB said 70 of 71 rows in the window matched, and I told Lee twice
that EE just needed a reload. He reloaded: triangles on half the day, only in CVC. The
checksum test was a *sufficient* cause I had once confirmed with Profiler and then treated as
the *only* cause. The actual discriminator took one query: the same asset's TPALINSE row in a
market MC had exploded. CVC rows carried `TIMECODE_O = POS_FIN + 1` (the scheduler writes the
nominal length; a short asset overruns by 2-3 frames); exploded rows carry
`TIMECODE_I/O = POS_INI/POS_FIN, DURATION = POS_FIN-POS_INI+1`. EE flags an event whose
in/out lies OUTSIDE its asset — 100% of the flagged commercials in four screenshots, 0% of
the clean ones, and program PARTs (sub-ranges inside one file) correctly unflagged. Stale
checksum is the *other* trigger (programs in the never-exploded hours). Explode fixes both;
`_refresh_checksums` fixed one. Out-of-bounds rows do air (27 A / 121 Q, 0 E since 8/20).

**How to apply:**
1. When a validator I wrote says "clean" and the user's screen says "flagged", the user's
   screen is the ground truth and my model is incomplete — stop defending it after ONE
   reload check. Do not say "EE needs a reload" twice.
2. **The oracle for a per-row flag is the same asset's row in a market where the flag is
   absent**, diffed column by column (`CVC vs others`: TIMECODE_O, VISIONATO, CRAWL_DESC
   popped out in one query). Diffing flagged-vs-clean *assets* within the broken market
   found nothing for an hour — the property was per ROW, not per asset.
3. Take a *truth set* from the screenshots (which rows are flagged, which are not) and
   require a candidate rule to score 100/0 on it before building on it. The checksum rule
   scored 0/… on the 06-09 rows and I built on it anyway.
4. A mass condition that is "only in market X" (196 rows CVC vs 5 elsewhere) is almost never
   a per-asset property (assets are shared) — look for what a per-market OPERATOR action
   (MC's Explode) would have written.
5. `finish_service._explode_window` now mimics Explode for the whole window (timecodes +
   DURATION + checksum, PART=0, LIVE_ID NULL, before `plan_window` since the planner reads
   DURATION; `_id_only` path re-times and asserts the hour end held). "Finish a show" means
   zero triangles in it, not zero triangles on the rows Finish inserted.

---

## A Width/Limit Copied From a Prior Fix Is Still a Guess — Read the Schema and Diff an Aired Sibling

**Session:** Maija's first Fill & Finish team test (2026-09-01) — most blocks errored

**Rule:** Two independent Finish failures, one shared shape: a constant carried forward
without checking what it constrains. (1) `_supporto()` truncated the playout binding
`[:30]` — inherited from the 7/22 PI-binding fix — but `TPALINSE.SUPPORTO` is
**varchar(42)**, and DAL's station ID binding is 31 chars: every DAL Finish wrote a
clipped binding and rolled back on its own verify. One `INFORMATION_SCHEMA` query plus
one look at an aired sibling row (which carried all 31 chars, STATUS='A') settled it.
(2) The break→COMS-segment search used ±120s around the actual break position — but the
grid's COMS segments sit at NOMINAL offsets (:14/:25:30/:29) while breaks land wherever
the pieces end (19:07:23), so most CTV blocks found nothing. The window the constant
implied ("breaks are near their nominal segments") was never true of DP-placed content.

**How to apply:**
1. Before truncating/bounding to a literal, verify it against the live schema
   (`CHARACTER_MAXIMUM_LENGTH`) — and when a "convention" constant exists in a sibling
   helper, suspect it was sized for that helper's data, not yours.
2. **An aired row is the oracle for any playout-facing value.** Diff what you write
   against what Etere/hand placement wrote for the same asset and did air. (Third
   appearance of this rule: sch_UpdateSupportAndProperties, PI fillers, now width.)
3. A transaction verify that fires on your own write is a SUCCESS — the DAL rollbacks
   meant zero corrupted rows reached playout. Fix the write, keep the verify, and make
   it exact (readback == written value; a `LIKE '%x%'` containment test cannot see a
   truncation of x).
4. When a proximity search fails "sometimes, per market": check whether you're
   searching near where the item nominally SHOULD be (grid offset) instead of where
   the schedule actually put it. If the association is bookkeeping (playout order is
   ORA/XORDER), nearest-in-window beats nearest-to-nominal.

---

## "I Don't Have Access" and "It Works Like X" Are Claims — Probe Before Stating Them

**Session:** SpotOps address book + CIB transfer path (2026-08-27)

**Rule:** Three times today I stated something about the environment that a one-minute
read-only probe would have settled: (1) "no access to portal.sales" — it resolved over
Tailscale and served JSON with no login; (2) the ETXServer log glob (`Etere.ETXServer*`)
— the files are PREFIXED `au64.etereau64.NNN.<host>.`, so the first fleet scan returned
FILES=0 on every box; (3) "the Datamover pushes over SMB" — inferred from file arrivals
until Lee challenged it, then confirmed live (258 open files on `\\CIB01\etxdb`).
Each was cheap to verify and each cost a round-trip with Lee when stated unverified.

**How to apply:**
1. Before saying a resource is unreachable or a capability is missing, try it:
   `getent hosts`, a `urllib` GET, an `ssm send-command` with `hostname`. Report what
   the probe returned, not what the config suggests.
2. Before scanning a fleet with a filename pattern, list the directory on ONE box first
   and build the glob from real names.
3. Label inferences as inferences ("I believe X because Y") until a probe upgrades
   them; when the user pushes back, the correct move is the probe, not the argument.
4. Corollary that worked well: when a user's mental model and mine differ, both are
   usually partly right — the Restore.log histogram showed "files move all day" AND
   "half of them move at 06:00". Measure the split instead of picking a side.

---

## "When We Do It" Is a Statement of FUTURE Intent — Not a Go-Ahead for Production Writes

**Session:** Shop LC Saturday fix, 2026-08-12 — Lee: "I didn't really intend to do all of this right now"

**Rule:** Lee asked to be *reminded* of the NYC Shop LC issue ("maybe we together can
fix it") and later noted "it's a safe and easy swap **when we do it**." I took the
safety remark as authorization and executed verified production writes across 7
markets. The writes were correct, guarded, and undoable — and still premature: the
phrasing framed a future working session, not a green light. "Safe to do" and "do it
now" are different statements.

**How to apply:**
1. Before any production write, the go-ahead must be **explicit and present-tense**
   ("go ahead", "do it", "fix it now"). Phrases like "when we do it", "we could",
   "maybe we can fix it together" scope a FUTURE task — respond with diagnosis + a
   written plan, then stop.
2. "We together" is itself a signal: the user wants to be in the loop per step, not
   handed a completed change.
3. Diagnosis/read-only research is always fine — the line is the first UPDATE.
4. If the line is crossed and the user pulls back: stop immediately, state exactly
   what was written, point at the undo, and let them choose keep vs revert. Never
   argue the change was correct as a reason it should stand.

---

## The PROPOSAL and the OFFICIAL IO Are Different Documents — Same Order Type, Two Readers, and the Money Basis Flips

**Session:** Crispin official IO for BAAQMD, order 212735 rev 2 (2026-08-10)

**Rule:** We trained Crispin on Charmaine's Excel **proposal**; the agency then sent
the official **IO**, a "Brand Time Schedule" PDF (the same agency-system layout
Daviselen and Intertrend send) that nothing in the repo could read. Critically the
proposal quoted **net** ($120/$100 discounted) while the IO quotes **gross**
(141.18/117.65 = net ÷ 0.85). Entering the IO's numbers under the proposal's 0%
commission would have booked **17.6% more revenue than the deal**.

**How to apply:**
1. **One order type, one dispatcher, two readers.** `parse_crispin(path)` routes on
   extension into `parse_crispin_pdf` / `parse_crispin_xlsx`, both returning the same
   `CrispinOrder`. The automation and bridge never learn there are two formats. Do
   NOT fork a second OrderType — that duplicates the automation and drifts.
2. **Never fix a gross/net mismatch with a multiplier in the parser.** Rates enter
   verbatim from whichever document we read and the **ANAGRAF commission** nets them
   down (`lookup_customer_defaults=True`). Lee set Crispin 0% → 15% in ANAGRAF for
   exactly this; the "use the commission linked to the client/agency" rule is what
   makes mixing a net-quoting proposal and a gross-quoting IO safe at all.
   Diagnostic: rate ÷ 0.85 landing on a round number means the doc is gross.
3. **Detection order:** a Brand Time Schedule carries no agency name on page 1 (a
   14-word cover), so match `"Brand Time Schedule" + "<AGENCY>"` on **page 2**, and
   test it **before** Intertrend/Daviselen, which share the marker. Bump
   `_SCAN_CACHE_VERSION` — the scan cache keys on file signature, so stale
   classifications outlive the code fix.
4. **The file is parsed at TWO call sites — fix both.** Pointing the gather and
   `parser_bridge._REGISTRY` at the dispatcher was not enough:
   `OrderProcessingService._process_crispin_order` re-parses the source itself and
   still imported `parse_crispin_xlsx`. So the entire interactive gather ran clean
   (IO read, late start replanned, customer resolved, separation confirmed) and
   entry then died on `openpyxl does not support .pdf`. **A gather that succeeds is
   not evidence the handler agrees.** `tests/unit/test_order_processing_service.py`
   now AST-walks every `_process_*_order` for its parser imports and asserts they
   match the bridge's registered entry point — verify a structural test like this
   FAILS on the drift it was written for before trusting it.

**Four traps in the Brand Time Schedule layout itself:**
1. **Column regimes.** A long flight is split into ~13-week column blocks and every
   line prints **once per regime** — merge by LINE#. A page can carry **two
   regimes** (regime-1's summary, a `-----` divider, then regime-2's header
   mid-page), so bind each data row to the nearest **preceding** day-number header:
   per-REGION, never per-page.
2. **Zero cells are not printed.** A "3" only means SEP14 by its x-position. Match a
   cell to a column by **centre distance** (tol 8 against a ~19pt pitch), and map
   the FIELD columns by **header label** (`LINE#`/`DAY(S)`/…/`TOT`) so an added
   column is a no-op and a renamed one raises. Cluster rows on **raw** `top` floats.
3. **Flight dates are PER LINE** (the DATES column): M-F lines ended 10/30 while
   M-Su lines ran to 11/01, so an order-level `flight_end` stretches the M-F lines
   two days. Also **trim each line to its own flight** — a line is zero-padded
   across the other regime's 13 columns, so without trimming every line reports a
   26-week grid; spots outside the stated flight are a contradiction, so raise.
4. **A non-airtime row rides in the grid as an ordinary line** (`TRANSLATION COST`,
   1 unit @ $2,447.06, complete with a `:15` length and a `RO` daypart code). Rule:
   a row naming **no language** is not airtime; accept it as a charge only if it
   also names a recognisable cost, else **raise** — guessing either way is wrong.

**Reconcile three ways and RAISE** (the doc is its own oracle): per line
`sum(week cells) == the line's TOT`; per regime against the `PTS/WEEK` summary row's
cells, spots AND dollars; and the whole order against `<station> TOT`
(324 units / $23,529.94, hit exactly). **Verify guards by tampering the word
stream**, not by asserting they exist — a dropped cell, a cell slid one column, a
renamed header, a blanked rate and an unclassifiable row must each refuse, and a
no-op mutation must still parse or the negative tests pass for the wrong reason.

**Production money does NOT become a contract line** (Lee): it goes in the line
form's **Production box** — `add_contract_line(production_cost=…)`, SP params
`@production`/`@productionLabel` (label IS the charge DESCRIZIONE, and only
'Production'/'Dubbing' are visible to the proposals app). Etere writes the
CONTRATTISPESE row itself, dated the carrier line's flight start; ride the **first
paid** line so it sits on billable airtime. Backwrite is not tuned for a production
line, and a zero-spot carrier line reads as airtime there — that pattern is
reserved for **production-ONLY** orders. The SP is encrypted, so "the box writes a
charge" is an observation about historical rows, not a contract: re-read
CONTRATTISPESE **inside the transaction** and roll back on a mismatch, or the money
silently vanishes.

**A LATE order's start-date answer must drive max-per-day, not just the dates.** A
later start does not reduce the week's spots — it compresses them into fewer days, so
the truncated first week needs a **higher** cap than the full weeks behind it.
`add_contract_line`'s auto-calc divides by the day PATTERN's width (M-F → 5) and has
no idea the line opens on a Wednesday, so compute the cap **per range** and **split
the range** when the short week and the full weeks disagree — one IO line becomes two
Etere lines. Order 212735 started Wed 8/12 instead of Mon 8/10: 16 lines → **19**,
zero spots lost, each M-F daypart gaining an 8/12–8/16 line at **2/day** (Wed–Fri = 3
of 5 days) while M-Su lines didn't split (Wed–Sun still holds 4 spots at 1/day).
Rules: (1) the gather preview and the entry loop must walk **one planner object**
(`_line_plan`) so what the user approves is what gets written; (2) when a start date
makes spots undeliverable — a Saturday start on an M-F line, or skipping whole weeks —
list them, print `entered of ordered`, and require an explicit `y`, never enter short
silently; (3) assert the invariant `cap × available days >= spots_per_week` across
every weekday × day-pattern, or you hand Etere a line it can never fill; (4) the
prompt asks "**What date should this order start?**" and re-prompts until it parses
and lands inside the flight — it feeds every date calculation, so it fails where the
human is standing.

**Testing a PDF parser without authoring a PDF:** commit the real IO as a fixture
(9.5 KB, and it carries its own totals) and drive the negative tests by mutating
`extract_words()` through a `sys.modules['pdfplumber']` shim — it targets exactly
the layer that fails in the wild. `tests/conftest.py` MagicMocks pdfplumber suite-
wide, so swap the real library in with a **module-scoped fixture that restores the
mock on teardown** (verified by running the modules in both orders) — a permanent
replacement is the order-dependence trap that same conftest warns about.

---

## A Wrapped Table Row Puts Its Metadata ABOVE the Key — and "Last Match in Block" Steals the Neighbour's Language

**Session:** Maija's HL traffic parse, Toyota August ACM #13935 R1 (2026-08-10)

**Rule:** HL prints traffic rows ISCI-first (`TYRN41271H <title> :30 ACM TV (Cantonese)
:30 100% 8/4/26 8/11/26`), but when the title fills the line the row **wraps**: the dates
print on the line **ABOVE** the ISCI, and the ISCI line keeps only `(Dialect)` — or the
ISCI sits **alone** with the dialect on the line below. Maija saw "Hindi doesn't show the
date, Cantonese 8/12–8/31 missing." Both were one layout, and it hid a third, worse bug.

**The three defects, all from the same cause:**
1. `_ISCI_RE` required `\s+(.*)` after the code, so a **bare ISCI line never started a
   block** — `TYRN43031H` (Mandarin) was swallowed as body text of the ISCI above it and
   **vanished from the parse**.
2. Because that block never closed, it absorbed the next row's `(Mandarin)`, and dialect
   was "the **last** `(Word)` in the block" → `TYRN43021H`, the **Cantonese** cut, was
   tagged **Mandarin**. 34 Mandarin spots would have aired the wrong-language commercial.
   **Nothing on screen looked wrong** — the dialect column just read Mandarin. Same family
   as the Admerasia colour→ISCI collapse: the language must stay tied to its own creative.
3. The last row's block ran past the table into `Link to spots:` (the end marker only knew
   `Link to new spots`), so `TYRN43051H` got **no dates** — and the route falls back to the
   instruction-level window when a spot has none, so the 8/12 Hindi creative would have
   been assigned across the entire 8/4–8/31 flight, colliding with the 8/4 creative.

**How to apply:**
1. A key that can appear **alone on its line** must still anchor a record: `\s*(.*)`, never
   `\s+(.*)`. One missing character deleted a whole creative.
2. When a row can wrap, resolve each field by **adjacency, in priority order** — own line,
   then the line ABOVE, then the lines below — and mark each metadata line **claimed by
   exactly one key**. Claiming is what makes it correct rather than lucky: every ISCI
   resolves from the line above it, leaving the line below free for the next one. Without
   it, this PDF only "worked" because all four dialects shared one window; a per-dialect
   flight would have silently given every spot its neighbour's dates.
3. **Search a record's attributes FORWARD from its key, never backward, and never past the
   next key.** "Last match in the block" is only safe if block boundaries are perfect —
   and they were not.
4. **Validate a parsed enum against a known vocabulary.** A parenthesised word only counts
   as the dialect if it IS a dialect, so a title like `(Non Offer)` can never become a
   language. Detect by content, not by position.
5. **Sweep every traffic PDF on hand old-vs-new and require the diff to be exactly the
   file you meant to fix** — 10 files swept, only #13935 changed, the June #13933
   ISCI-first instruction byte-identical.
6. **Diagnostic signature:** one row missing its date while its siblings have theirs, plus
   a dialect that appears twice across flights while another is absent. A missing date is
   the *cheap* symptom; the duplicated dialect is the one that misairs copy — when a
   dialect count looks wrong, check the ISCI against the PDF before assigning.

---

## An Agency Inserting ONE Column Silently Zeroed Every Rate — Map by Header Label + Reconcile

**Session:** DART Aug/Sept entered $3,000 as $0 on every line (2026-08-07)

**Rule:** `dart_parser` read the rate from a hardcoded `row[3]` (col D). DART inserted
a **Length** column between Schedule and Rate, so D became `":15s"`, `Decimal(":15s")`
raised, and a bare `except: rate = Decimal("0")` turned the whole buy into free
airtime. Contract 3010 entered with **$0 on all 9 lines**. The same order exposed a
second positional assumption: only the FIRST week date was read and the rest
**synthesized** as `first + 7i`, so the sheet's real 8/17, **9/14, 9/21** columns
became 8/17, 8/24, 8/31 and the flight entered as 8/17–9/6 instead of 8/17–9/27 — spots
in the wrong weeks. Both silent. The April order (DART 2604, $2,000) had entered fine
because the sheet had no Length column yet.

**How to apply:**
1. **Map columns by HEADER LABEL, never by index** (`_COLUMN_LABELS` +
   `_find_header_row`). An agency adding a column is routine and must be a no-op; a
   *renamed* column must fail loudly (`_require_columns`) rather than fall back to a
   positional guess.
2. **Read every week date from its own header cell.** Week columns are NOT necessarily
   consecutive — this order skips four weeks. Never synthesize `first + 7i`.
3. **Never `except: → 0` on money.** `_money()` returns **None** for an unreadable
   cell and a paid line with `None` raises. A zero rate is a legitimate value (bonus),
   so it can never double as "couldn't read it" — same family as the ANAGRAF-commission
   and iGraphix net-rate traps.
4. **Reconcile against the sheet's own arithmetic and RAISE** (the SCWA/SAGENT/Brentan
   rule, now applied here): per line `rate × units == Total Cost` and
   `sum(week columns) == Total Units`, plus the whole order against the **PAID summary
   row's Total Cost**. Any of those three would have caught this at parse time. Verified:
   relabel/blank the rate column and the parse now refuses instead of entering $0.
5. **Spot length is PER LINE** (paid :15s alongside bonus :30s) — `DartLine.spot_length`,
   with the order-level duration as fallback. Same lesson as the Charmaine/XML per-line
   length note.
6. **Diagnostic signature:** the gather summary printed `$0/spot` and `Cost: $0.00`
   **before** entry. A totals line reading $0 on a real buy is a parse failure, not a
   display quirk — stop and reconcile before answering the prompts.

---

## When a Vendor Tool Refuses, Diff It Against a WORKING Peer Before Believing the Message

**Session:** Etere refused the WDC/MMT program-grid copy, ">24 hours" (2026-08-07)

**Rule:** Etere's Weekly Schedule refused to copy WDC and MMT with *"there are some days
with a duration greater than 24 hours"* listing 12/28/2026–1/3/2027. **The claim was
false.** Every one of those days measured exactly 24:00:00 three independent ways (raw
`traffic_block` durations + offsets, Etere's own `trf_getDayStructureList()`, and the
`Traffic_DayStructure` view), and WDC's week was **block-for-block identical** to CVC's,
which copied happily. The validation lives in the desktop binary; nothing in the
database distinguished a station that worked from one that didn't.

**How to apply:**
1. **Pull a known-good peer as a control immediately.** CVC was the single most valuable
   query of the session: it turned "is this data corrupt?" into "this data is identical
   to data that works," which killed four hypotheses at once. Diff broken-vs-working
   before diffing broken-vs-your-mental-model.
2. **Trust an error's numbers only after reproducing them.** I found 20 orphan schedules
   named for exactly those 7 dates whose block totals summed to 30–67 hours — a
   seductive match to the error. Clearing them changed nothing. A number that matches
   the symptom is a hypothesis, not a diagnosis.
3. **The user's observations are the cheapest discriminators.** "It fails for *any*
   source week" and "it still fails with a different destination" each eliminated a
   whole theory in one message — faster than any query I ran. Ask what varies.
4. **Know when to stop root-causing.** After per-date overrides, `hourprev/durprev`,
   `Users.RolloverFrame`, gaps/overlaps, cross-station blocks and duplicate calendar
   rows all came back clean, further digging had no path — the code was encrypted/
   compiled. Reproducing the operation in SQL took ~1s per market and verified clean.
   Bypass beat root-cause here; say so plainly rather than pretending to a diagnosis.

**Writing to Etere's schedule tables safely** (full detail:
`tasks/weekly-schedule-control-room.md`, spec for moving this into Control Room):
grid = `Traffic_Calendar` → `traffic_schedule` → `traffic_scheduleblock` →
`traffic_block` → `traffic_segment`; blocks/segments are **shared station assets** so a
copy re-points at the same `ID_TrafficBlock` and never duplicates them;
`Traffic_DayStructure*` are all **VIEWS** (no cache to rebuild); schedule `Name` is a
meaningless inherited label (live CVC days read `Schedule of 11/29/2021 usrdvr`) — never
key on it. Guards that made a 357-day production write safe: targets empty, **zero
placed spots in range** (`TPALINSE` + `trafficPalinse`), per-day `(block, offset)` diff
against the source **both directions**, day start 647352 / span 2589408, **assert the
weekday pattern varies** (this is what catches Etere's own "one day copied to all
seven"), one transaction verified before commit, and a restore `.sql` written first.

---

## Confirmation Prompts Need a GUESS From Every Source the Parser Already Has

**Session:** Admerasia contract 3009 — six "[?]" language prompts (2026-08-07)

**Rule:** `_catalog_line_languages` guessed each line's language by scanning the line
DESCRIPTION. Admerasia line descriptions are pure dayparts (`W 11:30a-12:00p`), so all
six prompts showed `[?]` and Lee typed `V` six times — while the parsed order carried
`language = "Vietnamese"` the whole time. A confirmation prompt whose guess is empty
is barely better than no automation: the human does the work AND the review.

**How to apply:**
1. Layer the guess, most-specific source first: line description → order-level language
   from the parser (`_order_language_name` reads `order_input['order'].language`, then
   `order_input['language']`). `guess_language(desc) or order_guess`. The order hint may
   NEVER override a line that names its own language — a Chinese IO has per-line
   Mandarin/Cantonese that must not be flattened to the header's "Chinese".
2. `order_groups[i]` ↔ `results[i]` in lock-step is the existing contract that
   `_write_backwrite_manifests` already relies on — reuse it to reach the parsed order
   rather than inventing a new channel.
3. **Suppress a guess you know is probably wrong.** `guess_language("Chinese")` returns
   the combined-block code `M/C`, but a Chinese IO's individual dayparts are Mandarin
   OR Cantonese (the daypart decides — see the language-window lessons). A wrong guess
   that **Enter accepts** is worse than `[?]`, which at least forces a decision. The
   pass prints why it is staying quiet.
4. When a confirmation prompt shows `[?]`, treat it as a bug report about the guess, not
   as normal. Ask what the parser already knows that the prompt isn't being told.

---

## A Prompt Phrased as a Question Gets Answered "y" — Never `resp if resp else default`

**Session:** DART died on `int('y')` after the whole gather was answered (2026-08-07)

**Rule:** `Use stored customer ID '426'? [Enter=yes / type new ID]:` invites `y`, and
`customer_id = resp if resp else stored_id` stored the literal string **"y"**. It
survived the entire gather, the separation confirm, and the Admerasia order ahead of
it, then died on `int(customer_id)` inside `_create_dart_contract_direct` — after
Lee had answered every other question. **DART and Polaris both had it**, a
copy-paste family; lesson #6 (the date-override `y`/`yes` rule) had been applied to
Polaris's date prompts but never to either customer-ID prompt.

**How to apply:**
1. Use the shared **`customer_defaults.prompt_customer_id(default)`** — bracket-default
   phrasing (`Customer ID [426]:`, lesson #9), Enter/`y`/`yes`/`Y` all accept the
   default, and it **validates numeric and re-prompts**, so a bad ID fails at gather
   instead of mid-entry. Never re-implement it per parser.
2. A prompt whose answer feeds `int()`/`Decimal()`/`strptime()` must validate at the
   prompt. "Fail where the human is standing" — a gather-time re-prompt costs one
   keystroke; a processing-time crash costs the whole batch's worth of answers.
3. **Phrase prompts so the default is a value, not a yes/no.** `[426]:` cannot be
   answered "y" meaningfully; `Use 426?` invites it.
4. When you fix a prompt like this, `grep -rn "Enter=yes"` for the siblings — that's
   how both files were found in one pass.

**Also fixed here:** DART's new-customer branch prompted for a description prefix and
**discarded it** (`desc_name` assigned, never used — ruff had been flagging it). It
now reaches `description_name` in customers.db, so the next order defaults correctly
instead of re-asking. An "ask the user then throw it away" bug is invisible until
someone reads the lint.

---

## A Parser Sidecar Left in `incoming/` Renders as a PENDING Order Forever

**Session:** "the jsons still show up as pending after processing" (2026-08-07)

**Rule:** Entry moves the IO to `Entered/` but `_move_io_to_entered` moved **only the
IO**, stranding `<io>.adm.json` (Admerasia vision cache) in incoming. The orders
queue deliberately gives every unclassifiable file a row so the badge can't inflate
invisibly — and its filter listed only `.manifest.json` and `.ai.json`. So the
orphaned cache rendered as a pending **"Unrecognized file"** that outlived its order
and could only be cleared by hand. Worse, once the IO has moved, **nothing else keys
on its name**, so no sweep would ever reclaim it.

**How to apply:**
1. **`backwrite_manifest.SIDECAR_SUFFIXES` is the single list** (`.manifest.json`,
   `.adm.json`, `.adm-legend.json`, `.ai.json`, `.wl.json`, `.overrides.json`). The
   move and the queue's stray-row filter both read it, so they cannot drift. **Add a
   new sidecar suffix there, not at the use sites** — the hand-listed subset is
   exactly what caused this.
2. `move_sidecars()` travels with the IO on entry, and `_sweep_entered_strays()` runs
   it **unconditionally** (not only when the IO is still stray) so already-orphaned
   sidecars self-heal on the next queue load.
3. **A file-producing feature owes the cleanup path too.** Any new `<file>.x.json`
   cache must be registered in `SIDECAR_SUFFIXES` in the same change, or it becomes a
   permanent phantom pending order.

**Same family, found alongside:** `OrderScanner._cache_file()` stored
`.scan_cache.json` in the scanned directory's **parent** to keep it out of the
listing — but the web UI also scans `incoming/Entered` and `incoming/Used`, whose
parent IS `incoming`. So those scans dropped a cache into the incoming root (visible
in `[SCAN] Files found`) and, sharing one path, **clobbered each other's cache every
queue load**, re-OCR'ing already-classified files. Verified live: incoming's cache
held entries for files in `Used/`. Now stored **inside** the scanned directory, with
dotfiles skipped when building the file list. **A "put it in the parent" trick breaks
the moment the parent is itself a scanned directory.**

---

## A Test Reading `os.environ` Inherits the Developer's `.env` — Clear Opt-In Flags in conftest

**Session:** `test_scan_ignores_non_pdf_files` failed in a full run, passed alone (2026-08-07)

**Rule:** `order_scanner._ai_fallback_enabled()` reads `CTV_AI_FALLBACK` from
`os.environ` **at call time**. Lee's `.env` has it ON, and anything calling
`load_dotenv()` — importing `etere_direct_client`, or
`tests/integration/test_customer_repository.py` — leaks it into the whole pytest
process. `tests/integration` sorts before `tests/unit`, so a full run silently
turned the AI fallback on underneath the scanner test (an unidentifiable
`image.jpg` became an AI_FALLBACK order instead of being skipped) while
`pytest tests/unit` alone passed. **A test that passes alone and fails in the suite
is almost never about the test — look for global state, usually env or a module
singleton, set by whatever ran before it.**

**How to apply:**
1. `tests/conftest.py` has an **autouse** fixture clearing every opt-in routing flag
   (`CTV_AI_FALLBACK`, `CTV_CHARMAINE_AI`) via `monkeypatch.delenv(..., raising=False)`.
   Tests must exercise documented DEFAULT behavior, never the developer's `.env`.
   Because it's monkeypatch, the undo also stops a test that calls `load_dotenv()`
   mid-run from leaking into its successors. **Add any new env-gated flag here.**
2. A test wanting the flag ON sets it explicitly (`monkeypatch.setenv`) — and pin
   the behavior on BOTH sides of the flag, which is what the accidental version was
   testing all along without saying so.
3. Beware a stale test NAME masking the real contract: "ignores non-PDF files" was
   never true — the scanner deliberately accepts `.xml`/`.jpg`/`.png`/`.xlsx`/`.xlsm`.
   `.txt`/`.csv` are never candidates; a `.jpg` IS a candidate and is skipped only
   because nothing identifies its order type. Two different rules, one assertion.
4. Verify an ordering fix by running the failing pair in **both** orders, and the
   suite with the flags **pre-set in the ambient env** — not just `pytest tests`.

---

## A Validation Table Keyed to ONE Channel Must Take the Market — DAL Programs Different Dayparts

**Session:** DART Aug/Sept flagged 3 correct lines; Admerasia Seattle unreadable (2026-08-07)

**Rule:** `check_language_window()` validated every order against `CTV_LANG_WINDOWS`
with no market argument. The Asian Channel (DAL) programs **completely different**
dayparts — DAL Cantonese airs 17:00-18:00 where CTV Cantonese airs 19:00-20:00 — so
a perfectly correct DART order had **every paid line flagged** and Lee declined it at
the prompt. The check is universal (runs for all parsers) but the table it consulted
was not.

**How to apply:**
1. `check_language_window(lang, from, to, market=...)`. `find_language_window_issues`
   resolves the market per line (`ln['market']`), falling back to the order's
   `markets` when they're all DAL, then to `_DAL_ORDER_TYPES = {DART, WORLDLINK}`
   (DART's normalized lines carry no per-line market). The message names the channel.
2. **The day-less envelope is now DERIVED, not mirrored.** `_envelope()` unions each
   language's day-aware windows across all days — verified to reproduce all 10 old
   hand-written CTV entries exactly, so the mirror was pure drift risk (the very risk
   the old docstring warned about). Add a language window in ONE place now.
3. Ordered ranges go through `_win_bounds` too, so a DAL post-midnight daypart and a
   wrapping range (`20:00-02:00`) no longer silently fail to match anything.
4. **Sanity check when a validator fires on an order you believe is correct: confirm
   it is validating against the right channel/market before assuming the IO is messy.**
   A validator that flags *every* line is almost always looking at the wrong table.

---

## A Grid Digit Can Be GLUED to Overlapping Text by `extract_words` — Tolerance, Not Font

**Session:** Admerasia Vietnamese Seattle refused entry (2026-08-07)

**Rule:** Admerasia's positional reader took `extract_words()` at pdfplumber's default
`y_tolerance=3`. The program-name column **physically overlaps the first calendar
columns** in these PDFs, and a grid row's spot digits sit only ~1.1pt above the title
text on the same visual line — so a spot over the title merged into one word:
`"Life"` + `"1"` → **`"Life1"`**, which fails `.isdigit()` and **the spot vanishes**.
Row 4 summed to 1 against a printed total of 2 and the reconciliation guard correctly
refused the whole order. It looked like a vision/metadata problem; it was neither.

**How to apply:**
1. `extract_words(y_tolerance=SPOT_Y_TOL)` with `SPOT_Y_TOL = 0.5` — below the ~1.1pt
   digit/title offset, above the intra-word jitter (which is **zero**: a word is one
   text-show operator, so all its chars share an exact `top`).
2. Do **not** discriminate by font, even though the spot digits here are
   Calibri-Bold 3.84 vs Calibri 4.56 body text — that's the "detect by content, not
   encoding trait" trap. Geometry is the durable signal.
3. **Regression-sweep every fixture** before shipping a coordinate/tolerance change
   (the same discipline as the `round()`-clustering lesson): 8 Admerasia PDFs, exactly
   one recovered cell in the target file, the other 7 byte-identical cell-for-cell.
4. **Diagnostic signature:** the guard reports a row short by exactly the number of
   spots that sit in the leftmost calendar columns. Dump `extract_words` in the grid
   band and look for a digit fused to a text word — the digit is present in `.chars`
   but absent from the word list.

**The guard did its job.** It refused a 11-of-12-spot order rather than entering it
short. Trust it — when it fires, find the missing digit, never relax the threshold.

---

## `Contract.contract_number` Is the CODE — an Etere DB ID There Silently Kills Every etere_id Post-Pass

**Session:** two orders entered, only one language prompt (2026-08-07)

**Rule:** Lee entered an H&L and an Admerasia contract in one batch and was asked to
verify line languages for **only the H&L**. `_enrich_results()` resolves
`Contract.etere_id` by looking the `contract_number` up in `COD_CONTRATTO`; the
Admerasia handler put the **DB id** ("3008") there, no code matched, `etere_id` stayed
`None`, and `_catalog_line_languages` — which filters `for c in r.contracts if
c.etere_id` — skipped the contract **without a word**. DART had the identical bug. The
new-parser checklist already says "use gathered code, not DB ID"; the cost of breaking
it is not just a cosmetic summary line, it's every etere_id-keyed post-pass.

**How to apply:**
1. `contract_number` = the code the user confirmed at gather (`_gathered_code()` reads
   `contract_code`/`code`/`order_code`). If the automation returns the DB id, pass it
   as `etere_id=` — the enricher skips contracts that already have one.
2. **Defensive net in `_enrich_results`:** an all-digits `contract_number` that matches
   no `COD_CONTRATTO` is looked up as an `ID_CONTRATTITESTATA`, so a future handler
   regressing this degrades to a wrong summary label instead of a silently skipped
   language catalog. Fix the handler too — the net is not the fix.
3. **Diagnostic signature:** the batch summary prints one contract with `(ID: NNNN)`
   and another as a bare number with no ID. That bare number *is* the ID, and that
   contract has silently skipped every etere_id-keyed pass.
4. A post-pass that iterates `if c.etere_id` is an **invisible** dependency on the
   enricher succeeding. When adding one, decide what it should do when the id is
   missing — printing a warning beats silence.

---

## Injecting a `<script>` Tag by String-Replacing `</body>` Must Target the LAST Match

**Session:** date/time helper consolidation — Make Goods found dead (2026-08-06)

**Rule:** `src/web/app.py` injects site-wide `<script>` tags by byte-replacing the
closing tag (there is no shared base template). It replaced the **first** `</body>`.
`make_goods.html` builds its PDF export as a **JS template literal containing a whole
`</body></html>`** — so the tag was injected **inside that string**. The HTML parser
ends an inline script at the first literal `</script>` regardless of JS context, so
the block was truncated mid-literal and **every function on the Make Goods page was
dead** (date fields, `render`, `exportPDF`). Entirely silent — the page rendered, the
buttons just did nothing. It had been broken since the broadcast-health injection
shipped, and was only found because a consolidation sweep started checking each page's
console.

**How to apply:**
1. Inject at the **last** `</body>` (`html.rfind`), never the first. `</head>` is safe
   as a first match — the document head always precedes body script content.
2. Any new site-wide injection must be checked against templates that build HTML
   strings in JS: `for f in ...; do [ $(grep -c "</body>" $f) -gt 1 ] && echo $f; done`.
   make_goods.html is currently the only one.
3. **Diagnostic signature:** a page's entire JS inert with one
   `Uncaught SyntaxError: Unexpected end of input` pointing inside a template literal,
   while `node --check` on the extracted `<script>` body **passes**. That contradiction
   means the served bytes ≠ the template — something was injected into a string. Always
   compare the *served* page, not the template, when a syntax error makes no sense.

**Related:** a global injected into every page can also collide by name. The shared
`date-input.js` deliberately omits `formatDate` because `billing/monthly_logs.html`
defines its own `formatDate(iso)` display helper — and head-injection loads first, so
the shared one would have won and broken every date on that page. Grep for the name
before adding anything to a site-wide file.

---

## A Colour-Coded IO Identifies the CREATIVE, Not the ISCI — Language Comes From the Airtime

**Session:** Admerasia Chinese IO (Beverages August Window, contract 2999) — Lee 2026-08-03

**Rule:** An Admerasia **Chinese** IO carries **two ISCI legend blocks** (Mandarin +
Cantonese) that reuse the **byte-identical swatch colours** — 4 colours ↔ 8 ISCIs, one
colour per *creative title*, both languages sharing it. `_assign_clusters()` mapped one
colour → one ISCI via an RGB-distance permutation, so it silently picked whichever
language sorted first (**all Mandarin**) and every Cantonese spot got the Mandarin cut.
Nothing in the pipeline modelled language at all.

**How to apply:**
1. Colour identifies the **creative** (title + length). The **language** comes from the
   spot's own `(weekday, TPALINSE.ORA)` via the **day-aware** window table —
   `language_windows.classify_language()`. ISCI = `legend[(colour, language)]`.
2. **Only the day-aware table can do this.** The day-less `_WINDOWS_HHMM` mirror has
   Mandarin `20:00-23:59` *containing* Cantonese `23:30-23:59` — ambiguous by
   construction. Day-aware works because Cantonese is **weekday-only** while Mandarin
   owns the weekend `20:00-23:59`, so a Saturday 22:30 spot is unambiguously Mandarin.
   The day-aware `_CTV_LANG_WINDOWS`/`_DAL_LANG_WINDOWS` now live in
   `browser_automation/language_windows.py`; `orders.py` imports and aliases them, so
   the long-standing two-mirror drift risk is gone — **do not re-declare them.**
3. Keep a single-entry colour map **language-agnostic** so single-language IOs and the
   vision-legend fallback (which cannot report language) behave exactly as before.
   Verified: all 6 VT/FT fixtures produce identical cluster maps, SFO 06 still 69/69.
4. Derive a grid row's duration from the **legend's own :15/:30**, never from FILMATI —
   otherwise a not-yet-ingested creative makes the row duration-less and its spots fail
   with a useless "matched 0 grid rows" instead of "no FILMATI for ISCI X".
5. Pass the legend around as **dicts, not tuples** — this shape grew twice (language,
   then title) and positional unpacking at the consumer breaks silently each time.

**Oracle technique worth reusing:** a partially hand-trafficked contract is a free
regression oracle. Contract 2999 had 8 of 11 spots already assigned by a human; the new
logic had to reproduce all 8 exactly (it did, 0 mismatches) and its 3 failures had to be
exactly the 3 the human also couldn't do (Joy Ride, not yet ingested).

**Also:** Admerasia sometimes prints ISCIs with letter **O for digit 0**
(`MCIMO46526VH`), and can give two creatives the same code (Beverages Launch: Yap
Session and Macro Strawberry Watermelon both `…O47526VH`). Normalise the numeric body
and warn on a duplicate `(language, ISCI)`; never silently traffic it.

---

## A ±Tolerance Built for Interval CONTAINMENT Creates Overlaps When Reused for POINT-IN-TIME Classification

**Session:** same session — caught by an invariant test, not by a failing order

**Rule:** `check_language_window()` compares a whole ordered daypart against a window
and uses `_TOL_MIN = 1` to absorb 23:59-vs-24:00 rounding. Reusing that same `+ _TOL_MIN`
for a *single instant* lookup made **20:00 Monday match both** Cantonese (19:00-**20:00**)
and Mandarin (**20:00**-23:30) — adjacent windows both claiming their shared minute,
which collapses the entire Chinese disambiguation.

**How to apply:** point-in-time window matching is strictly half-open `[start, end)` with
**no tolerance**. Handle the two broadcast-day quirks in the bounds instead
(`language_windows._win_bounds`): an end written `"23:59"` MEANS `24:00` (so 23:59 is
inside), and an hour `< 06:00` is the post-midnight tail (`+24h`) — with the `hi <= lo`
wrap bump, i.e. the same rule pair as `_bcast_window_to_frames`. Assert the invariant in
a test (`for every (day, minute): not {Mandarin, Cantonese} <= classify(...)`) — a
boundary bug like this shows up on exactly one minute per day and no order will ever
reveal it.

---

## Grouping Program Pieces Into Airings: Split on the Piece LETTER Resetting, Not a Time Gap

**Session:** Daily Programming — Vietnamese Drama 10:00 + 12:00 both 0/9 vs 9/9 (Maija, 2026-07-21)

**Rule:** The Daily Programming placement badge groups a market's PGM pieces into
airings (`stampGroupAnchors`) and anchors each group to its first ora, so a show's
drifted last piece still counts as that show (the 7/18 Namaste E fix). The original
split rule was a **>3h time gap** — but the SAME show can air **twice in one day only
~1–2h apart**, reusing the identical piece codes (`VD-SCENTOFGRASS15-0721A..F` at both
10:00 and 12:00). A gap-only rule merged the two airings into one group anchored at
10:00, so the 12:00 window showed **0/9 placed** while 10:00 showed 9/9 — even though
Etere clearly had the 12:00 pieces.

**How to apply:** detect a new airing by the **piece letter not advancing** (`…F → A`),
not by elapsed time. A single airing's letters ascend A→B→…→F and stay one group no
matter how long a break stretches the tail (preserves the drift fix); a repeat restarts
at A and splits. `newAiring = !prev || letter <= prev.letter || gap > 3h` (keep the big
gap only as a backstop for letterless codes). Verify any change to piece→airing grouping
against a day that has the SAME show airing twice, and reconcile per-window market counts
against Etere. The grouping lives in **TWO** mirrored places that must stay in
sync — `daily_programming.html` (`stampGroupAnchors`/`pieceBase`/`pieceLetter`,
the placement badge + replace-modal) **and** `daily_programming_run.py`
(`_group_anchors`/`_piece_base`/`_piece_letter`, which backs the server's
`_is_placed` skip decision for BOTH `_place_once` and `_place_weekend_drama_once`).

**Recurrence (Ashe, 2026-07-29):** the 7/21 fix landed ONLY on the client badge;
the server `_group_anchors` kept the gap-only rule. So the badge showed the second
airing correctly, but the actual "skip if already placed" check still merged the two
airings — when Ashe ran all markets over Pacific markets she'd filled the day before,
`_is_placed` saw the second window as empty and **re-inserted `VD-SCENTOFGRASS21-…`,
duplicating it in every already-filled Pacific market**. Fixed by porting the
letter-reset rule to the server. **Lesson within the lesson:** when a placement/dedupe
rule is fixed on one side (client OR server), grep for the sibling implementation and
fix both in the same change — a client-only fix to a *display* count silently leaves
the *write* path broken.

---

## Commercial-Log "Agency" Column (Z) Is Agency-ATTACHED, Not Commission — and Only Ever "Agency"/"Non-Agency"

**Session:** Crispin backwrote as "direct" in col Z (2026-07-22)

**Rule:** Commercial-log **col Z ("Agency")** holds ONLY `Agency` or `Non-Agency`
(distinct from col Y "Billing Type" = Broadcast/Calendar). Backwrite emitted the
invalid `Direct`, derived from **commission** (`P_AGENZIA > 0`) — so a 0%-commission
**agency** order (Crispin / Bay Area AQMD) was mislabeled. Agency-vs-not is whether
an **agency is ATTACHED** (`CONTRATTITESTATA.AGENZIA > 0` / `header.agency` present),
NOT whether a commission exists. A 0-commission agency is still `Agency`.

**How to apply (backwrite):**
1. From-DB path (`orders.py::awaiting_backwrite_generate`): derive `agency_flag`
   from `ct.AGENZIA` (agency id), not `ct.P_AGENZIA`. Values `Agency`/`Non-Agency`.
2. All emission sites use `Non-Agency` (never `Direct`): `backwrite.py` prefill,
   `transformer.py` parse-SC default.
3. `transformer._canon_agency()` is the single canonicalizer — maps any input
   (incl. legacy `Direct`, blank, `client`) to `Agency`/`Non-Agency`; applied to
   `user_inputs["agency_flag"]` and the EB `Agency?` passthrough so old manifests
   with `Direct` self-correct. Gross-up/broker still key on the real `agency_fee`
   (0 for a no-commission agency → no gross-up, broker fees 0).

---

## Validate Paid-Line Language vs Daypart Before Entry — Catch Messy IOs

**Session:** SAGENT Stormwater Fall 2026 — language/time mismatch (2026-07-22)

**Rule:** A client can order a line in a language that doesn't match its daypart
(this IO booked Filipino & Vietnamese lines at 7p-12a — the Chinese evening slot).
The totals still foot, so a reconciliation check can't catch it; only a
language↔airtime check can. `browser_automation/language_windows.py`
(`check_language_window(language, from, to)`) validates a PAID line's daypart
against the language's actual Crossings airing window (Vietnamese 10a-1p, Filipino
4p-7p, Chinese 6-8a + 7p-12a, South Asian 1p-4p, Korean 8-10a, Hmong 6-8p WE).
**This is UNIVERSAL, not per-parser:** the orchestrator runs it for EVERY order
before gather (`Orchestrator._confirm_language_windows` →
`parser_bridge.find_language_window_issues`), which parses via the shared
normalizer, derives language from the normalized line (description keyword scan),
normalizes the daypart to HH:MM, and lists any mismatches with a continue/abort
prompt. **ROS/bonus lines are exempt** (they run across the whole window). It's
best-effort — never blocks entry on a validation/parse error.

**Keep in sync:** `language_windows.py` MIRRORS `_CTV_LANG_WINDOWS` in
`src/web/routes/orders.py` (the traffic-assignment source of truth) — update both
if programming windows change. (Japanese has no CTV window there, so it's not
validated.)

---

## Multi-Page PDFs: Read EVERY Page, and Parse Columnar Tables by Word Coordinates — Not Text-Flow

**Session:** SAGENT Stormwater Fall 2026 — only page 1 entered (2026-07-22)

**Rule:** `parse_sagent_pdf` did `pdf.pages[0].extract_text()` — it read ONLY page 1,
silently dropping lines 11–24 (a 4-page order → 10 of 24 lines, 405 of 945 spots).
Worse, its text-flow line-regex mis-read the GaleForce columns: the layout puts the
**language under the "Network" column and the market under "Program"**, and the
language often **wraps onto the time-period line above** the data row. Text
extraction interleaves those, so markets came out as "CHINESE"/"FILIPINO" (→ NYC at
entry), languages defaulted to Chinese, and every daypart collapsed to 6a-11:59p.

**How to apply:**
1. **Always iterate `pdf.pages`**, never `pages[0]`. Header/column headers repeat
   per page, so header fields still resolve from the concatenated text.
2. **Parse columnar order grids by word x-coordinates** (`page.extract_words()`),
   not by splitting `extract_text()` on spaces. Group words into visual rows by
   rounded `top`, then read each field by its x-band (line# <90, len 130–185, rate
   180–220, language 220–315, market 315–405, weekly spots x≥405). Time period is
   the row directly above; days are the row below. This survives per-page x-shifts
   (page 3 was shifted ~14px yet parsed clean). See `_extract_sagent_lines`.
3. For weekly spots, take the **first n_weeks integers** in the spot region
   (n_weeks from the reliably-parsed week headers) — do NOT match flaky per-page
   header day-number centers (page 3's header dropped its first day number).
4. **Reconcile against the order's own grand total** ("Total $59,675.00 945") and
   **raise** on mismatch — a dropped page/line must refuse to enter, never enter
   partially (same family as the SCWA/Brentan totals lesson). This is the guard
   that would have caught the original bug at parse time.

**Note:** the already-entered contract from the buggy run (SAGENT 2971) has only 10
wrong lines — it must be deleted and re-entered, not patched.

---

## Agency Commission Comes From the ANAGRAF Link — Never Clobber a Legitimate 0% With a 15% Default

**Session:** Crispin / Bay Area AQMD parser (2026-07-22)

**Rule:** `create_contract_header(lookup_customer_defaults=True)` pulls the agency
commission from ANAGRAF via `get_client_defaults` = `ISNULL(agency.Commissione, 0)`
— exactly what Etere's client-select auto-populate does. The header must use that
linked value verbatim. The old code did `agency_pct = (defaults.get("agency_pct")
or 15.0) if agency_id else 0.0`, and `0.0 or 15.0` = 15.0 — so a client whose
agency genuinely has **0% commission** (Crispin LLC / BAAQMD, agency 446) got a
**15% commission forced onto the contract**. Lee: "just use the commission that
is linked to the client/agency. I don't want to automatically override that part."

**Fix:** `agency_pct = float(defaults.get("agency_pct") or 0.0) if agency_id else
0.0` — trust the ANAGRAF value (already ISNULL→0). Blast radius is nil for the
other agency parsers: every agency in `AGENCY_IDS` except Crispin has
Commissione=15 in ANAGRAF, so only a true-0 agency changes (which is the point).

**How to apply:** never `x or DEFAULT` on a numeric that has a meaningful 0 (same
family as the iGraphix net-rate and the day-bits "0 or default" traps). For
commission specifically, ANAGRAF is the source of truth; if a value looks wrong,
fix it in ANAGRAF/Etere, don't special-case it in a parser. See [[pi-supporto-binding]]-style
"identity/value, not a default" discipline.

---

## A PI/PSA Filler's Playout Binding (TPALINSE.SUPPORTO) Is the FILE_ID — Never the DESCRIZIO

**Session:** WDC PIs airing in some blocks but not others — Maija (2026-07-22)

**Recurrence via Etere's OWN SP (Fill & Finish first live row, LAX 2026-08-28):**
`sch_UpdateSupportAndProperties` — the call our FCC daily-ID job makes after
`Traffic_InsertEvent` — writes SUPPORTO as prefix + **COD_PROGRA**
(`0ETX      STATIONID_NEW_GENERIC`), while every one of the ~1,300 Station IDs
that aired the prior week carries prefix + **FILE_ID** (`0ETX      ID - NEW -
GENERIC`). For any asset whose COD_PROGRA ≠ FILE_ID the SP's binding is wrong.
Rule: after ANY insert path, overwrite SUPPORTO from FS_FILMATI.FILE_ID yourself
(`finish_apply._supporto`) and verify the first live row against an AIRED
sibling's SUPPORTO before calling a write path done. Also: `Traffic_InsertEvent`
adds a `trafficPalinse` row (ID_ContrattiRighe=0) that hand-placed IDs/PSAs never
have (24 of 1,898 ID rows) — delete it to match the convention.

**Rule:** `TPALINSE.SUPPORTO` is the playout clip binding: `<prefix> + FILE_ID`
(e.g. `0ETX      PI-493-030`), where `prefix` = `FS_METADEVICE.LEGACY_BASESUPP`
and `FILE_ID` = `FS_FILMATI.FILE_ID`. It is what the CIB uses to find the media
file. Two break-optimization sites in `orders.py` built it as
`("0ETX      " + DESCRIZIO)[:30]` instead — the filler INSERT (missing-materials
blacklist+replace) and `_bo_apply_pi_replacement` (PI/PSA creative swap). For a
PI the description overruns the field (`0ETX      PI-493-030: Ship of `), the
playout server can't resolve it, and the event goes **STATUS='E'** (red-X in Exec
Editor, yellow triangle) and **never airs** — while the identical PI airs fine in
every block whose binding was built correctly. It looks market-specific (WDC had
the most) but it's really per-placement and spans all markets (~1-4 filler
rows/day). The checksum is a **red herring** here: stored==live on all these
rows; the failure is the binding string, not the checksum.

**How to apply:**
1. Any code inserting/updating a filler TPALINSE row must build SUPPORTO from
   `FS_FILMATI.FILE_ID` (helper `_pi_filler_supporto(cur, filmati_id, desc)` in
   `orders.py`), mirroring the auto-assign convention — never from DESCRIZIO/TITLE.
   FILE_ID for a PI equals the `PI-nnn-nnn` code = DESCRIZIO before the first `:`.
2. Diagnostic signature for "spot won't air but file is fine": query
   `SUPPORTO LIKE '%[:]%'` (a colon should never appear in a valid binding) — every
   such row is STATUS='E' or not-yet-aired, none ever air.
3. Remediate live corrupted rows by recomputing `prefix + FILE_ID` for
   `LIVELLO=0 AND DATA >= today`; ORA/XORDER/checksum untouched.

---

## "Penny-Accurate" Means EXACT — Never Round the Gross Rate; Feed the Backwrite the NET Rate + `rates_are_net`

**Session:** iGraphix (Sky River) backwrite gross-up (2026-07-21)

**Rule:** The backwrite's Net Amount is what we bill the customer and MUST equal
the IO net to the penny — a green check within $0.02 is a FAIL, not a pass (Lee:
"we aren't trying to get CLOSE"). The killer is rounding the *gross* rate to 2
decimals: `$52.94 × 18 × 0.85 = $809.98`, not `$810.00`. The clean number is the
*net* per spot ($45.00); the gross ($52.9411…) must stay full-precision so the
round-trip lands exactly. Excel money cells are formulas (no `round()`), so the
ONLY thing that must carry full precision is the gross unit rate.

**What was wrong:** iGraphix pre-grossed and rounded (`round(net/0.85, 2)`) and
its normalizer omitted `rates_are_net` + handed the backwrite that rounded gross.
So the backwrite skipped gross-up and billed the rounded value. Unlike
intertrend/mediasol, whose io_detail carries the **net** rate + `rates_are_net=True`.

**How to apply — any net-rate agency parser:**
1. `_normalize_<agency>` (parser_bridge.py) MUST set `"rates_are_net": True` and
   put the **net** per-spot rate on each line (`net_total / paid_spots`), NEVER a
   pre-grossed/rounded rate. Etere entry can still store gross — that's separate.
2. The backwrite's auto gross-up fallback maps `{Etere-rounded-gross: net}` (not
   `{net: net}`) so BOTH the SC tab (reads IO net) and the run sheet (reads
   Etere's rounded gross) gross to full precision. Key = `round(net/(1-fee), 2)`
   reconstructs exactly what Etere stored.
3. Verify by generating the real from-DB workbook and asserting SC net AND run
   sheet net both equal the IO net to the penny — reconstruct via the formulas,
   don't eyeball the cached cell.

**Related:** the estimate/purchase number lives in `CONTRATTITESTATA.CUSTOMERREF`
(the customer order ref set at entry) — pre-fill the backwrite Estimate field
from there, not a `\d{4,}` scrape of the description.

**Two backwrite generate paths exist — fix BOTH or you've under-fixed:**
1. **Manual `/backwrite` page** — `backwrite.py::backwrite_generate` (from-DB
   search or uploaded CSV). Gets `io_detail` ONLY if the user also drops the IO
   PDF; the from-DB search alone has no IO, so it can't know rates are net.
2. **One-click** — `orders.py::awaiting_backwrite_generate` (awaiting-queue →
   review modal). Reads the manifest, which carries `io_detail` + `rates_are_net`,
   so it grosses up automatically. Modal contact/estimate prefill is a THIRD
   endpoint (`awaiting_backwrite_contact`).
The gross-up mapping and the CUSTOMERREF estimate fallback had to be applied in
all of these. When you change backwrite gross-up/estimate logic, grep for every
`gross_up`/`rates_are_net`/`estimate` site across `backwrite.py` AND `orders.py`
— they were copy-paste siblings and drift silently.

---

## Reusing a CSS Class Means Inheriting Its Background Assumption — Check Contrast Against the Target Row

**Session:** Break Opt log-style refresh button (2026-07-20)

**Rule:** "Reuse existing classes" (the standing styling rule) is about not
inventing parallel styles — it does NOT mean a class is safe in every context.
`.expand-btn` uses `--text-muted`, tuned for the light `.log-meta` bar; dropped
onto the dark `--nord2` `.prg-header` it was unreadable. Every color token in
this codebase encodes an assumed background.

**How to apply:** when placing a reused class on a differently-colored surface,
compare its color tokens against what its new siblings use (here: `.prg-stat`
and `.prg-chevron` both use `--nord4`) and add a small per-context override
(`.prg-refresh { color: var(--nord4); }`) rather than a new button class.

**Same trap with the semantic TEXT tokens (2026-07-21, multiviewer toolbar):**
`--text-primary` (=`--nord0`), `--text-secondary`/`--text-muted` (=`--nord3`) are
all dark Polar-Night colors tuned for the app's LIGHT card backgrounds
(`--bg-primary`=`--nord6`). On any DARK surface — the header, or a `--nord0/1/2`
toolbar — they're dark-on-dark and vanish. On dark surfaces use **`--nord4`**
(what the header/`.agent-status` use), optionally at reduced opacity for a muted
look. Rule of thumb: `--text-*` tokens ⇒ light backgrounds only; `--nord4` ⇒ text
on dark.

---

## Never Build an Inline `onclick` From Interpolated Data — HTML-Escaping It Still Breaks the JS

**Session:** As-run contract dropdown, Admerasia (2026-07-20)

**Rule:** `onclick="fn('${esc(value)}')"` is broken for any `value` containing
`'` even WITH an HTML-escaper. Browser order is: HTML-decode the attribute
first, THEN parse as JS. So `esc`'s `&#39;` decodes back to a literal `'`
before the JS engine sees it, terminating the string → syntax error → the
handler silently does nothing. It only "works" until the first apostrophe:
BVFL contracts opened, Admerasia ("McDonald's …") didn't. HTML-escaping (for
text nodes) and JS-string-escaping (for code) are different jobs; an attribute
that is JS needs the latter, and inline handlers make that nearly impossible to
get right.

**How to apply:** render items with a `data-idx` (or data-id) only, then
`el.addEventListener('click', () => fn(rows[+el.dataset.idx]))` — pass the
value straight from the JS array, never embed it in markup. Grep the codebase
for `onclick="[^"]*\$\{` to find siblings (there's a latent one in this page's
spot-search — spot codes just never contain apostrophes yet).

---

## Idempotency Checks Must Key on Identity, Never on Position — Schedule Rows Drift

**Session:** FCC ID duplicates in SFO/CVC (2026-07-20, Maija report)

**Rule:** A "did I already place this?" check must match on immutable identity
(DATA + COD_USER + ID_FILMATI), never on where the row currently sits.
TPALINSE.ORA is recomputed by every start-time rebuild: a programming gap ahead
of the end-of-day FCC ID pushed the tail past midnight (ORA 24h+, same DATA),
the `ORA<24:00` dedupe stopped seeing the placed ID, and each daily sweep of the
date (today→+2 = up to 3 passes) inserted another copy. Position bounds belong
on the PLACEMENT target (which break to insert into), not on the dedupe.

**How to apply:** any sweep/retry loop that inserts schedule rows checks
existence by identity columns alone. If a position filter seems needed to
disambiguate, the asset is doing double duty — split the assets instead.

---

## "Like Page X" in a User Request Means Page X's Whole Interaction Pattern, Not the One Widget Named

**Session:** Break Optimization log-style redo (2026-07-16)

**Rule:** When a team email asks for something "similar to <existing page>", the
reference page's complete interaction model is the spec — its layout, its
expand/collapse verbs, AND its zero-click loading — even if the email names a
specific widget. The 7/15 email said "show/block selection similar to Edit
Logs, where we can use a dropdown selection" — we took "dropdown" literally and
bolted a dropdown onto the classic page (e0a1e49). What they meant was: make
the page WORK like Edit Logs (log-style show list, click to expand, auto-load
on pill/date, no Load button).

**How to apply:**
1. Before building from a "like X" request, open page X and list its
   interaction verbs (how it loads, selects, expands). The request almost
   certainly wants all of them, not just the named control.
2. When the interpretation is ambiguous, ship the trial as a NEW card next to
   the existing one (as done here) so the team can compare and choose —
   cheaper than guessing wrong twice on a live page.
3. Related: "New UI Features Extend the Page's Existing Interaction Pattern"
   below — same principle from the opposite direction.

---

## New UI Features Extend the Page's Existing Interaction Pattern — Never Add a Parallel One

**Session:** Daily Programming replace-piece (2026-07-16)

**Rule:** When adding a capability to an existing page, express it through the
interaction the team already knows, scoped by the page's existing selections.
Lee's verdict on v1 ("way too overcomplicated") came from three parallel
concepts: a separate card, its own Load button, and per-action market
checkboxes — when the page already had row→modal as its verb and network/market
pills as its scope. v2 (row hint '↻ replace piece' → same modal → pick piece →
pick file) was ~30 lines SMALLER and instantly accepted.

**How to apply:**
1. Before designing, name the page's existing verb (here: "click a line, the
   modal opens") and its existing scope selector (market pills). The feature
   must reuse both; new selectors need justification.
2. Design for the common case; drop edge-case affordances (partial-market
   checkboxes) when the existing scope selector already covers them.
3. **Group lists by what the USER means, not by DB identity.** v2's modal
   listed one row per (air time, file) — but air times drift minutes between
   markets, so one piece rendered 8 times. The user's mental object was "piece
   B of this show" = the FILE within the show's window; per Lee the operation
   is "find this file, swap with this file, in the chosen markets, only in the
   time period of the show in question."

---

## Unscheduling a Placed Spot Means BOTH Tables — a trafficPalinse-Only Delete Creates a Ghost Spot

**Session:** WL 2919 Coterie revision (2026-07-14)

**Rule:** A placed spot is `trafficPalinse` (contract side, what SE shows) + `TPALINSE`
(playlist side, what EE airs). Deleting only `trafficPalinse` makes the spot vanish
from SE while it still AIRS from EE — an unbilled ghost that also violates separation.
`_unschedule_spots` in `worldlink_automation.py` did exactly this; 45 live ghosts were
found (and deleted) on 2026-07-14, and historical ones trace back to 2022 (~2,900 —
manual EE/SE ops cause them too; leave aired ones alone, they're the as-run record).

**How to apply:** any delete of a scheduled spot must remove the `trafficPalinse` row
AND its `TPALINSE` row (collect `id_tpalinse` first). Detection: `scripts/check_ghost_spots.py`
lists future COM rows with no trafficPalinse backing; the WL automation runs the same
check as a watchdog after every commit. Re-attributing a spot to another line is the
opposite operation: update `trafficPalinse.ID_ContrattiRighe` only (TPALINSE carries
no line reference) — see `_apply_reattribution` for the revision rebook flow.

---

## Daily Programming Placement: Never Trust Traffic_InsertEvent's XORDER — Conform the Window Yourself

**Session:** Korean News 7/10 five-market failure (2026-07-10)

**Rule:** `Traffic_InsertEvent` derives a new row's XORDER from its ORA-neighbors **including soft-deleted (LIVELLO=666) rows with stale xorders**. Three interacting hazards break placement into an hour that has sat unplaced for a while:
1. **NOOP gap-fillers:** Etere's playlist generation drops a `NEWTYPE='NOOP'` filler (~50 min) into any unfilled program hole. A live NOOP in the window corrupts the rebuild — every 7/10 market with an active 8:10a NOOP failed, every market without one placed clean. `_clear_noop_fillers()` now soft-deletes (666, Etere's own pattern) overlapping NOOPs inside the placement transaction before inserting parts.
2. **BO-packed spots:** running Break Optimization on a program-less hour collapses ALL the hour's break spots into one contiguous pod at the top (the whole hour is one non-fixed block). TPALINSE.ORA then no longer reflects break membership — but **`trafficPalinse.offset` still holds each spot's true break position** (BO never touches it). Use it as the sort key to put spots back with their breaks.
3. **Stale-xorder inheritance:** parts inserted over dead NOOPs literally copy the dead row's years-stale xorder → parts interleave wrongly with the pod → `sch_rebuildStartTimeSchedule` chains a nonsense order → "verify failed: overlap at element N".

**Fix (in `daily_programming_run.py`):** after inserting parts + bumpers, `_conform_window_xorder()` reassigns the window's active rows the SAME multiset of xorders they already hold, ordered: open bumper, part1, break-1 spots (by trafficPalinse.offset), part2, …, close bumper after last part. Same-multiset reassignment can't collide with anything outside the window. The rebuild then produces the HOU-style interleaved layout.

**Deadlocks (1205) across market threads:** identical fixed retry delays re-collide in lockstep — four markets all slept exactly 1s and exhausted 3 attempts together. Retries need **jitter** (`_DEADLOCK_RETRY_SECONDS * attempt + random.uniform(0.1, 1.5)`, 5 attempts) and the rebuild SP (the most lock-hungry statement) is serialized process-wide via `_REBUILD_LOCK`. For manual remediation runs, just go sequential — one market at a time never deadlocks. Even jittered retries can exhaust under heavy contention (2026-07-13: HOU+SFO lost 5/5 while 7 sibling threads ran — `_REBUILD_LOCK` only serializes rebuild-vs-rebuild, not one market's rebuild vs another's inserts), so the run route now does an automatic **solo second pass**: `run_market` re-tags exhausted results `_deadlock: True`, and the route reruns those pairs one at a time after all threads finish.

---

## The Broadcast Day Runs 06:00→30:00 — Post-Midnight Is 24:00–29:59 on the SAME Date

**Session:** Daily Programming late-night / DAL midnight feedback (2026-07-06)

**Rule:** Etere stores traffic block/segment offsets (`traffic_scheduleblock.offset + traffic_segment.Offset`) and `TPALINSE.ORA` as **frame-of-day at 29.97fps**, but the broadcast day spans **06:00 → 30:00**. So the post-midnight tail (00:00–05:59) is stored at **24:00–29:59** frames, on the **same `DATA`** as the 06:00 start (NOT on the next calendar date, and NEVER at 0–6h — nothing lives there). Verified live: min block offset = 647352 (=6.000h) for both CTV and DAL; placed post-midnight rows carry ORA up to ~29.9h on the 06:00-start DATA.

**The bug it caused:** naive `(H*3600+M*60)*fps` conversion put a 01:00 block at ~1h (where no segments exist → "0 breaks / too many pieces", silent refusal) and a block ending at midnight got `end="00:00"` → `hi=0 < lo` → empty window → silent no-placement (CTV 11:30p; weekend 10:30p final show).

**How to apply — any time you convert an HH:MM to frame-of-day for an Etere schedule query:**
1. If `hour < 6`, add 24h (post-midnight tail). `_frames()` in `daily_programming_run.py` does this.
2. For a [start,end) **window**, after the shift, if `hi <= lo` add a further 24h — that's the day's final block ending at 06:00 next morning (30:00). See `_window()`.
3. Keep `DATA` = the 06:00-start date; do NOT roll it to the next calendar day for post-midnight content.
4. This lives in several places (keep them in sync): Daily Programming run engine (`_frames`/`_window` in `daily_programming_run.py`), the `program-pieces` preflight (imports `_window`), the client-side badge math in `daily_programming.html` (`hhmmToFrames`/`hhmmWindow`), and the shared `_bcast_time_to_frames(t, fps)` in `orders.py` that all TPALINSE.ORA converters now route through.

**Audited + fixed (2026-07-06, commit see git):** the traffic-assign filters (`_hhmm_to_frames`), the DAL language windows in `_build_spot_filter` (which literally contain post-midnight ranges — Mandarin `00:00–01:00`/`02:00–05:30`, Cantonese `01:00–02:00`/`05:30–05:59`), the program-spot fill (`_time_to_frames`), and the break optimizer (`_bo_time_to_frames`) were ALL affected — they now delegate to `_bcast_time_to_frames`. This had been silently dropping every DAL post-midnight spot from language-window assignment (verified: Mandarin 00:00–01:00 matched 0 → 140 spots/week; 02:00–05:30 matched 0 → 436). CTV windows are all ≥06:00 so were unaffected either way.

**Note:** the inverse display converters (`_bo_frames_to_hhmm`, `_frames_to_ampm`) render a post-midnight ORA as "27:00"/"3:00 AM" style broadcast time — that's cosmetic, not a matching bug; leave unless a display looks wrong.

**Recurrence (Ashe, 2026-08-02) — rule 1 without rule 2 is HALF a fix.** The 7/06
sweep routed every converter through `_bcast_time_to_frames` (the per-time <6h shift)
but converted **window pairs as two independent times**, so rule 2 (the `hi <= lo`
bump) was never applied outside Daily Programming. Break Optimization (Log Version)
showed **"0 breaks / No commercial breaks in this show"** for DAL's final program of
every day — 7/27 `5:30–6:00` Phoenix News Express P2, 8/01 `4:30–6:00` The Music
Project S2 — because start `05:30`→29:30 while end `06:00` stayed at 6:00, so
`ORA >= 29:30 AND ORA < 6:03` matched nothing. It looked DAL-only purely because
CTV's logs have no show ending at 06:00. Only the day's LAST show is affected;
`4:00–5:30` (both post-midnight, no wrap) worked fine all along, which is why it
went unnoticed.

**How to apply:** a start/end pair from a program/grid row must go through the
shared **`_bcast_window_to_frames(t_from, t_to, fps)`** in `orders.py` (mirrors
`_window()` in `daily_programming_run.py`) — never two `_bcast_time_to_frames`
calls. Fixed at both consumers of a log program window: the BO endpoints
(`/break-optimization/load` + `/bulk-apply`, via `_bo_window_to_frames`) and the
log spot-time fill (`_mc_fill_program_spots`). Ad-hoc user filters
(`_build_spot_filter`) and the pre-split language-window tables are per-time by
design and stay on `_bcast_time_to_frames`. **Diagnostic signature:** a time-window
feature returning exactly zero rows, with no error, only for the day's last show.

---

## Multi-Flight Traffic PDFs: Track Dates Per-Spot, Never at the Instruction Level

**Session:** HL traffic parser — Toyota June 2026 ACM #13933 R1 (2026-06-26)

**Rule:** A single traffic-instruction PDF often carries **several flights** (e.g. 6/2–6/8, 6/9–6/30, 6/30–7/6), each with its **own ISCI per dialect** (the same dialect gets a different creative each flight). The flight dates therefore belong on the **spot/ISCI**, not on the instruction. Two failure modes if you store one date range for the whole PDF:
1. Every spot inherits the header's full-flight range, so each creative is matched against the entire flight instead of its own window.
2. Downstream code that keys a `dialect → filmati` map collapses the flights — the last creative for a dialect overwrites the earlier two, and the right spots get the wrong creative.

**How to apply (traffic parsers + the `/traffic/assign-assets` route):**
1. Put `date_from_sql/date_to_sql/start_date/end_date` on the **spot** dataclass (`HLTrafficSpot`), parsed from that spot's own row. Keep instruction-level dates only for display (use the header EXACT FLIGHT DATES = full flight).
2. In the route, group found spots by `(system_dialect, date_from, date_to)` → one `dialect_assignment` per group, and put that group's **own** date range into `filters` (`date_from`/`date_to`). `_build_spot_filter` then counts/assigns only spots inside that window. Never reduce to `{dialect: filmati}`.
3. Many HL rows are **single-line** (ISCI, title, `(Dialect)`, dur, rotation, dates all on one line) — scan the **whole block** (line 1 + following) for the date pair, and take the **first** pair (a trailing `@ 12 NOON`/`@ 1201p` annotation must not shift the window).
4. **Multi-page bleed:** block-grouping that appends every non-ISCI line to the current block lets the last ISCI on a page absorb the *next* page's header (incl. its `EXACT FLIGHT DATES`). Close the open block on end-of-table markers (`Link to new spots`, `Page N of`).
5. The same collapse pattern exists in the **RPM** branch (`format == 'rpm'`) of the route — fix it the same way if a multi-flight RPM PDF appears.

**Verify:** parse → assert N distinct date windows; then run the real per-group COUNT query against the matched contracts and confirm the same dialect routes *different* spot counts to *different* windows.

---

## Format Detectors Must Not Hinge on a Single Encoding Trait — Detect by Content, Not Font

**Session:** Toyota CRSF-TV Q3 BDR parse failure (2026-06-24)

**Rule:** When a detector keys on an *encoding* artifact (custom font, `(cid:)` garble, rotation, image-only page) rather than the *content* of the document, it silently misroutes the day the source system changes its export. A new, valid file fails with **zero estimates and no error** — the worst kind of failure.

**What happened:** `is_bdr_pdf()` detected H/L Buy Detail Reports *only* by a Type3 custom-font fingerprint. H/L started exporting clean-text BDRs (normal embedded font, extractable text). Those fell through to the generic `hl_parser`, which can't read the BDR layout → returned `[]` silently. Compounding it, `parse_bdr_pdf` was OCR-only (always rasterize + rotate), so even called directly it produced garbage on the un-rotated clean PDF.

**How to apply:**
1. **Detect by content with a self-validating signature.** Add a text-based check (`is_bdr_text`) that matches the actual *row layout* (BDR rows are day-pattern-first, no line number, no daypart code). A layout guard means it won't steal sibling formats (`hl_parser` rows are line-numbered) even when header markers ("Buy Detail Report", "H/L Agency") overlap. The font-fingerprint check may be kept as a cheap pre-check **only if it self-validates too** — 2026-07-17: a DocuSign-signed RWNY proposal was misrouted to HL_BDR because DocuSign stamps embed Type3 ArialMT fonts and `is_bdr_pdf()` treated any page-1 Type3 as proof. The guard: a genuine Type3 BDR extracts as control-character garbage, so readable page text → NOT a BDR. Also bump `_SCAN_CACHE_VERSION` whenever detection logic changes — the scan cache keys on file signature only, so stale classifications survive code fixes.
2. **Text-source must degrade gracefully.** Parsers that OCR should try `pdfplumber.extract_text()` first and fall back to OCR only when the text is `(cid:`-garbled or < ~50 chars. Never assume a format always needs OCR.
3. **Order matters:** check the more-specific format before the format it shares markers with (`_is_bdr` before `_is_hl_partners` in `detect_from_text`).
4. **A station/market name is never an agency signature — and when the user
   names the definer, use THAT** (Wallrich xlsx, 2026-08-21, two corrections).
   The first Wallrich xlsx detector matched any `KBTV` cell; Lee objected (KBTV
   is the *station* — any agency's Sacramento buy could carry it). Tightening
   to Strata-layout+KBTV was still rejected: "anyone can use Strata layouts.
   A CLEAR definer of a Wallrich order is the client SD15 … or simply SMUD."
   Final rule keys on the client (filename `SMUD`; cell `SMUD` or exact
   `SD15`), with the other-client Strata/KBTV negative pinned in a test
   (5e76efa). Lesson: ask which token the user considers the identity of an
   order type before inventing a structural signature — the client/advertiser
   is usually more durable than station, layout, or template branding.

**Why:** Two parsers (`hl_parser`, `hl_bdr_parser`) share the same agency markers and differ only in table layout. The discriminator must be the layout, available in the extractable text — never a transient encoding trait.

---

## New Parser Checklist for Direct DB (All Future Parsers Are Direct DB)

**Session:** Pink-pill testing sweep (2026-06-09)

**Rule:** We no longer write Selenium order-entry parsers. Every new parser is direct DB. When building one, apply ALL of the following from the start — these were all discovered as bugs during the 2026-06 testing sweep:

### 1. Duration: always pass `str(seconds)`, never `f":{sec:02d}"`
`_duration_str_to_seconds()` in `etere_direct_client.py` splits on `:` — a leading colon (e.g. `":30"`) produces `['', '30']` and `int('')` crashes. Pass bare integer strings: `str(spot_duration)` (e.g. `"30"`, `"45"`).

### 2. `contracts` list must be populated on success; use gathered code, not DB ID
`ProcessingResult.contracts` must contain at least one `Contract(contract_number=order_code, order_type=OrderType.X)` when `success=True`. Never return `contracts=[]` on success — the final summary will show "0 contracts created" even if Etere has the data.

Use the **gathered contract code** (from `user_input.get('contract_code')`), not the Etere DB integer ID. Pattern:
```python
inp = order.order_input
label = (inp.get('contract_code') if isinstance(inp, dict) else None) or str(contract_id)
contracts = [Contract(contract_number=label, order_type=OrderType.X)] if success else []
```

**Do NOT set `etere_id` yourself** (2026-06-25). `OrderProcessingService._enrich_results()` runs once per batch and auto-resolves the Etere DB contract ID from each contract's code (`CONTRATTITESTATA.COD_CONTRATTO`), so every parser's pre-close and final summaries print `Contract <code> (ID: NNNN)` like WorldLink — for free. Just keep returning the gathered code as `contract_number`; the ID appears automatically. (WorldLink still sets `etere_id` itself; it's skipped by the enricher, which only fills `etere_id is None`.)

**Multi-contract parsers: each contract's code/description must carry its OWN
identifier, by SUBSTITUTION not suffix** (2026-08-24, HL ACM Q4 Toyota 3026-3028).
The gather suggests a code built from the FIRST estimate (`HL Toyota 13937 CV` —
number embedded by `resolve_defaults`); gluing ` Est N` onto that gave estimates
2-3 the wrong number plus a redundant suffix (`HL Toyota 13937 CV Est 13938`).
Use the shared `customer_defaults.per_estimate_text(text, first_est, est)` —
it swaps the first identifier for each contract's own and falls back to a
suffix only when the typed code lacks the number (keeps codes unique). Apply it
to code AND description, and print a gather-time preview of every contract's
code so the user confirms what will actually be written (the BDR automation's
pattern — it had this right first; HL drifted).

**Multi-contract parsers (one PDF → many contracts): the automation must RETURN the codes** (2026-06-26). A `bool` return throws away which contracts were created, so the handler can only report `contracts=[]` → "0 contract(s)" even on success. For any parser that loops creating >1 contract (Impact = per-quarter, H&L = per-estimate, Charmaine = per-order), change `process_X_order()` to return `list[str]` of created codes (append the code right after each header is created). Empty list = failure — **truthiness is preserved**, so existing `success = process_X_order(...)` callers keep working. Handler then does `contracts = [Contract(contract_number=c, order_type=OrderType.X) for c in codes]`. For autocommit parsers (Charmaine, H&L) return the codes actually created (reflects DB reality even on partial failure); for single-transaction parsers (Impact) the list is all-or-nothing. If the automation already returns the code (e.g. DART returns the contract number), just **use it** instead of discarding it.

**Audit technique** (2026-06-26): to find this bug across all handlers, AST-walk `_process_*_order` methods and flag any `ProcessingResult(...)` return where `success` is not literal `False` but `contracts` is `[]` / never appended-to. This sweep found 5 affected parsers (HL, Impact, RPM, DART, Charmaine) after the iGraphix report. (It also surfaced that the Impact handler was passing a non-existent `user_input=` kwarg to `process_impact_order` — a latent `TypeError` — now `pre_gathered_inputs=`.)

### 3. `booking_code` must always be explicit — never rely on `is_bonus`
Pass `booking_code=10 if is_bonus else 2` to every `add_contract_line()` call. `is_bonus=True` only sets the scheduling type; it does NOT set the booking code.

### 4. Customer ID must be resolved in `gather_*_inputs()`, not during processing
All user-interactive prompts (customer ID, order code, description) belong in the upfront gather function registered in `_INPUT_GATHERERS`. If `_resolve_customer_id()` or any `input()` call fires during processing, move it to gather.

### 5. `gather_*_inputs` must return a dict; service uses `user_input.get('key')`
Service methods check `isinstance(inp, dict)` and use `.get('order_code')` / `.get('contract_code')`. The gathered dict must use the correct key so the contracts-list builder can find it.

### 6. Yes/Enter at a date-override prompt must keep the original date
Pattern: `actual = raw if raw and raw.lower() not in ('y', 'yes') else original`. Never do `actual = raw if raw else original` — typing "yes" stores the string "yes" as the date.

### 7. Service/bridge registration — 3 files, all at once
**Updated 2026-06-10:** All new parsers are direct DB. Add to ALL THREE simultaneously:
1. `_DIRECT_DB_ORDER_TYPES` in `order_processing_service.py`
2. `_DIRECT_DB_KEYS` in `parser_bridge.py`
3. `_DIRECT_DB_TESTED_KEYS` in `parser_bridge.py`
Missing step 1 causes a browser session to be opened. Missing steps 2–3 hides the parser from the web UI entirely.

### 8. `gather_*_inputs` must prompt for contract code and description
Every gather function must ask the user for the contract code and description before processing starts. Never let the processing function prompt for these or auto-generate them silently.

### 9. All gather prompts must use the bracket-default pattern
Every user-facing prompt in a `gather_*_inputs` function must use this pattern:
```python
raw = input(f"  Contract code [{default_code}]: ").strip()
contract_code = raw or default_code
```

**Never** use the two-step "Use default? (y/n)" / "Enter X:" pattern — it doubles the keystrokes and is inconsistent across parsers.

### 10. Do not inline-prompt for separation in `gather_*_inputs`
The orchestrator calls `_confirm_separation(inputs)` after every `gather_*_inputs` call. Any parser that also prompts for separation inside `gather_*_inputs` causes a **double prompt**.

Just set `inputs['separation'] = separation` from the customer DB defaults and return it — the orchestrator handles the user-facing confirmation.

### 11. CustomerRepository API — always use the entity pattern, never dict-style upsert

**Session:** ACM parser (2026-06-11)

The `CustomerRepository` class requires these exact call patterns:

**Lookup:**
```python
import os
from src.data_access.repositories.customer_repository import CustomerRepository
from src.domain.enums import OrderType

if not os.path.exists(CUSTOMER_DB_PATH):
    return None
repo = CustomerRepository(CUSTOMER_DB_PATH)
cust = repo.find_by_name(client_name, OrderType.X) or repo.find_by_name_any_type(client_name)
```

**Reading fields (attributes, NOT dict `.get()`):**
```python
customer_id = cust.customer_id
separation  = (cust.separation_customer, cust.separation_event, cust.separation_order)
code_name   = cust.code_name or 'DEFAULT'
billing_type = cust.billing_type or 'direct'
```

**Save (requires a `Customer` entity):**
```python
from src.domain.entities import Customer
repo.save(Customer(
    customer_id=str(customer_id),
    customer_name=client_name,
    order_type=OrderType.X,
    billing_type='agency',
    separation_customer=separation[0],
    separation_event=separation[1],
    separation_order=separation[2],
))
```

### 12. Never use `%-m` / `%-d` in strftime — Linux-only, crashes on Windows

**Session:** ACM parser (2026-06-11)

The `%-m` and `%-d` strftime directives are **Linux/macOS only**. On Windows they raise `ValueError`.

**Wrong:** `f"{d.strftime('%-m/%-d/%y')}"`
**Correct:** `f"{d.month}/{d.day}/{d.strftime('%y')}"`

Use `.month`, `.day`, `.year` integer attributes directly.

### 13. `billing_type` must be read from the customer DB record, never hardcoded

**Session:** ACM parser (2026-06-11)

The customer DB record stores `cust.billing_type` (`"agency"` or `"direct"`). **Never hardcode** it in a gather or automation function — the customer record is the source of truth.

**Pattern in gather:**
```python
billing_type = cust.billing_type or 'direct'   # read from DB
```

### 14. Confirm the start date when the order starts tomorrow or earlier

**Session:** Start-date sanity check (2026-06-15)

Every new parser must check the order's flight start date during `gather_*_inputs()`. If the earliest start date is **tomorrow or earlier**, prompt the user to confirm before processing continues. An order that starts today/past usually means late IO, mis-parsed date, or backfill that needs special handling.

**How to apply:**
```python
from datetime import date, timedelta

if earliest_start <= date.today() + timedelta(days=1):
    print(f"  ⚠ This order starts {earliest_start.month}/{earliest_start.day}/{earliest_start.strftime('%y')} "
          f"(today is {date.today().month}/{date.today().day}/{date.today().strftime('%y')}).")
    raw = input(f"  Confirm start date [{earliest_start.month}/{earliest_start.day}/{earliest_start.strftime('%y')}]: ").strip()
    if raw and raw.lower() not in ('y', 'yes'):
        earliest_start = _parse_user_date(raw)
```

**Critical:** The override must reach the LINE dates, not just the contract header. Keep the original parsed start, and shift any range that begins on the original earliest start to the overridden date:
```python
date_from = _parse_date(rng['start_date'])
if original_start and override_start and override_start != original_start and date_from == original_start:
    date_from = override_start
```

### 15. Agency parsers: agency ≠ customer — hardcode the agency, look up the customer, let ANAGRAF win

**Session:** Brentan Media Services parser (2026-06-15)

For an **agency parser** (one media agency placing orders for many different advertisers), the **agency** and the **customer/advertiser** are two distinct ANAGRAF records.

- **Customer / advertiser** = who the campaign is for (ANAGRAF customer ID)
- **Agency** = the buyer placing the order (hardcoded in `AGENCY_IDS` in `etere_direct_client.py`)

**The rule:** *Always query ANAGRAF for the client, and if ANAGRAF returns an agency for that client, use it.* The hardcoded agency ID is only a fallback for rare clients with no agency linked.

**How to apply:**
```python
client.create_contract_header(
    code=..., description=..., customer_id=int(customer_id),
    agency_id=AGENCY_IDS["BRENTAN"],   # fallback only
    lookup_customer_defaults=True,     # always query ANAGRAF for the client
    contract_date=..., contract_end_date=..., billing_type=billing_type, allow_rename=True,
)
```

The gather function looks up / prompts for the **customer name only**, never the agency.

### 16. The advertiser/client name may live in the FILENAME, not the workbook

**Session:** Brentan Media Services parser (2026-06-15)

Some proposal workbooks carry only the **agency** in their cells; the **advertiser** appears only in the file name, e.g. `Crossings TV CA Conservation Corps_Brentan Media_2026.xlsx`. Extract it from the filename and let the user confirm/override it in `gather_*_inputs()`:

```python
m = re.search(r'crossings\s+tv\s+(.+?)\s*_\s*brentan', Path(path).stem, re.IGNORECASE)
client = m.group(1).strip() if m else ""
# in gather: raw = input(f"  Customer / advertiser name [{client}]: ").strip(); client = raw or client
```

### 17. Stop grid parsing at the totals/"Summary" section — don't rely on a market-name skip-set

**Session:** Brentan Media Services parser (2026-06-15)

Multi-market proposal grids end with a **"Summary of investment"** block. These rows can look like data rows but are totals/added-value notes.

**Rule:** `break` out of the row loop when you hit the summary header (`cell.lower() == 'summary of investment'`). Everything below it is never airtime lines. Always verify parsed totals reconcile against the order's own summary footer before shipping.

---

## Table Headers Are Not Always Row 0 — pdfplumber Merges Section Banners Into Tables; Scan for the Header Row and Reconcile Totals

**Session:** SCWA Aug-Sept partial entry (2026-07-16)

**Rule:** `extract_tables()` can absorb a section banner ("Central Valley, CA
(KBTV 8.2, ...)") as a table's row 0, pushing the real column header to row 1.
A parser that tests only `table[0]` for its header markers silently DROPS that
table. SCWA Aug-Sept: the August table had the banner merged, September didn't
→ contract 2958 entered with only the September month (5 of 10 lines), no error.

**How to apply (any multi-table grid parser):**
1. Scan each table's rows for the header row (`"Language Block" and "Total Unit"
   in row_text`), keep `(table, header_row_index)`, and parse rows from
   `header_ri + 1`. Map columns per table, not from the first table only.
2. Reconcile `sum(spots × rate)` against the PDF's own summary total
   ("Total (Net)") and **raise** on mismatch — a dropped table must refuse to
   enter, never enter partially. (Same family as the Brentan totals lesson.)
3. Partial-entry symptom: parsed subtotal equals ONE month's subtotal and all
   line dates carry the later month (later table overwrote nothing — the earlier
   one was never seen).

---

## Never Cluster on `round(coordinate)` — Round Manufactures Phantom Gaps at .5 Boundaries

**Session:** Admerasia positional reader — Vietnamese McValue July SF (2026-07-01)

**Rule:** When grouping PDF words into rows/columns by a coordinate (`top`/`x0`),
cluster the **raw float** values, never `round()`-ed ones. Rounding to int buckets
first splits a single row whose baseline straddles a .5 boundary into two buckets
(e.g. `top=299.48→299` and `299.81→300`), inventing a phantom row. In Admerasia this
made positional return 4 rows vs vision's 3 → `AdmerasiaVisionError` row-count
mismatch → the order silently refused to enter.

**How to apply:**
1. Sort the words by raw coordinate and start a new cluster only when the gap exceeds
   a tolerance that sits **between the intra-row jitter and the inter-row pitch**.
   Measure both before picking the tolerance — don't assume. In these grids the pitch
   is only ~5-7pt (dense 12-row Chinese order), and jitter is <0.5pt, so `ROW_TOL=2.0`.
   A too-big tolerance is as bad as rounding: a first attempt at `6` merged distinct
   rows in nearly every order (dense Chinese collapsed to 1 row of 73 spots).
2. **Regression-sweep coordinate changes across ALL known-good fixtures** before
   shipping — assert the new splitter reproduces the old row counts on every prior file
   and only changes the one you meant to fix. Cheap: the positional/coordinate half
   needs no API key, so batch it over the whole fixtures folder.

## `parse_day_bits` (DirectDB) and `_select_days` (Selenium) Must Stay in Sync

**Session:** Admerasia DirectDB conversion (2026-06-08)

**Rule:** Two day-parsing implementations must stay in sync:
- `EtereClient._select_days()` — Selenium path (etere_client.py). The **original** reference.
- `parse_day_bits()` — DirectDB path (etere_direct_client.py). Must support **all the same aliases**.

**How to apply:**
1. Any time you add a day alias to `_select_days`, also add it to `_TOKEN_MAP` and `_TOKEN_TO_INDEX` in `etere_direct_client.py`.
2. When you convert a parser to DirectDB, print the `days` string each line will pass to `add_contract_line` and confirm `parse_day_bits` produces at least one `True` flag. A line where all flags are False will silently enter but never schedule.
3. The canonical full alias set is in `_select_days` — treat it as the source of truth.

**Known full single-char set:** M=Monday, T=Tuesday, W=Wednesday, R=Thursday, F=Friday, S=Saturday, U=Sunday.

### `parse_day_bits` comma branch must expand range segments, not just single tokens

**Session:** WorldLink contract 2899 — "M-F,Su" entered Sunday only (2026-06-25)

**Bug:** A mixed pattern like `"M-F,Su"` failed the whole-string range `fullmatch` (the comma breaks it), fell into the comma-list branch, split into `["M-F", "SU"]`, and ran `_TOKEN_MAP.get("M-F")` → `None`. Only `SU` survived → Sunday-only line. Block auto-load then loaded only Sunday blocks, so the M–F airtime silently never scheduled.

**Fix:** Treat each comma segment uniformly — it may itself be a range *or* a single token. `parse_day_bits` now splits on commas and runs `_apply_day_segment()` (range-aware) on each piece. Handles `"M-F,Su"`, `"M-F,Sa-Su"`, pure ranges, and pure token lists with one code path.

**Why not delegate to `day_utils.tokenize`?** Tempting (it's the richer parser the Selenium path uses), but `day_utils` is **case-sensitive** and relies on mixed case to tokenize concatenated forms (`"MTuWThF"` → `M,Tu,W,Th,F`). `parse_day_bits` uppercases its input, and uppercase `"TU"` would greedily tokenize as `T`+`U` = Tuesday+**Sunday**. The two parsers have incompatible case contracts — keep `parse_day_bits` self-contained with its uppercase `_TOKEN_MAP`.

---

## Language-Targeted Traffic Instructions Must Use Day/Time Window Filters — Never Line Description Matching

**Session:** RPM Thunder Valley (2026-06-02)

**Rule:** Any traffic instruction format that assigns spots per language (Cantonese, Mandarin, Vietnamese, etc.) must use `_CTV_LANG_WINDOWS` (or `_DAL_LANG_WINDOWS` for The Asian Channel) time-window filters. Never attempt to detect language by matching against contract line descriptions.

**Why:** Line descriptions are free-text and change. Time windows are the ground truth: a spot that airs Monday 19:00–20:00 is Cantonese because that's what Crossings TV programs in that slot.

---

## Cache-Bust EVERY Static Asset You Change, Not Just app.js — and `.hidden` Only Works Where a Rule Defines It

**Session:** Backwrite Phase 4 contact modal (2026-07-13)

**Rule:** `index.html` versions its assets with `?v=YYYYMMDD` query strings so the browser refetches after a deploy (Lee runs on the Jumpbox behind pull+restart; without the bump the browser serves stale files). It's easy to bump **only `app.js`** and forget the CSS/other links. Symptom seen live: a modal whose *box* was styled (old cached `.detail-*` rules present) but whose *new fields* were completely unstyled — because `app.css` had no `?v=` and the browser never loaded the new `.bwc-*` rules. Looked like "the CSS is broken" for two rounds; it was pure caching.

**How to apply:**
1. When you edit `app.css` (or any linked static asset), bump its `?v=` in `index.html` in the SAME change as the JS bump. Keep the version tag consistent across the assets you touched.
2. If new markup renders with browser-default styling but the surrounding chrome is fine, suspect a stale cached stylesheet before suspecting your CSS.

**Related gotcha (same session):** there is **no global `.hidden { display:none }`** in `app.css` — `.hidden` is defined per-component (`.detail-overlay.hidden`, `.detail-error.hidden`, …). A new element given `class="... hidden"` will NOT hide unless you add its own `#id.hidden { display:none }` rule. The Phase 4 form stayed visible in the error path for exactly this reason until `.bwc-form.hidden` was added.

---

## Showing/Hiding `<tr>` Elements in JavaScript Requires `display='table-row'`

**Session:** Make Goods (2026-05-29)

**Rule:** To show a `<tr>` that has `display: none` in CSS, set `element.style.display = 'table-row'` explicitly. Setting it to `''` only works if the element has no CSS rule hiding it.

```js
// Wrong — reverts to CSS display:none
row.style.display = '';

// Correct
row.style.display = 'table-row';
```

For `<div>`, use `'block'` or `'flex'`.

---

## Etere Blacklist — Complete Reference

**Sessions:** Missing Materials blacklist button (2026-05-29) + Make Goods reconciliation (2026-05-29)

### The Accounting Formula

For any contract line, at all times:

```
N_PASSAGGI  =  trafficPalinse rows  +  TSL.PassageMiss
(ordered)       (placed/aired)          (blacklisted)
```

If `trafficPalinse + TSL.PassageMiss < N_PASSAGGI`, there are **orphaned deletions** — spots that were removed from the schedule but whose blacklist count was never written.

**Source of truth:** `trafficPalinse` (not TPALINSE, not N_PASSAGGI).

### How to Blacklist a Spot (one spot per call)

```sql
-- Step 1: delete from schedule
DELETE FROM trafficPalinse WHERE id_tpalinse = %s
DELETE FROM TPALINSE        WHERE ID_TPALINSE = %s

-- Step 2a: first blacklist on this line → INSERT
INSERT INTO Traffic_ScheduleList (
    ID_ContrattiRighe, BlackList, PassageMiss,
    ID_TRAFFICPALINSE, Date, ToDate,
    Notes, Operator,
    ID_FILMATI, ID_FILMATI_TAIL, ID_FILMATI_MIDDLE,
    ID_FATTURAEMITTENTE, Split
) VALUES (%s, 1, 1, %s, %s, %s, %s, %s, -1, -1, -1, 0, 0)

-- Step 2b: subsequent blacklist on same line → INCREMENT (never skip)
UPDATE Traffic_ScheduleList
SET PassageMiss = PassageMiss + 1
WHERE ID_ContrattiRighe = %s AND BlackList > 0
```

**Critical rules:**
- `ID_TRAFFICPALINSE` = `trafficPalinse.id_trafficPalinse` of the deleted row
- `Date` / `ToDate` = **always** `CONTRATTIRIGHE.DATA_INIZIO` / `DATA_FINE` — **never leave NULL**
- INSERT on first occurrence; `PassageMiss + 1` on every subsequent spot for that line (never skip)
- If `Date`/`ToDate` are NULL, that blacklisted spot is invisible in every date-range query

### What NOT to Do

- ❌ `LIVELLO=666` alone — Etere counts 666 rows AND TSL separately → doubles blacklist count
- ❌ TSL-only (leaving LIVELLO=0) → trafficPalinse count stays high, placed + blacklisted > N_PASSAGGI
- ❌ Skipping TSL write if entry already exists → orphaned deletions
- ❌ Leaving TSL Date/ToDate NULL → spot disappears from all date range reports

---

## Every Traffic Assignment Must Populate CONTRATTIFILMATI (the "Rotate with the following assets" pool)

**Session:** Tatari/MA Woof/Pholicious fix (2026-05-28)

**Rule:** Any code that assigns traffic (via `auto-assign`, `assign`, or `assign-spots`) **must** ensure `CONTRATTIFILMATI` is populated for every assigned line. Two requirements:

1. **`MaterialAddToAssetListC`** — the Etere HTTP call that registers filmati in the contract pool. Must be called for each filmati not yet in the pool for the specific lines being assigned. Endpoint: `POST /Sales/MaterialAddToAssetListC` with `{"idFilmatiList": [fid], "idct": contract_id}`.

2. **`CONTRATTIFILMATI` rows** — one row per `(ID_CONTRATTIRIGHE, ID_FILMATI)` with `PERCROTATION = 0`. Use DELETE+INSERT (not UPDATE+INSERT-if-rowcount) so new lines still get their rows. **Do NOT calculate or set PERCROTATION** — actual rotation percentages are set separately. The pool rows just need to exist.

3. **Cleanup DELETE must EXCLUDE assigned lines.** The cleanup that removes rows for non-assigned lines MUST include `AND ID_CONTRATTIRIGHE NOT IN ({assigned_line_ids})` — otherwise it deletes the rows just inserted.

**Mandatory checklist:** Before shipping any new traffic instruction format, verify:
1. The assignment path calls `MaterialAddToAssetListC` for each filmati being registered.
2. `CONTRATTIFILMATI` rows are written for every `(ID_CONTRATTIRIGHE, ID_FILMATI)` pair on assigned lines.

---

## Direct DB Entry Must Always Pass `booking_code` Explicitly

**Session:** iGraphix + WorldLink BNS fix (2026-05-28)

**Rule:** Any direct DB automation call to `add_contract_line()` must always pass `booking_code` explicitly:
```python
booking_code=10 if is_bonus else 2
```

Never rely on `is_bonus=True` to set the booking code automatically.

---

## Bonus / Added-Value Is ALWAYS Rotation — Even When a Caller Passes `scheduling_type`

**Session:** WorldLink AATV BNS-as-Priority fix (2026-06-25)

**Rule:** A BNS (bonus) or AV line must schedule as **Rotation (PRENOTAZIONE=1)**, never Priority — unless it's position-locked (bookend/billboard/bottom). This is enforced centrally in `add_contract_line()` and **overrides any explicit `scheduling_type` the caller passes.**

**What happened:** `worldlink_automation.py` passes `scheduling_type=0` (Priority) on *every* line. `add_contract_line` used to honor an explicit `scheduling_type` verbatim (`if scheduling_type is not None: prenotazione = scheduling_type`), which **bypassed** the bonus→Rotation rule — so all 69 bonus lines across 11 contracts entered as Priority and couldn't be scheduled as intended.

**How to apply:**
1. In `add_contract_line`, the bonus/AV rule is checked **before** the caller's `scheduling_type`:
   ```python
   _is_position_locked = is_bookend or is_billboard or is_bottom
   if (is_bonus or is_added_value) and not _is_position_locked:
       prenotazione = 1            # system rule — wins over scheduling_type
   elif scheduling_type is not None:
       prenotazione = scheduling_type
   ...
   ```
2. Paid lines still honor the caller's `scheduling_type` (WorldLink paid stays Priority).
3. To repair already-entered bonus lines: `UPDATE CONTRATTIRIGHE SET PRENOTAZIONE=1 WHERE ID_BOOKINGCODE=10 AND CONTROLLACAPOFILA=0 AND CONTROLLAFINEFILA=0 AND PRENOTAZIONE<>1` (scoped to the affected contracts). capofila/finefila already 0 and priorita 500, so flipping PRENOTAZIONE alone is the complete Priority→Rotation transition.

---

## EtereDirectClient SP Calls Must Use `self._ph`, Not Hardcoded `?`

**Session:** Trade entry direct DB write (2026-05-21)

**Rule:** Any SQL string in `etere_direct_client.py` that contains `?` placeholders must be executed as `cursor.execute(sql.replace('?', self._ph), params)`. Never hardcode `?` and call `.execute(sql, params)` directly — it will break on pymssql connections (which require `%s`).

**Applies to:** Both SP calls in `etere_direct_client.py` (header + line inserts), and any future SP calls added to the file.

---

## Month-Only Orders Must Use Rotation Scheduling; Week-Column Orders Stay Default

**Session:** Universal rule (2026-05-14)

**Rule:** Scheduling type is determined by how the IO/order is structured:

- **Week columns present** (order lists spots per week) → leave scheduling type at default (Priority, type 0). Pass `spots_per_week > 0` to `add_contract_line()`.
- **Month column only** (no weekly breakdown, just a total per month) → **Rotation** (type 1). Pass `spots_per_week=0` with the full-month date range.

`EtereClient.add_contract_line()` enforces this automatically: any line where `spots_per_week == 0` AND the flight is longer than 7 days is flagged `_is_monthly = True` and Rotation is selected. **No extra flag needed from the automation.**

---

## All Parsers Must Set `rates_are_net` on Their Order Object

**Session:** Backwrite gross-up automation (2026-04-17)

**Rule:** Every parser's order dataclass must carry a `rates_are_net: bool` field.

- `False` (default) — rates in the IO are Gross; no gross-up needed
- `True` — rates in the IO are Net; backwrite will auto-gross-up by dividing by `(1 - agency_fee)`

**How to apply:** When writing a new parser, add `rates_are_net: bool = False` to the order dataclass. If the format is always net, set it `True`. If it depends on a column header, detect it: `bool(re.search(r'\bNet\b', header) and not re.search(r'\bGross\b', header))`.

---

## Master Market Is Always NYC — Never Override It in Agency Automations

**Session:** SCWA implementation (2026-03-26)

**Rule:** Master market is set ONCE by `EtereSession` before any automation runs. Agency automation files must NEVER call `etere.set_master_market()` — doing so fires a second market-selection and can set the wrong market.

**Universal defaults:**
- Master market = **NYC** for ALL orders — Crossings TV and any other agency
- Master market = **DAL** only for WorldLink / The Asian Channel

When writing a new agency automation, do NOT include a `set_master_market` call. The session handles it. The line-level `market` argument to `add_contract_line()` is separate and correct to set per line.

---

## "Sloppy" Is the User's Judgment, Not a Rule Specification — Ask What They Saw

**Session:** Fill & Finish, MMT 14:00 (2026-08-28) — Lee: "let's not assume that's what I was referring to"

**Rule:** Lee said MC "sometimes does a sloppy job" on a show; I inferred the defect
(same PI file twice in the hour) from what *I* found in the data and proposed a rule
for it. Lee's actual rule was different and narrower: the same file twice in a show is
FINE; only a :30 and :60 of one campaign in the SAME BREAK is wrong. Had I built the
inferred rule, Finish would have "fixed" behaviour he wants (scarce :30s recycling).

**How to apply:** when a user calls something wrong without naming the defect, list what
you see and ask which item they mean before proposing a rule — never promote your own
diagnosis to their intent. Related: [[fill-and-finish]] rotation rules.
