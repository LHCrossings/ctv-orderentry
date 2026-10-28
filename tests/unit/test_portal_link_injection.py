"""The portal header button (Kurt, 2026-10-28) is one script injected site-wide
by the app.py middleware; it mounts next to the page's 🏠 by selector. Pin the
three pieces that must agree: the tag is injected at the LAST </body> like the
broadcast-health script, the script targets the portal and the home selector,
and every template's home button still matches that selector."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = (ROOT / "src/web/app.py").read_text()
JS = (ROOT / "src/web/static/js/portal-link.js").read_text()
TEMPLATES = ROOT / "src/web/templates"

HOME_ANCHOR = re.compile(r'<a href="/" class="(home-btn|cr-back)">🏠</a>')


def test_portal_tag_is_injected_at_the_last_body_tag():
    assert 'src="/static/js/portal-link.js?v=' in APP
    assert '_inject_last(body, b"</body>", _PORTAL_TAG)' in APP


def test_script_targets_the_portal_and_leaves_the_home_button_alone():
    assert 'PORTAL_URL = "http://portal.home/"' in JS
    assert 'a.home-btn[href="/"], a.cr-back[href="/"]' in JS
    assert "cloneNode" in JS and 'insertAdjacentElement("afterend"' in JS
    assert "home.href" not in JS and "home.textContent" not in JS


def test_landing_page_without_a_home_button_gets_the_fallback_mount():
    portal = (TEMPLATES / "portal.html").read_text()
    assert "🏠" not in portal and 'class="logo"' in portal
    assert 'querySelector(".header-inner .logo")' in JS
    assert 'insertAdjacentElement("afterbegin", el)' in JS


def test_every_home_button_matches_the_mount_selector():
    missing = []
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text()
        if "🏠" in text and not HOME_ANCHOR.search(text):
            missing.append(str(path.relative_to(ROOT)))
    assert not missing, f"home buttons the portal link cannot find: {missing}"
