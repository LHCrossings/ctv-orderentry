# Comprehensive Code Audit — 2026-07-07

**Method:** Seven parallel audit agents, one per subsystem, each checked against
`tasks/lessons.md` and `.claude/documents/data-reference.md` domain rules.
**Scope:** ~83k lines of tracked Python — `src/web` (incl. the 8,353-line
`orders.py`), `src/business_logic`/orchestration/presentation/backwrite,
`browser_automation` core clients, ~45 `*_automation.py` handlers, 53 parsers,
plus repo hygiene / config / secrets / tests.

**Severity legend:** 🔴 Critical · 🟠 High · 🟡 Medium · ⚪ Low

---

## Executive Summary

The system is fundamentally healthier than a repo this size usually is: the
2026-06 testing-sweep fixes clearly landed (booking_code, total_spots,
duration strings, contracts-list returns in the live handlers), secrets have
**never** been committed, and broadcast-calendar math in the backwrite is
provably correct. The problems cluster into five systemic themes rather than
random scatter:

1. **The separation order/event tuple convention is contradictory across the
   codebase — and `data-reference.md` contradicts itself.** Three independent
   audits (Etere clients, business logic, orders.py) converged on this. Gather
   functions build `(customer, EVENT, order)` per lessons #11; the clients,
   orchestrator prompt, and `separation_utils` consume `(customer, ORDER,
   event)`; orders.py reads/writes `INTERVALLO=order, INTERV_CONTRATTO=event`
   while one data-reference table says the opposite. Latent only because
   order/event separations are almost always 0. **Needs a one-time live
   verification against a web-entered line, then a single canonical fix across
   code + both doc tables + lessons #11.**
2. **The web app has no working authentication.** `require_export_token` in
   `auth.py` is dead code referenced by zero routes; uvicorn binds `0.0.0.0`.
   Combined with real SQL injection in four traffic-assign routes, a path
   traversal in EDI export, destructive unauthenticated S3 endpoints, and no
   CSRF — network trust is the only defense.
3. **Two known-fixed bug classes recur in corners the fixes never reached:**
   missing `LIVELLO=0` filters (4 more query sites) and the broadcast-day
   06:00→30:00 conversion rule (worldlink room, `_build_spot_filter`, and the
   entire order-entry time path in both Etere clients).
4. **Lessons.md bug patterns survive verbatim in unswept files:** multi-flight
   collapse in `ma_traffic_parser`, `round()` clustering in `tcaa_av_parser`,
   multi-contract bool returns in 4 automations the June AST sweep missed
   (their loops live in helpers, not `_process_*`), cross-month day resolution
   ignored in `lexus_parser`.
5. **The 49k-line `browser_automation/` core that writes to the production DB
   is essentially untested and excluded from ruff**, and several
   `scripts/fix_*` one-offs commit UPDATEs to the live Etere DB with no
   confirmation gate.

---

## Priority 1 — Security (web layer)

- 🔴 `src/web/auth.py:18` + `web_main.py:20` — `require_export_token` is defined but referenced by **zero** routes; every endpoint (DB queries, EDI generation, destructive S3 delete/rename `/api/assets/file`, `/api/assets/rename`) is unauthenticated while uvicorn binds `0.0.0.0`. Fix: wire the dependency into every router via `dependencies=[Depends(...)]` in app.py, or bind 127.0.0.1/Tailscale only.
- 🟠 `src/web/routes/orders.py:4137,4530-4531,4904,6646-6647` — **SQL injection**: four assign routes join raw JSON-body IDs into `IN(...)` lists via `",".join(str(x)...)` with no `int()` cast (`filmati_ids`/`line_ids` in traffic_contract_assign, traffic_auto_assign, bookend_pairs_assign, traffic_assign_spots). `apply_break_optimization:3782` shows the correct int-cast pattern — apply it to all four.
- 🟠 `src/web/routes/edi_export.py:459,491` — **Path traversal**: `csv_filename` from the JSON body joined as `INCOMING / csv_fn` unsanitized in `/edi/export/generate` and `/generate-batch` (`../../` reads arbitrary files). Fix: reject when `Path(csv_fn).name != csv_fn`.
- 🟡 `src/web/app.py:31-53` — No CSRF protection on any state-changing endpoint (S3 delete/rename via GET/DELETE query params, template save/delete, EDI generate); cross-origin pages can trigger them. Fix alongside auth: require a custom header or CSRF token.
- 🟡 `templates/edi/post_log.html:737,789`, `templates/billing/coop_invoicing.html:322,461` — Unescaped template-literal interpolation into `innerHTML` (contract_no, err.message, uploaded filename) — XSS via crafted filenames/PDF content. Use the `escHtml` helper assets.html already has.
- ⚪ `templates/assets.html:317,538` — `escAttr` escapes only `\` and `'`; a key containing `&quot;` breaks out of the `onclick` attribute. HTML-escape `&`/`"` too, or use addEventListener + dataset.
- ⚪ `src/web/routes/reports.py:44,59`, `airchecks.py:71`, `etere_report_fetcher.py:59` — SQL built via `% contract_id` interpolation; safe only because of int coercion today. Parameterize.
- ⚪ `docker-compose.yml` — publishes port 4000 on 0.0.0.0 by default (mitigation only in comments). `Dockerfile` — runs as root while bind-mounting the K drive and holding prod DB creds. Add `USER app` and bind 127.0.0.1 + Tailscale.
- ⚪ `.github/workflows/deploy.yml` — `appleboy/ssh-action` and `tailscale/github-action` pinned by tag not commit SHA in a workflow holding SSH + Tailscale secrets.

