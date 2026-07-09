# EDI Billing Redesign — progress tracker

Spec: `tasks/edi-billing-redesign.md` (approved by Lee 2026-07-09; open
questions resolved — broadcast month, flag-don't-reject, drill-down diff,
deliberate fetch button).

- [x] Phase 0 — golden tests + safety fixes (2026-07-09; details in spec)
- [ ] Phase 1 — consolidate into `src/business_logic/services/edi_billing.py`
- [ ] Phase 2 — customer-ID template matcher + backfill script (run with Lee)
- [ ] Phase 3 — unified `/edi/billing` page
- [ ] Phase 4 — cutover, then retire `/edi/post-log` + `/edi/export` after one
      real billing cycle

---

# Tier 1 Conformance to ctv-common / ctv-template standard

**Goal:** Align `ctv-orderentry`'s toolchain & repo hygiene with the CTV house
style (`daseme/ctv-common`, `daseme/ctv-template`). **Zero runtime risk** — no
changes to application code, only dev tooling / CI / packaging metadata / docs.

**Out of scope (deferred):** adopting `ctv-common` as a dependency (Tier 2),
Flask migration / Tailscale auth (Tier 3). Deployment model (bee/systemd/Tailscale)
is UNKNOWN — flagged for the team, not acted on here.

---

## Context: why this is only a partial match
The standard targets small **Flask + SQLite + Tailscale** tools. This app is
**FastAPI + SQL Server (Etere) + SQLite + Selenium**. So the shared *library*
(`ctv-common`, all Flask-specific) does not drop in. Tier 1 conforms the parts
that are framework-agnostic: toolchain, CI, packaging conventions, repo hygiene.

---

## Plan (checkable)

### A. Packaging & lint config (`pyproject.toml`)
- [ ] Migrate `[project.optional-dependencies] dev` → `[dependency-groups] dev`
      (house style; uv-native). Refresh lock: `uv lock`.
- [ ] ruff lint: add `"B"` and `"UP"` to `select` (standard set is E,F,W,I,B,UP).
      **Judgment call:** keep `ignore = ["E501","E402"]` for now (removing them
      is a separate cleanup); keep the `exclude` list (Old Code, archived
      scripts, browser_automation) — legacy dirs the standard doesn't have.
- [ ] Run `uv run ruff check` and review new B/UP findings BEFORE letting
      pre-commit `--fix` auto-rewrite anything (UP fixes modernize syntax = code
      changes; want them reviewed, not silent).

### B. Type checker — DECISION NEEDED
Standard = `mypy --strict` on `src/`. Current = pyright. Flipping strict mypy on
a large existing FastAPI codebase produces a large error backlog — NOT a quick
hygiene item. Recommended default: **keep pyright, document the divergence**, and
track "adopt mypy" as its own future effort. (Alt: add non-strict mypy as an
extra gate.) *Do not enable strict mypy in this pass.*

### C. pre-commit (`.pre-commit-config.yaml`)
- [ ] Add the **gitleaks** hook (secret scanning) — matches standard, pure win.
- [ ] Bump `ruff-pre-commit` rev v0.3.0 → v0.6.9 (match standard).
- [ ] (mypy hook only if B decides to adopt mypy.)

