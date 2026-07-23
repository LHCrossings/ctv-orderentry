"""Per-show "setup profiles" for the Set up Daily Programming tool.

Some shows need extra elements layered in when they're set up, beyond the raw
program content — e.g. open/close bumpers for Korean News. A profile describes
how to identify the show and what extra elements it requires; the run engine
(daily_programming_run.run_market) consults it and conforms the schedule to it.

SOURCE OF TRUTH = the `chat.show_profiles` table in the shared Etere SQL DB, so
every app deployment sees the same exceptions (a local file/DB would diverge
across instances). The `_DEFAULT_PROFILES` below are the **seed** for that table
(see scripts/setup_show_profiles_table.py) AND the **fallback** used when the
table is missing/empty or the DB is briefly unreachable — so the tool always
works. A future "manage exceptions" UI writes rows to the table.

The PRIMARY match key is `code_re` (a regex on the file's COD_PROGRA, e.g.
Korean News = ^NEWSTODAY\\d{6}$). Everything except name/code_re is stored in the
row's `config` JSON column (networks, days, window, open_bumper, close_bumper,
and any future element types) — so new element kinds need no schema change.

Element types so far:
  * fillers — handled separately (the pieces/fillers path in the modal).
  * fcc_id — on a `daily: true` profile (one placement per broadcast day
    in the last COMS break before midnight; swept by the run route via
    daily_elements/sweep_daily_ids, independent of any show). Per-show
    placement (segment/break/position/anchor, placed by run_market) is still
    supported by the engine but no longer used — the Children profile that
    used it was retired 2026-07-15.
  * bumpers — {code, event_type}; the engine inserts the bumper if it isn't
    pre-placed and conforms its type/order:
      open  bumper → break 1, first position, EVENT_TYPE 'F' (locked anchor)
      close bumper → after the last segment, EVENT_TYPE 'T' (floats)
    Program segments themselves are EVENT_TYPE 'T'.
"""
from __future__ import annotations

import json
import re

# Market code → COD_USER, and the named "OTA" (over-the-air) market group used by
# element scopes. OTA spans both networks: SFO/CVC are CTV, DAL is TAC.
MARKET_CODE_TO_CU = {"NYC": 1, "CMP": 2, "HOU": 3, "SFO": 4, "SEA": 5,
                     "LAX": 6, "CVC": 7, "WDC": 8, "MMT": 9, "DAL": 10}
OTA_MARKETS = ["SFO", "CVC", "DAL"]
CTV_MARKETS = ["NYC", "CMP", "HOU", "SFO", "SEA", "LAX", "CVC", "WDC", "MMT"]  # all CTV (no DAL)

_DEFAULT_PROFILES = [
    {
        "name": "Korean News",
        "code_re": r"^NEWSTODAY\d{6}$",   # NEWSTODAYmmddyy — PRIMARY match
        "networks": ["CTV"],              # CTV markets only (no DAL)
        "days": "M-F",
        "window": ("08:00", "09:00"),
        # NOTE: still in the legacy open_bumper/close_bumper shape — the validated
        # run_market bumper path reads these directly. Migrate to `elements` when the
        # UI lands (the engine will normalize both).
        "open_bumper": {"code": "BUMP_MBCNEWSTODAY_OPEN", "event_type": "F"},
        "close_bumper": {"code": "BUMP_MBCNEWSTODAY_CLOSE", "event_type": "T"},
    },
    {
        # Kingdom of God (CTV, Sunday 6-7a religious slot). The religious open/
        # disclaimer airs at the top of the hour as the F-locked anchor, and the
        # program (KOG<date>, a single whole file) plays immediately after as T —
        # exactly the anchor mechanic the retired Children FCC-ID used. Matched by
        # KOG file code only (per Lee); other 6a religious titles are out of scope.
        # All 9 CTV markets; the 6-7a hour is a single PRGS slot, so the open and
        # KOG share it (open first).
        "name": "Kingdom of God",
        "code_re": r"^KOG",               # KOG<mmddyy> / KOG-<mmddyy> / KOG0410 …
        "networks": ["CTV"],
        "days": "Su",
        "window": ("06:00", "07:00"),
        "elements": [
            {"kind": "id", "id": 3653, "code": "RELIGIOUSOPEN10E01",
             "markets": "ctv", "segment": "PRGS", "break": 1, "position": "first",
             "event_type": "F", "anchor": True},
        ],
    },
    # (The per-show "Children" profile — kids FCC ID 2891 anchored at the top of
    # the first PRGS break on SFO/CVC — was retired 2026-07-15 when all three
    # OTA markets moved to the daily end-of-day ID below. Its chat.show_profiles
    # row is disabled, not deleted, in case it ever needs to come back.)
    {
        # Standing DAILY element — not show-matched (no code_re/label, so
        # profile_for never returns it). All programming carries the E/I logo,
        # so the FCC only requires a daily public notice of where the children's
        # programming records reside: one ID per broadcast day, in the last COMS
        # break before midnight (24:00), type T so master control can settle it
        # as the final item aired in the calendar day. Swept by the run route
        # (today → +2) whenever a Daily Programming run includes the market.
        # Each OTA market has its own ID asset, hence one element per market.
        "name": "OTA FCC ID (daily)",
        "daily": True,
        "elements": [
            {"kind": "fcc_id", "id": 83128, "code": "ID - TACDAL - FCC",
             "markets": ["DAL"], "placement": "last_coms_before_midnight",
             "event_type": "T"},
            {"kind": "fcc_id", "id": 142947, "code": "ID - CTVCVC - FCC",
             "markets": ["CVC"], "placement": "last_coms_before_midnight",
             "event_type": "T"},
            {"kind": "fcc_id", "id": 142948, "code": "ID - CTVSFO - FCC",
             "markets": ["SFO"], "placement": "last_coms_before_midnight",
             "event_type": "T"},
        ],
    },
]