---

## Priority 2 — Separation order/event convention conflict (cross-cutting)

Independently found by three agents; **the reference doc itself contains two
mutually contradictory INTERVALLO/INTERV_CONTRATTO tables.**

- 🟠 `browser_automation/etere_direct_client.py:852-854` — tuple slot labeled "order" is passed to `@intevent` and "event" to `@intsrighe`, contradicting the DLL-confirmed mapping (intervalloRigheContratto=order, intervalloEvento=event).
- 🟠 `src/orchestration/orchestrator.py:332` + `src/presentation/cli/input_collectors.py:399` — gathers build `(customer, EVENT, order)` per lessons #11, but `_confirm_separation` prints `Customer/Order/Event` in slots 0/1/2 and `_get_customer_separation` SELECTs `(customer, ORDER, event)`. A user override like "15,5,0" writes order/event into swapped slots.
- 🟠 `ai_fallback_automation.py:165`, `etere_client.py:1192`, `etere_direct_client.py:849-854`, `separation_utils.py` — producers and consumers disagree on slot meaning.
- 🟡 `src/web/routes/orders.py:851-857,8281-8283` — separation read/write uses `INTERVALLO=order, INTERV_CONTRATTO=event`, contradicting the source-confirmed data-reference table (internally consistent, but one convention is wrong).

**Fix path:** verify once against a live web-entered line with distinct
customer/order/event values → pick the canonical ordering → fix
etere_direct_client, orchestrator prompt, input_collectors SELECT, orders.py,
lessons #11, and BOTH data-reference.md tables in one commit. Add a named
tuple/dataclass so slot meaning is explicit at every producer.

---

## Priority 3 — Recurring known bug classes

### Missing `LIVELLO=0` filters (deleted spots pollute results)
- 🟠 `src/web/etere_report_fetcher.py:45-59` — `_enrich_bookingcode` matches CSV↔DB **positionally**; one soft-deleted (666) spot shifts every subsequent spot code onto the wrong airing in the Run Sheet.
- 🟠 `src/web/routes/orders.py:272,280,1104` — `_mc_fill_program_spots` / fill_log_times: deleted rows match TITLE and shift actual-air-times written into official traffic-log Excel files.
- 🟡 `src/web/routes/reports.py:48-58` — placement-by-week counts inflated by deleted spots (sibling as-run query at :407 filters correctly).
- 🟡 `src/web/routes/reports.py:299-305` — as-run spot search includes deleted airings.
- 🟡 `src/web/routes/airchecks.py:54-65` — a soft-deleted future spot can be scheduled for aircheck capture.