### D. CI (`.github/workflows/ci.yml`)
- [ ] Add a **gitleaks** job (with `permissions: pull-requests: read`, per the
      standard's documented fork quirk).
- [ ] Add `ruff format --check` as a gate (standard requires format-clean).
- [ ] Keep the `unixodbc-dev` apt step (needed for pyodbc) and py3.12.
- [ ] Optionally switch pip → uv (`setup-uv` + `uv sync --group dev`) to match
      house style, since the repo already ships `uv.lock`.

### E. New standard files
- [ ] `Makefile` with `bootstrap / dev / test / lint / format` targets adapted
      to this project (uvicorn dev server, `uv sync`). No `migrate` target
      (SQL Server schema is managed in Etere, not `schema.sql`).
- [ ] `CHANGELOG.md` seeded with `[Unreleased]` + the current `0.1.0`.
- [ ] `.github/CODEOWNERS` (placeholder, mirroring the template).
- [ ] `.github/pull_request_template.md` (mirroring the template).

### F. Flag-only (no action this pass)
- [ ] `.claude/` layout differs (root `CLAUDE.md` + root `tasks/` vs template's
      nested `.claude/CLAUDE.md` + `.claude/tasks/`). Both valid for Claude Code;
      relayout is cosmetic + churny + would break the `@`-imports. **Leave as-is,
      note divergence.**
- [ ] Repo hygiene: top level has `MSBuildTemp*`, UUID dirs, `pytest-of-scrib`,
      `__pycache__`, `Old Code`, `archived scripts`. Confirm all are gitignored
      (git status is clean, so likely yes) — no cleanup unless something's tracked.
- [ ] Deployment model unknown — record as an open question for the team.

---

## Review
_(to be filled in after implementation)_

---
---

# (archived) TCAA (Toyota) traffic auto-assign

## Context
- New client: TCAA "CABLE Traffic Instructions" PDF (Toyota Dealer's Assoc).
- 2 station pages (Asian American TV Corp + Crossings TV) — identical ISCIs; dedupe by ISCI.
- 3 creatives, rotation % (30/40/30), all :30, "RUN IN ALL PROGRAMMING" (no language targeting).
- **Estimate mismatch:** traffic sheet says `TCAA-9179`; Etere contract is `TCAA Toyota 9712`.
  So NOT an auto-parse (can't find contract by estimate). Instead: **select contract, then drop
  PDF to auto-assign** — operator confirms the contract maps to their estimate.
- Assign rotation to **all :30 lines** (matches "RUN IN ALL PROGRAMMING").

## Plan
- [ ] Parser `browser_automation/parsers/tcaa_traffic_parser.py` (modeled on daviselen):
      estimate (display), product, campaign dates → SQL, spots [isci,title,pct,dur], dedupe by ISCI.
- [ ] Endpoint `POST /api/traffic/tcaa/parse-io?contract_id=` — gate on "TCAA"; resolve filmati;
      load contract's duration-matched lines w/ spot counts; return spots + lines + dates.
- [ ] Frontend: add "TCAA (Toyota)" to Auto-Assign dropdown; drop-zone row + grid;
      parseTcaaIo → renderTcaaGrid → applyTcaaTraffic (buildRotationList → existing /auto-assign).
- [ ] Reset TCAA UI in selectContract(); wire drop-zone dragover/drop.

## Verify
- [ ] Parse the sample PDF → 3 unique spots, all found in FILMATI, dates 2026-07-06..26.
- [ ] Against contract 2478: 11 :30 lines resolved; rotation list built 30/40/30.

---

# EDI Billing Redesign — PDF drop → validation → upload-ready ZIP

**Full spec: `tasks/edi-billing-redesign.md`** (read it first — written for a
fresh session; includes current-state map, the customer-ID template-matching
design, TVB EDI validation rules, and phase details).

- [ ] Phase 0: golden `.txt` fixtures + byte-identical test; fix path traversal
      in `/edi/export/generate(-batch)`; log the silent except-pass swallows
- [ ] Phase 1: consolidate duplicated PDF/CSV/EDI logic from `edi.py` +
      `edi_export.py` into `src/business_logic/services/edi_billing.py`
- [ ] Phase 2: template matching by `CONTRATTITESTATA.COMMITTENTE` customer ID
      (+ market tie-break); backfill script w/ confirm; wire into existing scan
- [ ] Phase 3: unified `/edi/billing` page — PDF drop → intake → [Fetch post
      logs] (single Etere session) → validation screen (reconcile badges,
      spec-driven field validation, template override, diff modal) → export ZIP
- [ ] Phase 4: cutover nav; retire `/edi/post-log` + `/edi/export` after one
      clean billing cycle
- [ ] Verify: goldens byte-identical; real month w/ Lee — McD→McD, Toyota→Toyota;
      no-template contract flagged not guessed; mid-batch fetch failure logs out
      the Etere seat and is retryable
