/*
 * Portal link — a header button to the main portal (http://portal.home/).
 *
 * Kurt (2026-10-28): master control is comfortable in Control Room but does
 * not see it as part of the main portal system. Injected on EVERY page by the
 * response middleware in app.py (next to broadcast-health.js), so this file is
 * self-contained: it finds the page's 🏠 home button and places a sibling
 * "portal" button right after it. The home button itself is untouched (Lee:
 * a separate button, never take over the existing home button).
 */
(function () {
  "use strict";
  if (window.__portalLinkLoaded) return;    // guard against double-injection
  window.__portalLinkLoaded = true;

  var PORTAL_URL = "http://portal.home/";

  function injectStyles() {
    if (document.getElementById("portal-link-styles")) return;
    var s = document.createElement("style");
    s.id = "portal-link-styles";
    s.textContent =
      ".portal-btn{margin-left:6px;}" +
      ".portal-btn::after{content:\"portal\";}";
    document.head.appendChild(s);
  }

  function mount() {
    if (document.querySelector(".portal-btn")) return;
    // Every Control Room header carries the home button as the first anchor
    // (class home-btn, or cr-back on the two billing pages). Clone it so the
    // portal button inherits whichever styling that page uses.
    var home = document.querySelector('a.home-btn[href="/"], a.cr-back[href="/"]');
    if (!home) return;
    injectStyles();
    var el = home.cloneNode(false);
    el.classList.add("portal-btn");
    el.href = PORTAL_URL;
    el.title = "Main portal";
    el.textContent = "🌐";
    home.insertAdjacentElement("afterend", el);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
