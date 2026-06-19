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
  * bumpers — {code, event_type}; the engine inserts the bumper if it isn't
    pre-placed and conforms its type/order:
      open  bumper → break 1, first position, EVENT_TYPE 'F' (locked anchor)
      close bumper → after the last segment, EVENT_TYPE 'T' (floats)
    Program segments themselves are EVENT_TYPE 'T'.
"""
from __future__ import annotations

import json
import re

_DEFAULT_PROFILES = [
    {
        "name": "Korean News",
        "code_re": r"^NEWSTODAY\d{6}$",   # NEWSTODAYmmddyy — PRIMARY match
        "networks": ["CTV"],              # CTV markets only (no DAL)
        "days": "M-F",
        "window": ("08:00", "09:00"),
        "open_bumper": {"code": "BUMP_MBCNEWSTODAY_OPEN", "event_type": "F"},
        "close_bumper": {"code": "BUMP_MBCNEWSTODAY_CLOSE", "event_type": "T"},
    },
]


def default_profiles():
    """A fresh copy of the built-in seed/fallback profiles."""
    return [dict(p) for p in _DEFAULT_PROFILES]


def to_config(profile):
    """The JSON `config` payload for a profile row — everything except the
    name/code_re columns (networks, days, window, bumpers, future elements)."""
    return {k: v for k, v in profile.items() if k not in ("name", "code_re")}


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
                "SELECT name, code_re, config FROM chat.show_profiles "
                "WHERE enabled = 1 ORDER BY sort_order, id"
            )
            rows = cur.fetchall()
        if rows:
            out = []
            for name, code_re, cfg in rows:
                p = json.loads(cfg) if cfg else {}
                p["name"] = name
                p["code_re"] = code_re
                out.append(p)
            return out
    except Exception:
        pass  # table absent / DB down → use the built-in defaults
    return default_profiles()


def profile_for(file_code):
    """Return the setup profile whose code_re matches this file code, or None."""
    code = (file_code or "").strip()
    for p in load_profiles():
        if re.match(p["code_re"], code, re.IGNORECASE):
            return p
    return None
