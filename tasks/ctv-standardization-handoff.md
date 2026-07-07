# ctv-orderentry → ctv-common / ctv-template Conformance — Handoff

**Written:** 2026-07-02 (Claude Code session with Lee)
**Audience:** Lee + Kurt (Kurt designed the template system)
**Purpose:** Capture everything analyzed so far so we can pick this up together
later. No changes have been made to the repo — this is analysis + a proposed
plan only. (A checkable version of the Tier 1 plan also lives in
`tasks/todo.md`.)

---

## 0. Where things stand / setup notes

- The two reference repos were cloned locally for analysis:
  - `/home/scrib/dev/ctv-common`
  - `/home/scrib/dev/ctv-template`
  - (Both are **private**; access is via Lee's **SSH key** as `LHCrossings`.
    HTTPS clone fails — no stored GitHub credential on this machine. Use
    `git@github.com:daseme/...` URLs.)
- **Nothing in `ctv-orderentry` has been modified.** We stopped at planning so
  Kurt can weigh in first.
- Versions seen at analysis time: `ctv-common` **v0.5.4**, `ctv-template` pins
  `ctv-common @ v0.4.1`.

---

## 1. What the two repos are

### `ctv-common` — a shared Flask infrastructure *library*
Small, focused modules that CTV tools import instead of re-implementing.
Installed by pinning a git tag in `pyproject.toml`
(`ctv-common @ git+ssh://git@github.com/daseme/ctv-common.git@vX.Y.Z`), then
`uv sync`. Modules:

- `config` — `.env` loading + required-var validation → frozen `Config`.
- `logging` — JSON logger with request-ID middleware.
- `healthz` — `/healthz` Flask blueprint returning `{status, tool}`.
- `auth` — **Tailscale** identity via the local Unix-socket whois API;
  `require_tailscale_auth` Flask middleware.
- `db` — raw `sqlite3` context managers (`sqlite_connection`, `transaction`,
  `read_only_connection`, `init_db`) with a fixed WAL pragma set. **SQLite only.
  No SQLAlchemy, no ORM. No SQL Server.**
- `errors`, `static`, `nav` — shared error handlers + CSS/nav assets.
- `testing` — pytest fixtures (`temp_sqlite`, `tailscale_identity`,
  `caplog_json`) registered via `pytest_plugins = ["ctv_common.testing"]`.

Versioning: 0.x with breaks-on-minor until a 1.0 graduation (criteria in the
`standardization-policy.md` referenced below). Releases are git tags;
`make release VERSION=vX.Y.Z`.

### `ctv-template` — a GitHub "Use this template" starter for **new** Flask tools
Fork it → run `/bootstrap` (a Claude Code slash command that interviews you for
tool name / description / port / version, then renames the package and fills in
placeholders). What a fresh fork gives you:

- Flask **app factory + blueprints** skeleton wired to `ctv-common`.
- Config via `.env`, JSON logging, `/healthz`, Tailscale auth on all other routes.
- **SQLite** via `schema.sql` + `make migrate` (raw sqlite3, idempotent
  `CREATE TABLE IF NOT EXISTS`).
- Toolchain: **uv**, **ruff** (lint+format), **mypy --strict** on `src/`, **pytest**.
- **pre-commit**: gitleaks + ruff + mypy.
- **CI** (`.github/workflows/ci.yml`): gitleaks job, lint job
  (`ruff check` + `ruff format --check` + `mypy src/`), test job — all via uv,
  with `webfactory/ssh-agent` + a `CTV_COMMON_DEPLOY_KEY` secret so CI can
  install `ctv-common` over git+ssh.
- **Dockerfile** with the BuildKit `--mount=type=ssh` pattern for the same.
- `.claude/` scaffolding (`CLAUDE.md`, `tasks/lessons.md`, `tasks/todo.md`,
  `documents/`, `commands/bootstrap.md`, `settings.json`, a `.bootstrapped`
  sentinel).
- **Deployment target:** systemd **user service on the "bee" server**, behind a
  reverse proxy, authenticated by **Tailscale**. Bee tool ports live in
  5100–5199.

### The "house style" these two encode
Python ≥3.11 · `src/ctv_<name>/` layout · Flask factory · SQLite +
`schema.sql`/`make migrate` · uv + ruff (E,F,W,I,B,UP, line-length 100) +
mypy strict + pytest · pre-commit(gitleaks/ruff/mypy) · git+ssh tag-pinned
shared lib · CHANGELOG + semver · Tailscale auth · bee/systemd deploy.

---

## 2. The core problem: ctv-orderentry does not fit the template's assumptions

The template is built for **small, fresh, Flask + SQLite + Tailscale** tools.
`ctv-orderentry` is a **large, mature app** that differs on the load-bearing
assumptions:

