# Stirlitz Media IP Multiviewer (Enterprise) — Integration Reference

Live-verified integration reference for our Stirlitz IP Multiviewer Enterprise.
Everything Stirlitz we own runs on **one box** (per Lee). Self-contained; read this
instead of re-doing discovery.

- **Vendor:** https://www.stirlitzmedia.com/products/ip-multiviewer-enterprise/
- **Manuals:** https://manuals.stirlitzmedia.com/ (Server6, Player6)
- **Device:** `http://34.208.18.64` (Control Room "IP Multiviewer" card → `/files/index.html`)
  - **Plain HTTP on port 80** (`:443` refused — HTTPS not enabled). WebFetch force-upgrades
    to HTTPS so it can't reach it; use a plain-HTTP client (Bash `urllib` from the session
    sandbox works).

---

## Authentication (verified live 2026-07-21)

Reverse-engineered from `/files/communication.js` and confirmed by logging in.

**Two ways to authenticate:**
1. **Session login** (for interactive/UI-style use):
   - `POST /sessions`, body = JSON `{ "user":"<name>", "passhash":"<md5(password)>" }`
     sent as **`text/plain`** (the XHR core sets no Content-Type; sending
     `application/json` makes the server parser fail: `Unexpected type COLON`).
   - Response `{ "session":"<28-char token>" }`. Send it on every call as HTTP header
     **`session: <token>`** (NOT a cookie). Logout: `DELETE /sessions/{token}`.