def default_profiles():
    """A fresh copy of the built-in seed/fallback profiles."""
    return [dict(p) for p in _DEFAULT_PROFILES]


def to_config(profile):
    """The JSON `config` payload for a profile row — everything except the
    name/code_re/label columns (networks, days, window, bumpers, elements, …)."""
    return {k: v for k, v in profile.items() if k not in ("name", "code_re", "label")}


def load_profiles():
    """Active profiles from chat.show_profiles (shared Etere DB).

    Falls back to the built-in defaults if the table is missing/empty or the DB
    is unreachable. Uses its OWN short-lived connection so a read error (e.g. the
    table not created yet) can never poison a caller's open transaction.
    """
    try:
        from browser_automation.etere_direct_client import connect
        with connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT name, code_re, label, config FROM chat.show_profiles "
                "WHERE enabled = 1 ORDER BY sort_order, id"
            )
            rows = cur.fetchall()
        if rows:
            out = []
            for name, code_re, label, cfg in rows:
                p = json.loads(cfg) if cfg else {}
                p["name"] = name
                if code_re:
                    p["code_re"] = code_re
                if label:
                    p["label"] = label
                out.append(p)
            return out
    except Exception:
        pass  # table absent / DB down → use the built-in defaults
    return default_profiles()


def profile_for(file_code, label=None):
    """Return the setup profile matching this show, or None.

    A profile matches by either `code_re` (regex on the file's COD_PROGRA, e.g.
    Korean News) OR `label` (the program's grid kind tag, e.g. "Children"). First
    match wins (ordered by sort_order). `label` is optional so existing callers
    that pass only a file code keep working (code_re profiles still match).
    """
    code = (file_code or "").strip()
    lab = (label or "").strip().lower()
    for p in load_profiles():
        cre = p.get("code_re")
        if cre and code and re.match(cre, code, re.IGNORECASE):
            return p
        plabel = p.get("label")
        if plabel and lab and plabel.strip().lower() == lab:
            return p
    return None


def elements_for(profile, cod_user):
    """Elements from a profile that apply to this market (COD_USER).

    Each element's `markets` is `"all"`, `"ota"`, or a list of market codes
    (e.g. ["SFO","CVC"]). Returns the elements whose scope includes cod_user.
    """
    out = []
    for el in (profile or {}).get("elements", []):
        mk = el.get("markets", "all")
        if mk == "all":
            cus = set(MARKET_CODE_TO_CU.values())
        elif mk == "ota":
            cus = {MARKET_CODE_TO_CU[c] for c in OTA_MARKETS}
        elif mk == "ctv":
            cus = {MARKET_CODE_TO_CU[c] for c in CTV_MARKETS}
        else:
            cus = {MARKET_CODE_TO_CU[c] for c in mk if c in MARKET_CODE_TO_CU}
        if cod_user in cus:
            out.append(el)
    return out


def daily_elements(cod_user):
    """Standing elements that place once per broadcast day for this market
    (COD_USER), independent of any show being set up — from profiles flagged
    `daily` in their config (e.g. the DAL end-of-day FCC children's-records ID).
    Show matching (profile_for) never returns these: they carry no code_re/label."""
    out = []
    for p in load_profiles():
        if p.get("daily"):
            out.extend(elements_for(p, cod_user))
    return out
