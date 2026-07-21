/*
 * Broadcast Health — global on-air indicator (Phase 2).
 *
 * Injected on EVERY Control Room page by a response middleware in app.py, so
 * this file is self-contained: it injects its own styles, drops a small
 * green/red indicator into the page header, polls the cached status endpoint,
 * and raises a corner toast when a station newly goes off air.
 *
 * Data: GET /api/broadcast-health/status  (see src/web/routes/broadcast_health.py)
 * Design: tasks/broadcast-health.md
 */
(function () {
  "use strict";
  if (window.__bhLoaded) return;            // guard against double-injection
  window.__bhLoaded = true;

  var POLL_MS = 10000;                       // client poll cadence (server caches ~5s)
  var STATUS_URL = "/api/broadcast-health/status";
  var MULTIVIEWER_URL = "http://34.208.18.64/files/index.html";  // external viewer — where to look on an outage
  var ALERTED_KEY = "bhAlertedStations";     // sessionStorage dedupe of toasts

  function alerted() {
    try { return new Set(JSON.parse(sessionStorage.getItem(ALERTED_KEY) || "[]")); }
    catch (e) { return new Set(); }
  }
  function saveAlerted(set) {
    try { sessionStorage.setItem(ALERTED_KEY, JSON.stringify([].slice.call(set))); } catch (e) {}
  }

  function injectStyles() {
    if (document.getElementById("bh-styles")) return;
    var css =
      ".bh-indicator{display:inline-flex;align-items:center;gap:7px;margin-left:auto;" +
      "font-size:.78rem;font-weight:600;cursor:pointer;white-space:nowrap;" +
      "color:var(--nord4,#d8dee9);text-decoration:none;padding:4px 6px;border-radius:6px;}" +
      ".bh-indicator:hover{background:rgba(255,255,255,.06);}" +
      ".bh-dot{width:9px;height:9px;border-radius:50%;background:var(--nord3,#4c566a);flex-shrink:0;}" +
      ".bh-indicator.ok .bh-dot{background:var(--nord14,#a3be8c);}" +
      ".bh-indicator.offair .bh-dot{background:var(--nord11,#bf616a);animation:bh-pulse 1.1s infinite;}" +
      ".bh-indicator.unknown .bh-dot{background:var(--nord3,#4c566a);}" +
      ".bh-indicator.offair{color:var(--nord11,#bf616a);}" +
      "@keyframes bh-pulse{0%,100%{opacity:1}50%{opacity:.3}}" +
      ".bh-toast-wrap{position:fixed;bottom:20px;right:20px;z-index:100000;display:flex;" +
      "flex-direction:column;gap:10px;max-width:360px;}" +
      ".bh-toast{background:var(--nord11,#bf616a);color:#fff;padding:13px 15px;border-radius:10px;" +
      "box-shadow:0 8px 26px rgba(0,0,0,.4);font-size:.85rem;line-height:1.4;cursor:pointer;" +
      "display:flex;gap:10px;align-items:flex-start;animation:bh-slide .25s ease;}" +
      ".bh-toast b{display:block;font-size:.9rem;margin-bottom:2px;}" +
      ".bh-toast small{opacity:.9;}" +
      ".bh-toast-x{margin-left:auto;background:none;border:none;color:#fff;font-size:1.1rem;" +
      "line-height:1;cursor:pointer;opacity:.8;padding:0 2px;}" +
      ".bh-toast-x:hover{opacity:1;}" +
      "@keyframes bh-slide{from{transform:translateY(12px);opacity:0}to{transform:none;opacity:1}}";
    var s = document.createElement("style");
    s.id = "bh-styles";
    s.textContent = css;
    document.head.appendChild(s);
  }

  var indicatorEl = null;
  function ensureIndicator() {
    if (indicatorEl && document.body.contains(indicatorEl)) return indicatorEl;
    var el = document.createElement("a");
    el.className = "bh-indicator unknown";
    el.href = MULTIVIEWER_URL;
    el.target = "_blank";
    el.rel = "noopener";
    el.title = "Broadcast health — click to open the multiviewer";
    el.innerHTML = '<span class="bh-dot"></span><span class="bh-label">Checking…</span>';
    var header = document.querySelector(".header-inner");
    if (header) {
      var badge = header.querySelector(".header-badge");
      if (badge) header.insertBefore(el, badge);
      else header.appendChild(el);
    } else {
      el.style.position = "fixed";
      el.style.top = "16px";
      el.style.right = "20px";
      el.style.zIndex = "100000";
      document.body.appendChild(el);
    }
    indicatorEl = el;
    return el;
  }

  function toastWrap() {
    var w = document.getElementById("bh-toast-wrap");
    if (!w) {
      w = document.createElement("div");
      w.id = "bh-toast-wrap";
      w.className = "bh-toast-wrap";
      document.body.appendChild(w);
    }
    return w;
  }

  function showToast(station) {
    var t = document.createElement("div");
    t.className = "bh-toast";
    var titles = (station.titles || []).join(", ");
    t.innerHTML =
      '<div><b>⚠ ' + esc(station.stationName) + " is off air</b>" +
      (titles ? "<small>" + esc(titles) + "</small>" : "") + "</div>";
    var x = document.createElement("button");
    x.className = "bh-toast-x";
    x.textContent = "×";
    x.setAttribute("aria-label", "Dismiss");
    x.onclick = function (e) { e.stopPropagation(); e.preventDefault(); t.remove(); };
    t.appendChild(x);
    t.onclick = function () { window.open(MULTIVIEWER_URL, "_blank", "noopener"); };
    toastWrap().appendChild(t);
    setTimeout(function () { if (t.parentNode) t.remove(); }, 30000);  // auto-dismiss; indicator stays red
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function render(data) {
    var el = ensureIndicator();
    var offair = (data && data.offair) || [];
    if (!data || data.state === "unknown" || data.unreachable) {
      el.className = "bh-indicator unknown";
      el.querySelector(".bh-label").textContent = "Health unknown";
      el.title = "Broadcast health unavailable" + (data && data.error ? " — " + data.error : "");
      return;
    }
    if (data.state === "offair" && offair.length) {
      el.className = "bh-indicator offair";
      var names = offair.map(function (s) { return s.stationName; }).join(", ");
      el.querySelector(".bh-label").textContent =
        offair.length + " off air";
      el.title = "OFF AIR: " + names + " — click to open the multiviewer";
      // Toast only for stations not already alerted this session.
      var seen = alerted();
      var current = new Set();
      offair.forEach(function (s) {
        current.add(s.stationId);
        if (!seen.has(s.stationId)) { showToast(s); seen.add(s.stationId); }
      });
      // Drop recovered stations so they can re-alert if they fail again.
      var pruned = new Set();
      seen.forEach(function (id) { if (current.has(id)) pruned.add(id); });
      saveAlerted(pruned);
    } else {
      el.className = "bh-indicator ok";
      el.querySelector(".bh-label").textContent = "All networks on air";
      el.title = "All " + ((data.counts && data.counts.stations) || "") + " networks on air";
      saveAlerted(new Set());   // clear dedupe when everything is healthy
    }
  }

  function poll() {
    fetch(STATUS_URL, { signal: (window.AbortSignal && AbortSignal.timeout) ? AbortSignal.timeout(8000) : undefined })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { render(d); })
      .catch(function () { render(null); });
  }

  function start() {
    injectStyles();
    ensureIndicator();
    poll();
    setInterval(poll, POLL_MS);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