### Broadcast-day 06:00→30:00 conversions still missing in places
- 🟠 `src/web/routes/orders.py:1175-1178` — worldlink_room `_to_frames` uses FPS=30 (not 29.97) and skips the <6h→+24h shift — and WorldLink/DAL is exactly the post-midnight market; 00:00–05:59 filters silently return zero rows.
- 🟠 `etere_client.py:1491-1506` + `etere_direct_client.py:1126-1127` — overnight ranges ending at 6am ("11p-6a") parse to end < start → inverted window; `_assign_blocks` matches zero blocks **silently**. No hour<6→+24h handling exists anywhere in the order-entry path.
- 🟡 `src/web/routes/orders.py:319-324` — `_build_spot_filter` never applies the hi<=lo→+24h window rule → 23:00→06:00 windows silently match nothing.
- ⚪ `src/web/routes/orders.py:733-745` — `_frames_to_ampm` renders post-midnight ORA (24–29h) as garbage like "15:00p" in the separation UI.
- ⚪ `orders.py:6873` etc. — duration display divides by 30 instead of 29.97; market-id map redefined 8×. Consolidate module-level `_MARKET_CODES`/`_FPS`.

### Multi-contract automations still returning bool (June sweep missed these — loops live in helpers, not `_process_*`)
- 🟠 `mediasol_automation.py:80-180` — one contract per estimate, returns bool; handler reports "0 contract(s)" on success.
- 🟠 `tcaa_automation.py:266,535` + `xml_automation.py:347-408` — per-estimate loop returns `success_count == len(estimates)`; partial success reports as total failure despite contracts in the DB.
- 🟠 `saccountyvoters_automation.py:342` — one contract per phase, returns bare bool.
- Fix all: return `list[str]` of created codes (truthiness-preserving), handler builds Contract objects.

### Lesson patterns recurring in unswept parsers
- 🟠 `parsers/ma_traffic_parser.py:130-150` — multi-flight collapse: only the "primary" segment's ISCIs kept, dates flattened to min/max — verbatim the HL bug class. Put dates on MASpot, one assignment group per segment.
- 🟠 `parsers/tcaa_av_parser.py:248` — `round(top/2)*2` row bucketing; boundary-straddling row splits, then gets **silently dropped** by the column-count guard. Cluster raw floats like admerasia_positional.
- 🟠 `parsers/lexus_parser.py:634-680` — cross-month range cells always resolve day numbers to the start month (no "day < range start_day → end month" rule); unmatched columns silently get `date.today()`.
- 🟡 `parsers/dart_parser.py:91-111` — `_to_24h` maps 12a/midnight to 06:00 instead of 23:59 → inverted end window.
- 🟡 `parsers/rpm_traffic_parser.py:24-31` — RPM spots carry no dates (the known-pending RPM multi-flight collapse); `today.replace(month,day)` can raise on day-out-of-range.
- ⚪ `parsers/intertrend_parser.py:274,281`, `admerasia_parser.py:1113` — more `round()`-keyed dedup (lower risk, same class).
- ⚪ `parsers/directdonor_traffic_parser.py:14`, `imd_traffic_parser.py:14` — spot dataclasses carry no dates; fine while single-flight, same collapse risk if a multi-flight file arrives.

---

## Priority 4 — Silent-failure paths (the repo's documented worst failure mode)

- 🟠 `src/web/routes/orders.py:317-326` — `_build_spot_filter` swallows time-parse errors with `except: pass`, dropping the time-window clause; `traffic_contract_clear` (:4379) then mass-UPDATEs (wipes COD_PROGRA/ID_FILMATI) across the **whole contract** instead of the intended window.
- 🟠 `parsers/admerasia_traffic_parser.py:17-18`, `parsers/hl_bdr_parser.py:442-443` — `except Exception: return []` swallows every parse/import error, returning empty with no signal.
- 🟡 `etere_direct_client.py:366-398,447-454` — unrecognized day pattern prints a warning then inserts the line with all seven day flags False (enters but never schedules); alias set has drifted from day_utils (`Tues`, `Thurs`, `We`, `Fr`, full names missing). Should abort when no day bit set.
- 🟡 `etere_direct_client.py:1036-1041` — new line ID fetched via `SELECT MAX(ID_CONTRATTIRIGHE)` instead of the SP's `@id` OUTPUT; concurrent/failed insert silently targets the wrong line for SECEVENTTYPE + block assignment.
- 🟡 `src/business_logic/services/daily_programming_run.py:127-139` — `Traffic_InsertEvent` EXEC built by raw f-string; `fetchone()[0]` raises opaque TypeError when placement silently failed. Parameterize + explicit error.
- 🟡 `src/web/routes/orders.py:4317-4335,6808-6827` — duplicated aircheck lookup wrapped in `except: pass` returns needs_airchecks=False on any failure.
- 🟡 `edi_export.py:246,321-323` — corrupt template JSON silently dropped; PDF parse errors yield EDI files with blank advertiser/product.
- ⚪ Widespread `except Exception: pass` with plausible-but-unlogged fallbacks: `pdf_order_detector.py:144,263,345`; automations (ai_fallback:109,302, bvk:138, hl_bdr:57, sagent ×3, hyphen:164, igraphix:366, intertrend:298, prosio:302, eqc:335, impact:406, dart:124, lrccd:295); parsers (polaris:202,358 — budget silently stays 0, misfit:562, lrccd:80, admerasia:1338, hl_bdr:303). Add one-line warnings.
- ⚪ `etere_session.py:120-124,182-183` — login marks `is_logged_in=True` without verifying; unknown market silently falls back to NYC. `ai_fallback_automation.py:164` — separation 30 silently rewritten to 25.
- ⚪ `src/orchestration/orchestrator.py:272-276` — ImportError during gather appends the order inputless; every direct-DB handler then fails with "inputs not collected." Skip with a clear error instead.

