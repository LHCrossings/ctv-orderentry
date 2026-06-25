# Plan: Phase out the terminal pop-out → fully browser-driven order entry

**Goal:** Run the app on a Linux server, accessed remotely from any machine, with
the entire order-entry interaction happening **in the browser** — no OS console
window, and ultimately no in-browser "terminal" either, just forms + a progress view.

**Status:** Planning (investigation complete 2026-06-25). Implementation deferred —
"near future project." Do NOT start until the current order crunch is past.

---

## The key insight (changes the priority order)

Order processing is **always a spawned subprocess** (`main.py`), never in-process.
`POST /api/run` (`src/web/routes/orders.py:1627`) branches on platform:

- **Windows** (`orders.py:1668`): `subprocess.Popen([... main.py --pause], CREATE_NEW_CONSOLE)`
  → a **detached OS console window**. This is the pop-out. It only appears on the
  machine running the server — the remote-access problem.
- **Linux** (`orders.py:1677`): returns `{"terminal":"sse"}`. The browser then opens
  `GET /api/terminal/stream` (`orders.py:1687`), which spawns `main.py -u` with
  piped stdin/stdout and **streams stdout into a browser modal** (`app.js:215`);
  typed answers POST to `/api/terminal/{id}/input` (`orders.py:1736`) → subprocess stdin.

**=> Deploying to Linux already eliminates the literal pop-out.** The terminal becomes
an in-browser modal reachable from any client. That's clunky (you type answers to
`input()` prompts), but it *works remotely*. The real work is replacing that
interactive terminal with browser forms + non-interactive processing.

---

## What already exists to build on

1. **Structured per-order preview:** `GET /api/orders/{filename}/detail` →
   `get_order_detail()` (`src/web/parser_bridge.py:524`) returns normalized JSON:
   client, markets, flight dates, buyer, line items, rates, warnings,
   `required_fields`, `rates_are_net`. The detail modal already renders all of it
   read-only (`app.js:376`).
2. **Edit + override plumbing (underused):** the detail modal renders
   `required_fields` as `<select>`s (`app.js:405`); `setOverride()` stores into a
   `pendingOverrides` JS object; Run Queue posts `{files, overrides}`;
   `/api/run` writes `<file>.overrides.json` (`orders.py:1642`). **Today it only
   carries `market`**, and only **Charmaine** (`charmaine_automation.py:348`) and
   **Wallrich** (`wallrich_automation.py:193`) read it. All other parsers ignore it.
3. **Live customer typeahead:** `GET /api/trade/search-customer` (`orders.py:7445`)
   queries Etere `ANAGRAF` live and returns `[{id,name}]`. Reusable for an order
   customer picker. (`customers.db` management routes also exist.)
4. **Cached extraction:** AI orders cache to `<file>.ai.json` and WorldLink scanned
   orders to `<file>.wl.json`, so preview and entry already use the SAME extraction.
5. **Per-file scan cache** (`.scan_cache.json`) already makes scans instant after the
   first — list/refresh are fast.

---

## What blocks a no-terminal flow

`order_processing_service.py` has **zero** `input()` calls — all interactivity lives in
`browser_automation/*`. Two tiers:

### Tier A — gather-time prompts (easy: collectable in a form up front)
The orchestrator calls each `_INPUT_GATHERERS` (`orchestrator.py:33`) function with the
PDF path; each prompts via `input()`, then `_confirm_separation()` (`orchestrator.py:320`)
always prompts. Fields needed, by parser:

- **Common:** separation (cust,order,event), contract code, description, customer_id.
- **WORLDLINK** (`worldlink_automation.py:246`): revision-vs-new (auto-detected);
  if customer DB-miss → customer id + abbrev + separation; if revision → confirm/enter
  existing contract; if `revision_change` → first-available date. Contract code &
  description come from the parsed order (not prompted).
- **HL / HL_BDR:** contract code, description, revision + existing contract id,
  customer id (only on DB miss), gross-up % (if net), add-AV, estimate selection.
- **TCAA:** estimate selection, contract-code prefix, description (customer hardcoded 75).
- **AI_FALLBACK:** proceed-confirm, start-date confirm, advertiser, customer id
  (mandatory), billing type, save-to-DB, contract code, description.