2. **Access key** (the clean **server-to-server** path — no login):
   - `GET /accesskeysmonitor` (session-auth'd) returns the monitor **accessKey**.
   - Then call the monitor API with **`?accessKey=<key>`** — no session header needed.
   - Keys are validated: a wrong/absent key → HTTP 400 `{ "error":"NO_SESSION" }`.
   - **Two keys already exist: one scoped `monitor`, one `Full control`.** Use the
     **`monitor`** key for our read-only health poller (least privilege — it's the scope
     `/alarmsState/monitor` expects). Do NOT use the Full-control key for polling.

Error conventions: 400 `NO_SESSION` (missing/invalid auth), 404 `NO_RESOURCE` (unknown
path), `NO_PERMISSIONS` (role). Success = 200.

---

## Endpoints (verified live)

### Alarms — the health feed we want (star)
| Method | Path | Returns |
|---|---|---|
| GET | `/accesskeysmonitor` | `{ "accessKeys":"<key>" }` — the monitor access key |
| GET | `/alarmsState/monitor?accessKey=<key>` | live alarm state — **key-only auth, no login** |

`/alarmsState/monitor` response shape:
```json
{
  "currentDate": "2026-07-21T00:24:40.740-07:00",
  "alarmLines": {
    "CVC KBTV 8.2 type:srt source:srt://10.0.0.32:6018 ei:0.1.level": {
      "title": "Video freeze alarm",
      "alarm": "0",
      "alarmSince": "2026-07-20T03:27:44.633-07:00",
      "stationId": "CVC KBTV 8.2 type:srt source:srt://10.0.0.32:6018 ei:0",
      "stationName": "CVC KBTV 8.2"
    }
  }
}
```
- `alarm` = `"0"` means OK; non-zero/non-empty = **ACTIVE**.
- **4 monitored conditions per station:** `Video freeze alarm`, `Video freeze alarm - no
  data`, `Audio track level below threshold`, `Audio level - no data`.
- ~44 alarm lines on our box (~12 video stations + 10 audio, all markets). `alarmSince` =
  how long the current state has held. Poll this one URL → full station health in one call.

### Live monitor / inventory
| Method | Path | Returns |
|---|---|---|
| GET | `/live/screens` | wall layout: per-station `shortName,left,top,width,height`; key encodes `type:srt source:srt://10.0.0.x:port` (SRT-source inventory) |
| GET | `/live/streams` , `/live/streams/{s}/video/playlist` (+segments) | live video via MSE (fragmented mp4, H.264/H.265) |
| GET | `/netstreamsWatch` | `{"netstreamsWatch":["REALTIME"]}` |
| GET | `/navigationBar` | modules+role (crossingstv → `["monitor"]`) |
| POST | `/screenclick/` | tile interaction |
| GET/PUT | `/users/` | user admin (returns staff accounts + roles — treat as sensitive; do NOT dump) |
| GET | `/live/screens/{screen}/preview` | JPEG snapshot of a screen — **requires the `session` header** (accessKey is rejected → "Access violation"). Screens are `0.0` and `stream-REALTIME`. |
| — | `/files/preview.html#screen=<name>` | single-screen preview page (dedupes the default two-grid view) |
| — | `/files/watch.html#stream=<name>` | per-stream LIVE VIDEO+audio page (embeddable, works in all browsers) |
| — | `/files/activeAlarms/index.html` | the alarms UI (where the alarm API is used) |

**⚠ Thumbnail previews are Firefox-only.** The vendor viewer loads screen previews via an
authenticated fetch (`ffFetchImage`) **only on Firefox**; on Chrome/Edge it falls back to a
plain `<img src=".../preview">` which cannot send the `session` header → 401 "error loading
image". The **live realtime video (`watch.html`) authenticates via MSE fetch and works
everywhere.** So the inline `/multiviewer` defaults to the realtime stream, not thumbnails.
To offer working low-bandwidth thumbnails on Chrome we'd need a **server-side proxy**
(server holds a session, fetches `/live/screens/{s}/preview`, re-serves the JPEG) — which
needs a multiviewer **username+password** (the monitor accessKey does NOT work for preview).

---

## Integration surfaces
### Buildable NOW (no new licensing; needs only an accessKey)
1. **SRT / signal health dashboard + outage badge** (star) — poll
   `/alarmsState/monitor?accessKey=<key>`, count active alarms (`alarm != "0"`), and per
   station show video-freeze / audio-loss with `alarmSince` duration. Light a red badge on
   the Control Room card like Traffic's "missing materials" pill; feeds the MC
   outage-escalation manuals. Key-only auth → a simple server-side poller, Nord + Chart.js.
2. **Channel / SRT-source inventory card** — from `/live/screens`: which markets are on
   the wall and their `srt://...` sources.
3. **Embed the live wall / a tile** — iframe `/files/index.html` or
   `/files/watch.html#stream=<name>` behind a session, instead of the external link.

### Pending investigation
4. **Recent/historical alarms view.** The alarms UI links to a **history sub-app**
   (`/files/historyAlarms/index.html`, found in `activeAlarms.js`) — so an alarm-history
   browser exists. `/alarmsState/monitor` is the *current* state; the history endpoint lives
   under that sub-app (map it the same way when we want an alarm-history panel). Enterprise
   also has a "SQL archive of all events + web interface to export events history."
5. **Clip export (Airchecks tie-in) needs the separate LOGGER product — future/low
   priority.** Per the Enterprise page, continuous channel recording / DVR / clip export is
   **not** in IP Multiviewer Enterprise — it's the distinct **Stirlitz Media Logger**
   product (would be a paid add-on). Our own Airchecks utility already works ~99% of the
   time and we have a workaround; the only gap is the occasional request for something that
   wasn't recorded. **Lee may price Stirlitz Logger later** as a nice-to-have — not a
   current build item. (Enterprise's "export" = *event/alarm history*, not video clips.)

## Open items
- [x] Auth mapped + login verified; **access-key (key-only) auth confirmed** on the alarm API.
- [x] **Alarm Web API found & verified:** `/alarmsState/monitor?accessKey=` (the health feed).
- [ ] Lee to mint a **dedicated accessKey** (and ideally a scoped/read-only account) for us.
- [ ] Confirm whether the box **records/logs** an archive → decides the Airchecks export path.
- [ ] Review the Enterprise product page for any other module we haven't probed.

## Contrast with Haivision (see haivision-srt-gateway-api.md)
- Haivision = pull REST (cookie login) for SRT route/stream **stats**.
- Stirlitz = pull JSON **alarm/health feed** (`/alarmsState/monitor`, key-only) + live video.
- Both speak SRT and both can feed one Control Room "Broadcast Health" view.

_Discovery + live verification: 2026-07-21. (Access-key value kept out of this doc — fetch
it from `/accesskeysmonitor` or use the dedicated key Lee provisions.)_
