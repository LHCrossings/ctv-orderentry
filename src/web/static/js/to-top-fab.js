// Floating return-to-top button — the "log version" arrow in the bottom-right
// corner (Ashe: "constant button no matter where we are on the page", and
// 2026-08-13: MC looks for it in that corner on every operations page).
// Include with: <script src="/static/js/to-top-fab.js?v=1"></script> before
// </body> on any page that needs it. Self-contained: injects its own style
// and button, so pages carry no markup or CSS for it.
(function () {
    'use strict';

    function init() {
        if (document.getElementById('toTopFab')) return; // page already has one

        const style = document.createElement('style');
        style.textContent = `
        .to-top-fab {
            position: fixed;
            right: 24px;
            bottom: 24px;
            /* Above ordinary page content, below page modals
               (daily_programming's .modal-overlay is z-index 50). */
            z-index: 40;
            width: 46px;
            height: 46px;
            border-radius: 50%;
            border: 1px solid var(--border-color);
            background: rgba(255, 255, 255, 0.55);
            -webkit-backdrop-filter: blur(6px);
            backdrop-filter: blur(6px);
            box-shadow: 0 4px 14px var(--shadow-color);
            color: var(--nord3);
            font-size: 1.2rem;
            line-height: 1;
            cursor: pointer;
            font-family: inherit;
            transition: background 0.15s, color 0.15s, border-color 0.15s, transform 0.15s;
        }
        .to-top-fab:hover {
            background: var(--nord10);
            border-color: var(--nord10);
            color: var(--nord6);
            transform: translateY(-2px);
        }
        .to-top-fab:focus-visible { outline: 2px solid var(--nord10); outline-offset: 2px; }
        /* Broadcast-health alarms live in this same corner (fixed, bottom:20px,
           right:20px, z-index 100000). Lift their stack above the button so an
           off-air toast never covers it. Must be the #id selector:
           broadcast-health.js appends its <style> at runtime, so a
           .bh-toast-wrap rule could lose the tie. */
        #bh-toast-wrap { bottom: 84px; }
        `;
        document.head.appendChild(style);

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.id = 'toTopFab';
        btn.className = 'to-top-fab';
        btn.title = 'Return to top';
        btn.setAttribute('aria-label', 'Return to top');
        btn.innerHTML = '&#8593;';
        // Jump instantly when the user prefers reduced motion, or when the
        // page is so long that a smooth scroll would crawl.
        btn.addEventListener('click', () => {
            const far    = window.scrollY > 12000;
            const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            window.scrollTo({ top: 0, behavior: (far || reduce) ? 'auto' : 'smooth' });
        });
        document.body.appendChild(btn);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