### Tier B — mid-processing prompts (hard: depend on live Etere DB state)
These fire *during* the write, after inspecting the live DB — they can't simply be
pre-filled from a form:
- `worldlink_automation.py:727` — "Treat Line N as CHANGE?" (line already in Etere)
- `worldlink_automation.py:841` — "Apply? [y/n]" per-line gate
- `hl_automation.py:178` — "Press Enter when lines are cleared" (revision)
- `tcaa_automation.py:732` — "Continue with remaining contracts?"
- `xml_automation.py:402` — per-estimate "Continue?"
- `etere_client.py:295` — Selenium manual customer search (legacy Selenium path only;
  DirectDB doesn't hit this)

Tier B needs either (a) **pre-flight DB checks** surfaced in the form ("Line 3 exists →
treat as change?"), or (b) a **decision policy** passed up front (auto-apply,
treat-existing-as-change), or (c) a **pause→ask→resume** round-trip over SSE.

---

## Phased plan

### Phase 0 — Deploy to Linux (unblocks remote NOW, ~no code)
- Stand up the app on the Linux server; confirm the SSE in-browser terminal works
  from a remote client (it should — it's the existing Linux branch).
- Add a DB **login timeout** to `etere_direct_client.connect()` so an unreachable
  Etere SQL fails fast with a clear error instead of hanging (critical on a server).
- Outcome: remote users can run orders in-browser today, via the (clunky) terminal modal.

### Phase 1 — Make gather non-interactive (the core enabler)
- Add a tiny helper `ask(answers, key, prompt, default)`: returns `answers[key]` when
  present, else falls back to `input()` (CLI keeps working unchanged).
- Thread an `answers` dict into each `gather_*_inputs` (loaded from a richer
  `<file>.overrides.json`, superseding today's market-only sidecar). When all needed
  keys are present, **no prompt fires**.
- Refactor parser-by-parser: **WORLDLINK first**, then HL/HL_BDR, TCAA, AI_FALLBACK,
  then the long tail. Also make `_confirm_separation` honor a provided value.
- Keep the CLI path intact the whole time (fallback to `input()`).

### Phase 2 — Browser form per order
- Extend `/detail` (or a new `/api/orders/{file}/form-schema`) to return the **field
  schema** that parser needs (types, defaults, options) — generalize `required_fields`
  into a full form spec.
- Build a per-order **confirm/edit panel**: parsed preview (already have it) + the gather
  fields. Wire the customer field to the ANAGRAF typeahead (`/api/trade/search-customer`).
- On submit, write the rich `overrides.json` (or POST to a new endpoint) — the same dict
  Phase 1's `answers` consumes.

### Phase 3 — Non-interactive run + output-only progress
- New `POST /api/process` that runs the order with the supplied answers and **no input()**.
  Two viable implementations:
  - (a) subprocess `main.py --files X --answers <sidecar>` (isolation, simplest), or
  - (b) in-process background task/worker.
- Stream stdout to the browser as an **output-only progress view** (reuse the SSE
  channel minus the input box). The "terminal" becomes a log/progress panel.

### Phase 4 — Handle Tier-B live-DB decisions
- Add **pre-flight DB checks** so existing-line / revision conflicts are detected at
  form time and surfaced as toggles (e.g. "Line 3 exists → CHANGE").
- Pass per-order **decision policies** in the answers dict (auto-apply, treat-existing-as-
  change, continue-remaining). Do revision line-clearing programmatically (DirectDB
  delete) instead of the manual-Etere `hl:178` pause.
- For anything truly unknowable up front, support a **pause → SSE event → web decision →
  resume** round-trip (rare path).

### Phase 5 — Decommission the console/terminal
- Remove the Windows `CREATE_NEW_CONSOLE` branch and `--pause`.
- Retire the interactive SSE-input endpoint (keep output-only streaming).
- Linux server is the deployment target; everything is browser forms + progress view.

---

## Cross-cutting concerns
- **Don't break the CLI:** keep `input()` fallback until each web path is proven.
- **DB reachability:** add connect timeouts; show clear errors in the UI.
- **Multi-user (server):** multiple operators may run concurrently. Subprocess-per-job
  isolates state; watch Etere DB/session concurrency and per-job working dirs.
- **Auth:** a shared server likely needs login/authz (out of scope here; flag it).
- **Sidecar hygiene:** consume-once + clean up `.overrides.json` / `.answers.json`
  (Charmaine/Wallrich already unlink after read).
- **Incremental & safe:** each phase ships independently; Phase 0 delivers remote value
  immediately, Phases 1–2 can land parser-by-parser behind the existing terminal.

## Suggested first slice (when we start)
Phase 0 (deploy + DB timeout) → Phase 1 for **WORLDLINK only** + Phase 2 form for
WorldLink + Phase 3 run path → prove the end-to-end no-terminal flow on one parser,
then fan out to the rest.
