"""Every 🏠 home button navigates to the portal root — never to a section hub.

Lee (2026-08-13, after /programming/weekly-schedules linked its home button to
/programming): "Home goes to home." This has regressed more than once, so this
test makes the rule structural: any template anchor with class "home-btn" must
have href="/". A new page that points its home button anywhere else fails here
instead of shipping.
"""

import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "web" / "templates"

# An <a> tag that carries the home-btn class, in either attribute order.
_ANCHOR_RE = re.compile(r"<a\b[^>]*class=\"[^\"]*\bhome-btn\b[^\"]*\"[^>]*>", re.IGNORECASE)
_HREF_RE = re.compile(r"href=\"([^\"]*)\"", re.IGNORECASE)


def _home_buttons():
    for path in sorted(TEMPLATES_DIR.rglob("*.html")):
        for tag in _ANCHOR_RE.findall(path.read_text(encoding="utf-8")):
            href = _HREF_RE.search(tag)
            yield path.relative_to(TEMPLATES_DIR), tag, href.group(1) if href else None


def test_templates_exist():
    assert TEMPLATES_DIR.is_dir(), f"templates dir moved? {TEMPLATES_DIR}"


def test_home_buttons_found():
    # Guard the guard: if the class is renamed site-wide, this test must not
    # silently pass on zero matches.
    assert sum(1 for _ in _home_buttons()) >= 40


def test_every_home_button_goes_home():
    offenders = [f"{rel}: {tag}" for rel, tag, href in _home_buttons() if href != "/"]
    assert not offenders, (
        "Home buttons must link to '/' (Lee: 'Home goes to home'):\n  " + "\n  ".join(offenders)
    )