---

## Priority 5 — Correctness & data-integrity (Etere writes)

- 🟠 `src/web/routes/orders.py:1232-1261` — worldlink_room_blacklist writes `TSL.ID_TRAFFICPALINSE` = the request's **TPALINSE** id, violating the documented blacklist mechanism (must be trafficPalinse.id_trafficPalinse). The correct pattern exists at :2160-2204.
- 🟠 `etere_client.py:1522-1539` — `check_sunday_6_7a_rule` only strips Sunday for exactly "M-Su"/"Sa-Su"; "F-Su", "Su", "W,Su" pass through unchanged (line still airs Sun 6–7a), and only 3 exact time strings match. Rebuild via day_utils.tokenize + parse_time_range normalization.
- 🟡 `etere_direct_client.py:1115-1135` — `_assign_blocks` omits two DLL-confirmed loadBlock filters: block-type (`oiSpot`) and customer price-list — non-ad-eligible or wrong-pricelist blocks can be attached.
- 🟡 `etere_direct_client.py:467-479,767,1032` — default autocommit commits header and each line separately; mid-contract failure leaves a committed partial contract, no rollback (only ai_fallback opts out).
- 🟡 `etere_direct_client.py:979` — `@percCommission1` hardcoded 0 while data-reference param 10 carries the header agency % — potential net/commission drift vs web-entered lines.
- 🟡 Selenium/direct path drift: direct forces Rotation for windows >120min (absent in Selenium); Selenium clamps monthly spots_per_week→0 (absent in direct); BNS/AV-vs-position-lock precedence differs (`etere_direct_client.py:873-877` vs `etere_client.py:1022-1105`). Billboard auto-detection exists **only** in Selenium and uses exact string matching (`etere_client.py:909-916`). Extract one shared scheduling-type resolver + billboard detector.
- 🟡 `src/web/routes/orders.py:4240-4313,6740-6806` — TPALINSE/CONTRATTIFILMATI sync runs only after all Etere HTTP rotation calls; mid-loop failure leaves Etere-side rotation applied with no local sync and no compensation.
- 🟡 `dart_automation.py:81-97`, `timeadvertising_automation.py:342`, `wallrich_automation.py:125` — Sunday 6–7am rule never applied (34 other automations do).
- ⚪ `src/web/routes/orders.py:8321-8323` — apply_make_good decrements N_PASSAGGI by user-supplied count with no floor guard (can go negative, breaking blacklist accounting).
- ⚪ `spot_relocator.py:44-54,296-305` — hardcoded `%s` placeholders + `LIKE 'WL%%'` (pymssql-only), violating the `self._ph` rule.
- ⚪ `etere_direct_client.py:830` — bare `from etere_client import EtereClient` (vs package-qualified at :1227): dual module identity + makes the direct path hard-depend on selenium at line-insert time.
- ⚪ `src/web/routes/orders.py:2901` — `strftime("%-H:%M:%S")` — the only `%-` in the repo; crashes on Windows.

---

## Priority 6 — Week consolidation & rates drift (parsers/backwrite)

