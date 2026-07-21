# Stirlitz Media IP Multiviewer / Logger — Integration Reference

Discovery notes for the Stirlitz Media IP Multiviewer (built on the SML — Stirlitz
Media Logger — Server 6 platform). Self-contained; read this instead of re-doing
discovery. **Caveat:** Stirlitz does not publish a full REST endpoint spec; the
detailed "Web API" is gated to licensed IP Multiviewer *Enterprise* customers.
What's below is confirmed from the public product pages + the Server 6 / Player 6
manuals; items marked ⚠ still need the vendor API doc or a look at our own device.
**We subscribe to IP Multiviewer *Enterprise* (confirmed 2026-07-21)**, so the JSON
Web API for current/recent alarms IS available to us — we just need its endpoint spec.

- **Vendor:** https://www.stirlitzmedia.com/products/ip-multiviewer/
- **Manuals:** https://manuals.stirlitzmedia.com/ (Server6 = logger/MV, Player6 = webPlayer)
- **Our device:** http://34.208.18.64/files/index.html (Control Room "IP Multiviewer" card)
  - Serves **plain HTTP on port 80** — `:443` refuses connections (HTTPS not enabled).
    WebFetch force-upgrades http→https, so it can't reach the device; a plain-HTTP
    client from a host that can route to it (+ login) is needed to inspect it live.

---

## Platform facts (confirmed from the manuals)

**Embedded web server (webPlayer)** — `Server6/access.html`
- Off by default; **HTTP port 80** (configurable). **HTTPS/TLS 1.2** optional — certs
  go in `smlserver\sslcerts\` (`cert`, `key`, `rootcert`, `ciphers.txt`).
- **SML Player (Windows) port 12540** (fixed).
- **Auth:** user/group management, or **Active Directory** (HTTPS required for AD web auth).
- **Multi-server federation:** "Secondary webPlayer URLs" merge several SML servers into
  one station list under a master.
- **Export profiles:** codec, bitrate, resolution, burn-in timecode, DVB subtitles;
  export **to the browser or to a network path**.
- Outputs per channel: low-latency browser playback, MPEG-TS, **SRT**, NDI.

**Alarms module** — `Server6/module-alarms.html`
- **Email (SMTP):** server/port (default 587), auth, separate recipient lists for
  error / warning / info; test button. Per-station audio/video error recipients set
  in each station's config (e.g. "audio below X dB for >60s").
- **SNMP traps:** community `public` only; enterprise ID; trap types for
  **error / warning / info / low-signal-level**.

**webPlayer / exports** — `Player6/`
- webPlayer = browser client to browse, select, and **export archived programmes**;
  embeddable into a third-party browser UI (the "webPlayer API").
- HTTP/HTTPS clip/log export is a documented capability.

---

## Integration surfaces (ranked value-for-risk)

1. **SNMP trap receiver → Control Room outage badge.** The cleanest *documented*,
   standards-based hook. Point the MV's traps at our server; catch error/warning/
   low-signal traps and light a red badge like Traffic's "missing materials" pill.
   Feeds the MC outage-escalation manuals. No vendor API doc needed.
2. **HTTP clip export → Airchecks tie-in.** The logger already archives every channel;
   its HTTP export can pull clips programmatically. Strong fit with our Airchecks
   feature (which records network streams to MP4) — potentially pull from the archive
   instead of/along with live capture. ⚠ Need the exact export URL + params (vendor doc
   or device inspection).
3. **Embed the webPlayer** in a Control Room page (iframe/script) for live channel
   views + clip review, instead of linking out to the separate UI. Needs webPlayer
   login (or AD + HTTPS). ⚠ Need the embed URL/param pattern.
4. **Web API for current/recent alarms (JSON)** — **available to us (Enterprise).**
   Enables a native alarm dashboard pulled straight into Control Room (JSON, like the
   Haivision health card). Now a first-class option alongside SNMP. ⚠ Still need the
   exact endpoint spec (Enterprise Web API doc).

## Open items before building
- [x] Edition confirmed: **IP Multiviewer Enterprise** → JSON alarm Web API available.
- [ ] Get the **Enterprise Web API doc** (endpoints/auth/JSON shape) from Stirlitz
      support, or inspect our device for the export/embed/alarm URL patterns.
- [ ] Plain-HTTP reachability to `34.208.18.64:80` from the app server + a webPlayer
      login (read-only if possible).
- [ ] Decide first build: an **SNMP trap listener → badge** is the lowest-risk start and
      needs no gated docs.

## Contrast with Haivision (see haivision-srt-gateway-api.md)
- Haivision = a clean, self-documented **REST API** (cookie auth, JSON) → good for a
  pull-based health dashboard.
- Stirlitz = monitoring/logging platform; the reliable programmatic hooks are
  **push-based (SNMP/email)** + **webPlayer embed/export**; its richer Web API is gated.

_Discovery session: 2026-07-21._
