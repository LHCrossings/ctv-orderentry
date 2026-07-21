# Plan — Broadcast Health (Stirlitz alarm integration)

Status: DRAFT for review. No app code until approved.
Source API: see `.claude/documents/stirlitz-multiviewer-api.md`.

## Goals (three deliverables)
1. **Global on-air indicator in the header of EVERY Control Room page** — green ("all
   networks on air") normally; turns **red on any page** when a station has a problem;
   optional **live popup** to grab attention. (Modeled on the Airchecks Datamover dot.)
2. **Broadcast Health dashboard card** — a page showing every station's live status
   (video freeze / no-data / audio) with how long each has been in that state.
3. **Inline IP Multiviewer card** — embed the live video wall in Control Room instead of
   redirecting to the external site.

## Data source (recap)
- `GET http://34.208.18.64/alarmsState/monitor?accessKey=<MONITOR_KEY>` — key-only auth,
  no login. Returns `{ currentDate, alarmLines: { "<stationId>.N.<type>": {title, alarm,
  alarmSince, stationId, stationName} } }`.
- `alarm=="0"` → OK; any non-"0"/non-empty → **ACTIVE**.
- 4 conditions/station: `Video freeze alarm`, `Video freeze alarm - no data`,
  `Audio track level below threshold`, `Audio level - no data` (~44 lines, all markets).
- Key lives in `credentials.env` as `STIRLITZ_MONITOR_KEY` — **never** in the repo or
  client JS.

### Alarm filter (DECIDED with Lee 2026-07-21)
Lee only cares about: **video off, video black/blue, and audio off.** Low audio level is
explicitly NOT wanted (their old monitoring made audio-level alerts annoying). So it's
**binary green/red — no amber tier.**

Rule-based include (title-matching, so new video alarm types are auto-caught):
- **RED** if any active alarm's title contains `"Video"` (freeze / no-data / black / blue),
  **or** title == `"Audio level - no data"` (audio off).
- **IGNORE** entirely: `"Audio track level below threshold"` (low level).
- **GREEN** when no RED condition is active.

⚠ **Black/blue verify item:** the live feed currently exposes only 4 titles (Video freeze,
Video freeze - no data, Audio track level below threshold, Audio level - no data) — no
explicit "black/blue" title. Confirm on the Stirlitz box whether a black/blue screen raises
its own alarm or is reported as "Video freeze." The `title contains "Video"` rule catches
whatever it emits either way.

## Architecture decisions
- **Server-side proxy + cache (NOT browser-direct).** A backend endpoint polls Stirlitz
  and caches; all pages read the cache. Reasons: (a) keeps the access key server-side
  (browser-direct would leak it); (b) avoids mixed-content if Control Room is HTTPS
  (Stirlitz is HTTP-only); (c) one device poll serves all users. Follows the Airchecks
  `urllib` + `asyncio.to_thread` + try/except→`unreachable` pattern.
- **Global injection via response middleware (recommended).** Add a FastAPI middleware in
  `app.py` that appends a single `<script src="/static/js/broadcast-health.js?v=…">` before
  `</body>` on `text/html` responses. One change → covers all 58 pages **and every future
  page**, with no base template and no 58-file edit. Fallback option: add the one script
  line to each template header (mechanical, but 58 edits and easy to forget on new pages).
- **Popup fan-out via polling, not push (v1).** Since every page will run the shared
  poller, every active user sees a red transition within one interval — no server push
  needed. A true instant broadcast (shared SSE endpoint + subscriber registry, or
  WebSockets) is a **future** upgrade, noted below.
- **Poll cadence (DECIDED):** client polls `/api/broadcast-health/status` every **10s**;
  server caches the Stirlitz result ~**5s**. → device is hit ≤ once/5s **total** regardless
  of user count (server-side cache), client↔server is tiny JSON. Outage surfaces in ≤~15s.
  Not a traffic concern. (SSE push is the future path to 0-latency / no polling.)
- **Control Room is served over HTTP (confirmed)** → the inline-multiviewer iframe of the
  HTTP Stirlitz box has **no mixed-content problem**.

## Deliverable 1 — Global header indicator  ⭐ (the priority)
New shared file `src/web/static/js/broadcast-health.js` (self-contained, no deps):
- On load, injects a small indicator into `.header-inner` (present on all pages; fixed
  top-right fallback if absent): a dot + label, e.g. `● All networks on air`.
- Polls `GET /api/broadcast-health/status` every **10s** (`fetch` + `setInterval`, matching
  `checkTraffic`/`checkAirchecks`). Colors via `var(--nord14)` green / `--nord11` red (from
  `_variables.css`). Binary green/red (no amber, per Lee).
- States: green = all clear; red = off-air (label lists affected stations, e.g.
  `⚠ CVC KBTV 8.2 off air`). `unreachable` from the device → grey/"health unknown" (don't
  cry wolf on a device blip).
- **Corner toast on transition to red** (DECIDED — unobtrusive corner toast, not a modal):
  show it only when a station newly crosses OK→off-air. Dedupe with `sessionStorage` (set of
  off-air stationIds already shown this session) so it alerts once per newly-affected
  station, not every 10s. Clicking the toast deep-links to the Broadcast Health dashboard.
- Clicking the header indicator also opens the dashboard.

CSS: add a `.bh-indicator` / `.bh-dot` / `.bh-toast` block. Options: put it in
`portal.css` (linked by ~all pages) so it's globally available without per-page CSS edits.

## Deliverable 2 — Broadcast Health dashboard
- Route `GET /broadcast-health` → new template `broadcast_health.html` (standard header +
  home button; Nord).
- Reads the same `/api/broadcast-health/status` (richer payload): per-station tiles grouped
  by network/market (derived from `stationName`), each showing the 4 conditions with
  green/amber/red and `alarmSince` ("off air 2h 14m"). Auto-refresh on interval.
- Add a **portal card** ("Broadcast Health", 📡/🟢) with its own badge that turns red when
  off-air (same `checkTraffic` badge idiom on portal.html).
- Optional later: fold in Haivision SRT route stats (see haivision doc) for a combined view.

## Deliverable 3 — Inline IP Multiviewer
- Route `GET /multiviewer` → template `multiviewer.html` that `<iframe>`s the vendor viewer
  full-bleed under our header.
- **Default = thumbnail grid** (`/files/index.html` — cached thumbnails, low bandwidth).
- **A toggle button "▶ View / Hear Realtime Streams"** switches the iframe to the REALTIME
  net stream (`/files/watch.html#stream=REALTIME`) — live video + audio; a "◀ Back to
  thumbnails" button returns. (The button click is also the user gesture browsers require
  before audio can play — so audio "just works" on that click.)
