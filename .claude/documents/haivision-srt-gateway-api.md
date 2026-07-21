# Haivision SRT Gateway (HMG/HSG) REST API — Reference

Discovery notes and integration reference for the Haivision Media Gateway /
SRT Gateway REST API. Self-contained — read this instead of re-doing discovery.

- **Official docs:** https://doc.haivision.com/HMG/4.2/rest-api-integrator-s-reference
- **Our device:** https://44.235.103.12/login  (also a Control Room home-page card)
- **API base:** `https://44.235.103.12:443/api`
- **Firmware:** 4.2.0-3694 (confirmed 2026-07-21) → use the **v2** endpoints below,
  not the legacy `/api/gateway/{deviceID}/...` paths.
- **TLS:** IP-addressed host → self-signed cert; clients must skip/trust cert verification.

---

## Authentication (cookie-based)

1. **Log in:** `POST /api/session` with JSON body `{ "username": "...", "password": "..." }`.
2. Success returns a **session cookie**. Include it on every subsequent request
   (browsers, Postman, and cookie-jar HTTP clients do this automatically).
3. **Check session:** `GET /api/session`.  **Log out:** `DELETE /api/session`.
4. A **401 Unauthorized or 404** mid-session usually means the cookie expired —
   re-issue `POST /api/session`.
5. Each endpoint documents the **user role** it requires. Use a **read-only role**
   for monitoring; only use a write-capable role for start/stop/config.

### Conventions
- Content types accepted (POST/PUT): `application/json`, `application/octet-stream`,
  `multipart/form-data`.
- Success = `200 OK` unless noted. `415` = unsupported content type.
- Error body:
  ```json
  { "error": { "type": "...", "message": "..." } }
  ```
  (`type` = `"SessionAuthorization"` when the role lacks permission.)
- JSON property classes: **Required** (error if omitted), **Optional** (defaults on
  PUT / current value on POST), **Immutable** (can't change after create), **Ignored**
  (informational).

---

## Endpoint map (v4.2)

### Session / devices
| Method | Path | Purpose |
|---|---|---|
| GET/POST/DELETE | `/api/session` | check / log in / log out |
| GET | `/api/devices` | list devices (response `_id` = device ID) |
| GET | `/api/devices/{id}` | device config detail |
| GET | `/api/tags` | device tags |

### Routes (v2 — the 4.2 workflow)
| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/api/routes` | list / create |
| GET / POST / DELETE | `/api/routes/{routeID}` | get / update / delete |
| PUT | `/api/routes/{routeID}/actions` | **start / stop a route** |
| GET | `/api/routes/{routeID}/thumbnail` | route thumbnail image |
| GET / POST | `/api/system/preset` | export / import full config |

### Sources / destinations
| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/api/sources` | list / create |
| PUT / DELETE | `/api/sources/{id}` | modify / delete |
| GET / POST | `/api/destinations` | list / create |
| PUT / DELETE | `/api/destinations/{id}` | modify / delete |
| PUT | `/api/routes/{routeID}/destinations/{destID}/actions` | **start/stop one destination** |

### Statistics / health
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/statistics?routeID=` | route performance metrics |
| GET | `/api/statistics/srt?routeID=` | SRT stats file download |
| GET | `/api/gateway/{deviceID}/statistics?routeID=&sourceID=` | source-level stats |
| GET | `/api/gateway/{deviceID}/statistics?routeID=&destinationID=` | destination-level stats |
| GET | `/api/gateway/{deviceID}/statistics/client?routeID=&destinationID=&clientAddress=&clientPort=` | per-SRT-client metrics (RTT, packet loss, …) |

### Legacy (`/api/gateway/{deviceID}/...`)
Still present (routes, updates, commands, preset, statistics). Prefer v2 on 4.2;
keep only as a fallback.

---

## Gotchas
- **Update-a-route is destructive.** GET the full route config, strip read-only
  fields (`elapsedTime`, `started`, …), modify, then POST the *whole* object back —
  any destination you omit gets **deleted**. Reads are safe; writes need care.
- Self-signed cert → the HTTP client must disable/trust cert verification.
- Cookie expiry → treat a sudden 401/404 as "re-login and retry".

---

## Integration ideas for Control Room (ranked value-for-risk)
1. **Read-only SRT health dashboard card** — poll `/api/routes` + `/api/statistics`,
   show each stream up/down with bitrate / RTT / packet loss. Nord + Chart.js like the
   rest of the app. Lowest risk, highest value; start here.
2. **Outage badge** — same pattern as the Traffic "missing materials" badge: card goes
   red when a route drops or a destination's SRT loss crosses a threshold. Feeds the
   MC outage-escalation manuals.
3. **One-click start/stop / failover** — `PUT /api/routes/{id}/actions`, once the read
   path is trusted.
4. **Aircheck tie-in** — if airchecks pull network feeds the gateway distributes,
   source/destination stats can confirm a clean recording.

## Open items before building
- [ ] Read-only service credential for the gateway.
- [ ] Reachability check for `44.235.103.12:443` from the app server (Windows/Tailscale,
      not WSL) — needs credential + go-ahead; it's a production device.
- [ ] Verify live JSON shapes for `/api/routes` and `/api/statistics` against the device.

_Discovery session: 2026-07-21._