- 🟡 `parsers/wallrich_parser.py:345-400` — no >7-day-gap split (hiatus weeks merge) and single flight_year breaks Dec→Jan flights. `parsers/sagent_parser.py:646-652` — same missing gap check. `parsers/charmaine_parser.py:233+` — same year-rollover issue.
- 🟡 `parsers/tcaa_parser.py:435-490`, `parsers/hl_parser.py:540-605`, `misfit_parser.py:575-625` — three copy-pasted ad-hoc consolidation variants instead of `EtereClient.consolidate_weeks_from_flight`.
- 🟡 ~25 parsers lack `rates_are_net` on their order dataclass (charmaine, sagent, impact, misfit, daviselen, worldlink, igraphix, rpm, dart, timeadvertising, scwa, prosio, polaris, sierra, opad, imprenta, galeforce, hyphen, tcaa, tcaa_av, lexus, fightthebite, saccountyvoters, xml estimate, …); only 13 have it. Backwrite gross-up silently skips these.
- 🟡 `src/backwrite/transformer.py:893-905` — monthly-summary money cells written as `round(x,2)` literals (cents drift vs grand total), violating the no-rounding lesson; SC-lines section (:716-722) does it right with `=SUM()`.
- ⚪ `src/backwrite/worldlink_transformer.py:236-247,453-543` — 7-day stepping from mid-week flight start mis-attributes straddling weeks to the earlier month; double-rounded literals; 15%/10% broker split hardcoded as literal instead of formula.

---

## Priority 7 — Process hygiene in automations