- Change the existing portal "IP Multiviewer" card from an external link to `/multiviewer`
  (keep an "open in new tab ↗" affordance).
- **Mixed content: resolved** — Control Room is HTTP (confirmed), so the HTTP iframe is fine.
- **Caveats still to verify at build time:**
  - The multiviewer needs its own **login** (session in localStorage, scoped to the
    Stirlitz origin) — the user logs in once inside the iframe; it persists per browser.
  - `X-Frame-Options` / CSP on the Stirlitz box could forbid framing — verify it allows it.

## Backend endpoint spec
`GET /api/broadcast-health/status` (in `src/web/routes/` — new small router or add to
orders.py), returns cached JSON:
```json
{
  "state": "ok|degraded|offair|unknown",
  "checked_at": "2026-07-21T01:01:37-07:00",
  "networks": [{"name":"Central Valley","state":"ok","stations":[...]}],
  "offair":  [{"stationName":"CVC KBTV 8.2","title":"Video freeze alarm - no data","since":"..."}],
  "degraded":[...],
  "counts": {"total": 12, "offair": 0, "degraded": 0},
  "unreachable": false
}
```
- Server polls Stirlitz at most every ~15–30s (module-level cache with timestamp); many
  client requests → one device call.
- `urllib.request.urlopen(..., timeout=3)`; on exception → `{"state":"unknown",
  "unreachable":true}`. Key from `os.environ["STIRLITZ_MONITOR_KEY"]`.
- Derive per-station rollup from `alarmLines` (group the `.N.<type>` lines by `stationId`).

## Phasing
- **Phase 1 — DONE (commit e1efd18):** backend `/api/broadcast-health/status` (+ ~5s cache),
  verified live (12 stations, 0 off air). Alerts on video off/freeze/black/blue + audio-off.
- **Phase 2 — DONE (commit d150178):** global header indicator via `static/js/broadcast-health.js`
  injected on every HTML page by a response middleware in `app.py`. Green/red dot + label +
  corner toast on new off-air (sessionStorage-deduped). Verified across 8 routes with TestClient.
  Indicator currently links to the **external** multiviewer; repoint to inline `/multiviewer`
  when Phase 4 lands.
  - **Prod action:** `STIRLITZ_MONITOR_KEY` in `credentials.env` on each server (Lee: done on
    most; "the Bee" pending Jenna).
- **Phase 3:** Deliverable 2 (dashboard `/broadcast-health` + portal card). Lower priority —
  the header indicator + toast already surface outages; dashboard is the detailed view.
- **Phase 4 — DONE (commit 6b09981):** inline `/multiviewer` iframes the vendor viewer.
  Default = thumbnail grid; **"View / Hear Realtime Streams" button** swaps to
  `/files/watch.html#stream=REALTIME` (live video + audio; click = audio gesture) with a
  Thumbnails toggle. Device sends no X-Frame-Options/CSP (verified) so framing works.
  Portal card + health indicator/toast now deep-link to `/multiviewer`. Verified via TestClient.
  (Remaining: vendor viewer defaults to two grids side-by-side — layout quirk to tackle later.)
- **Future:** instant push (shared SSE/WebSocket broadcast) instead of 10s polling, if the
  poll latency isn't good enough.

## Decisions (Lee, 2026-07-21)
1. **Control Room = HTTP** → inline-multiviewer iframe is viable (no mixed content).
2. **Alarms that matter:** video off, video black/blue, audio off. **Ignore low audio level.**
   Binary green/red (no amber). Rule: red if title contains "Video" or == "Audio level - no
   data".
3. **Corner toast** (not modal). Re-alerts per newly off-air station (sessionStorage dedupe).
4. **Poll interval 10s** (server cache ~5s) — confirmed not a traffic concern.
5. **Middleware auto-injection on every page** — approved.

## Remaining verify items (at build time, not blockers)
- Confirm whether "video black/blue" raises its own Stirlitz alarm or is reported as
  "Video freeze" (the title-contains-"Video" rule catches either).
- Confirm the Stirlitz box allows framing (X-Frame-Options/CSP) for Deliverable 3.