| Standard assumes | ctv-orderentry actually is |
|---|---|
| **Flask** (all of ctv-common's auth/healthz/logging/errors are Flask middleware & blueprints) | **FastAPI + uvicorn + Jinja2** — no Flask anywhere in the codebase |
| **SQLite only** (`ctv_common.db`) | Heavy **SQL Server / Etere** via `pymssql` (~19) and `pyodbc` (~23), plus SQLite (`customers.db`, ~108 `sqlite3` refs) |
| Small greenfield fork | Large: **Selenium** (~35 refs) browser automation, Anthropic, pandas, boto3, pdfplumber/pymupdf/pytesseract, dozens of agency parsers |
| mypy --strict, ruff {E,F,W,I,B,UP}, py3.11 | **pyright**, ruff {E,F,W,I} (ignores E501/E402), **py3.12** |
| `[dependency-groups] dev` | `[project.optional-dependencies] dev` |

**The headline consequence:** the marquee benefit of the standard — dropping in
shared **auth / logging / healthz** — is **Flask-specific and does not apply to a
FastAPI app.** You also can't "Use this template" here; that path is only for new
greenfield tools. And `ctv_common.db` is SQLite-only, so it can't front the
Etere SQL Server layer (the bulk of this app's DB work).

So "conforming" this project is **not** a re-fork and **not** a full match. It's
**selective, gradual adoption of the parts that fit** — which is exactly what the
standard's own "strangler rule" anticipates for existing tools.

---

## 3. Proposed tiered approach

- **Tier 1 — toolchain & repo hygiene (cheap, safe, zero runtime risk).**
  Align ruff/pre-commit/CI/Makefile/CHANGELOG/packaging conventions. Touches no
  application code. **This is the tier Lee selected to start with.**
- **Tier 2 — framework-agnostic shared code.** Adopt `ctv_common.config`,
  `ctv_common.logging`, a `/healthz` endpoint, and `ctv_common.db` *for the
  SQLite side only*. Needs a check that these work outside Flask.
- **Tier 3 — the hard mismatches.** Tailscale auth + the Flask blueprints would
  need either a FastAPI shim or a decision to migrate the web layer to Flask.
  Etere/SQL-Server and Selenium have no shared equivalent and stay bespoke.
  Parts of Tier 3 may legitimately be "won't conform, and that's correct."

---

## 4. Tier 1 plan (proposed — NOT yet executed)

All items touch only dev tooling / CI / packaging metadata / docs.

**Edits**
- `pyproject.toml`: move `[project.optional-dependencies] dev` →
  `[dependency-groups] dev`; add `"B"` and `"UP"` to ruff `select`. Keep the
  existing `ignore = ["E501","E402"]` and the `exclude` list (Old Code,
  archived scripts, browser_automation) for now. Then `uv lock`.
  - ⚠ Adding `UP` means pre-commit's `ruff --fix` would auto-modernize syntax
    (real code edits). Run `ruff check` and review first; don't let it silently
    rewrite.
- `.pre-commit-config.yaml`: add **gitleaks** hook; bump ruff pin v0.3.0→v0.6.9.
- `.github/workflows/ci.yml`: add a **gitleaks** job (with
  `permissions: pull-requests: read`, per the template's documented fork quirk);
  add `ruff format --check`. Keep `unixodbc-dev` apt step + py3.12. Optionally
  switch pip → uv to match house style (repo already has `uv.lock`).

**New files (mirroring the template)**
- `Makefile` — `bootstrap / dev / test / lint / format`, adapted to
  **uvicorn + uv** (no `migrate` target — Etere schema is not a local
  `schema.sql`).
- `CHANGELOG.md` — seed `[Unreleased]` + current `0.1.0`.
- `.github/CODEOWNERS` and `.github/pull_request_template.md`.

**Flag-only (no action)**
- `.claude/` layout differs (this repo: root `CLAUDE.md` + root `tasks/`;
  template: nested `.claude/CLAUDE.md` + `.claude/tasks/`). Both valid for
  Claude Code; relayout is cosmetic + would break the `@`-imports. Leave as-is.
- Top-level clutter (`MSBuildTemp*`, UUID dirs, `pytest-of-scrib`, `Old Code`,
  `archived scripts`). Git status is clean so they're presumably gitignored;
  verify, no cleanup unless something's tracked.

---

## 5. OPEN DECISIONS / QUESTIONS FOR KURT

These are the reasons we paused — they're architectural and belong with Kurt.

1. **Is a big FastAPI app like this even meant to conform to a Flask-centric
   standard?** Or is the standard scoped to the small bee tools, and
   ctv-orderentry is intentionally an exception that just borrows the toolchain?

2. **Type checker (the immediate blocker):** the standard mandates
   `mypy --strict`; this repo uses **pyright**. Flipping strict mypy on a
   codebase this size = a large error backlog and eventual app-code changes —
   not a hygiene tweak. Options:
   - (a) Keep pyright, document divergence, defer mypy — *Claude's recommended
     default to keep Tier 1 zero-risk*;
   - (b) add mypy **non-strict** as an extra gate now;
   - (c) commit to full `mypy --strict` as its own multi-session effort.

3. **Should `ctv-common` grow FastAPI support** (auth/healthz/logging as
   FastAPI-compatible, framework-agnostic, or dual)? That would unlock Tier 2/3
   for this app. Or is the expectation that this app's web layer eventually
   moves toward Flask?

4. **Deployment model — currently UNKNOWN.** Does ctv-orderentry run on the bee
   as a systemd user service behind Tailscale like the other tools, or elsewhere
   (e.g. a Windows box near Etere, Docker)? This determines whether Tailscale
   auth / systemd conformance is ever in scope. (Lee: "not sure.")

5. **SQL Server reality:** `ctv_common.db` is SQLite-only. Is there any appetite
   for a shared SQL-Server/Etere helper, or does that layer stay fully bespoke
   here forever? (Likely stays bespoke — flagging for confirmation.)

---

## 6. Reference pointers

- Standardization policy (referenced by both repos, NOT cloned here):
  `daseme/spotops` → `docs/superpowers/specs/standardization-policy.md`
  (also cited as `/opt/spotops/docs/superpowers/specs/standardization-policy.md`).
- Spotops parent `CLAUDE.md`: `/opt/spotops/.claude/CLAUDE.md` (workflow
  inspiration the template's CLAUDE.md points to).
- ctv-common README covers `db`, `testing`, Tailscale socket perms, releasing.
- ctv-template README covers the post-fork checklist, CI/Docker/bee SSH patterns,
  and the "why these defaults" rationale.

---

*Resume point: get Kurt's answers to §5, especially Q1 (is this app in scope at
all) and Q2 (mypy). If Tier 1 is a go, the checkable steps are in
`tasks/todo.md`.*