- 🟡 `admerasia_automation.py:~128`, `daviselen_automation.py:~99`, `rpm_automation.py:~143` — inline separation prompts + `inputs['separation']` → orchestrator double-prompt (lesson #10).
- 🟡 27 automations lack the start-date-tomorrow confirmation (lesson #14) — including recent ones (admerasia, tcaa, tcaa_av, hl, hl_bdr, charmaine, dart, impact, rpm, mediasol). Only imprenta, rwny, lrccd, scwa, tt, worldlink, ai_fallback, sierra, lexus, eqc have it.
- 🟡 `timeadvertising_automation.py:156,166`, `threeolives_automation.py:321`, `sagent_automation.py:268,283` — banned two-step "Use default? (y/n)" prompt pattern (lesson #9).
- ⚪ 8 automations return `f":{seconds}"` duration strings (rwny:68, intertrend:73, sierra:62, scwa:78, saccountyvoters:339, tcaa_av:102, tcaa:287, prosio:210) — non-crashing only because the client special-cases the leading colon; change to `str(seconds)`.
- ⚪ Inconsistent `set_master_market()` on EtereDirectClient (20 call it, 17 don't) — benign today (attribute defaults "NYC") but a future DAL parser copied from a non-caller silently defaults to NYC. Standardize.
- ⚪ `src/presentation/cli/input_collectors.py:397-421` — `_get_customer_separation`/`_get_customer_abbreviation` bypass CustomerRepository with raw sqlite3 + hand-rolled fuzzy matching, `except: return None`.
- ⚪ `src/business_logic/services/order_processing_service.py:902,1061` — bare automation imports relying on sys.path hack; siblings use `browser_automation.` prefix. Normalize.

---

## Priority 8 — Dead code & repo hygiene

- 🟡 `order_processing_service.py:1031,1115,1190,653` — four dead `_run_*_with_driver` Selenium fallbacks still carry the banned `success=True, contracts=[]` pattern (uncalled since direct-DB migration). Delete.
- 🟡 `worldlink_automation.py:962-1060` — dead legacy Selenium `_add_crossings_lines`/`_add_asian_lines` (no booking_code) with zero callers. Delete (matches the Selenium-cleanup memory).
- 🟡 `src/web/routes/orders.py:1867-2030` — diagnose_missing debug endpoint: `?` placeholders that always fail under pymssql, hardcoded server name, leaks login/schema info. Delete or gate.
- ⚪ `src/web/routes/edi.py:319-328,338-341` — uncalled `_fetch_report_sync`; `_fetch_all_reports_sync` computes the wrong repo root (works only by accident). `etere_report_fetcher.py:136-216` — `fetch_media_library` has no callers (opens an Etere license seat if ever invoked).
- ⚪ `archived scripts/` — 21 tracked dead files (while "Old Code/" is correctly ignored); `git rm` or move under the ignored dir.
- ⚪ `.gitignore` gaps — MSBuildTemp* dirs, ~14 UUID session dirs, errors/, processed/, orders/, outgoing/ are untracked but unignored (git-status noise, `git add -A` risk); one file in `outgoing/` is tracked, likely by accident.
- ⚪ `run_all_tests.py` duplicates pytest with a hand-maintained phase list; main.py/web_main.py use sys.path.insert instead of packaging.
- ⚪ `orders.py` structure — ~80 handlers + four near-duplicate assign→sync→pool→aircheck pipelines (:4109, :4511, :4827, :6637) + an ~800-line per-format `_run` inside one `build_router`. This is where the recurring bug classes hide; extract shared helpers so fixes land once.

---

## Priority 9 — Testing, CI, and operational safety

- 🟠 `scripts/fix_2611_times.py`, `fix_separation_2611.py`, `fix_separation_6am_9am.py`, `update_separation_contract.py`, `setup_show_profiles_table.py` — UPDATE/INSERT against the **production** Etere DB with zero confirmation prompt and hardcoded contract IDs; an accidental re-run mutates live traffic data. Add the preview-then-confirm gate (pattern exists in fix_aspect_duration.py).
- 🟠 `browser_automation/` (49k of 83k LOC) — almost no test coverage (3 of 13 unit-test files touch it; 53 parsers essentially untested) AND excluded from ruff, so CI lints/tests only the src/ shell. Remove the ruff exclude; add parser regression tests from existing fixture PDFs.
- 🟡 `.github/workflows/ci.yml` — CI installs floating `>=` versions via pip while production uses `uv sync --frozen`; CI tests a different dependency set than what ships.
- 🟡 `scripts/test_lexus_direct.py` — creates and COMMITS a real contract in production with no prompt. Default to rollback + `--commit` flag.
- ⚪ `pyproject.toml` — all 18 runtime deps are unbounded `>=` floors (safe only where the lock is used).
- ⚪ `etere_direct_client.py:64,67` etc. — Tailscale IPs/hostnames hardcoded as defaults (no passwords; acceptable unless the repo could go public).

---

## What's Clean (verified positives)

- **Secrets:** credentials.env / .env never tracked, never in git history, triple-protected (gitignore + dockerignore + runtime mount). No hardcoded passwords or API keys anywhere in tracked code.
- **June 2026 sweep held:** all 40+ live `_process_*` handlers populate contracts from gathered codes; `_enrich_results` centralizes etere_id resolution; every live `add_contract_line` passes `booking_code` and `total_spots` explicitly.
- **Direct-DB client:** fully parameterized (no injection; `self._ph` followed everywhere except spot_relocator); bonus/AV→Rotation override, forced priorities 3/997, comma+range day parsing, minutes→frames separation all correct.
- **Backwrite `compute_broadcast_month`** provably correct incl. Dec→Jan rollover; detection ordering `_is_bdr` before `_is_hl_partners` matches the lesson; no `%-m`/`%-d` outside one orders.py line; no bare `except:` in src/.
- **CI exists and runs** (ruff + 280 tests collecting cleanly); deploy workflow has an actor-allowlist tripwire; airchecks timezone math handles >24h ORA correctly.

---

## Suggested Remediation Order

1. **Same-day:** int-cast the four injectable IN-lists in orders.py; sanitize `csv_filename` in edi_export; wire auth (or bind 127.0.0.1/Tailscale); add confirmation gates to the `scripts/fix_*` prod-DB scripts.
2. **This week:** resolve the separation order/event convention (live-verify once, fix code + both doc tables + lesson #11 in one commit); add `LIVELLO=0` to the five bare TPALINSE query sites; route worldlink_room and `_build_spot_filter` through `_bcast_time_to_frames`/`_window`; fix the worldlink blacklist TSL reference id.
3. **Next:** fix the four bool-returning multi-contract automations; fix ma_traffic/tcaa_av/lexus/dart parser bugs; make `_assign_blocks` and day-parse failures loud; rebuild `check_sunday_6_7a_rule`.
4. **Structural (ongoing):** extract the shared scheduling-type resolver for both Etere clients; extract the 4× duplicated assign/sync pipeline in orders.py; converge week consolidation on the EtereClient helpers; add `rates_are_net` to the ~25 missing dataclasses; delete dead Selenium code; un-exclude browser_automation from ruff and start fixture-based parser regression tests.
