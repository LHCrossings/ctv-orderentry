"""Per-show "setup profiles" for the Set up Daily Programming tool.

Some shows need extra elements layered in when they're set up, beyond the raw
program content — e.g. open/close bumpers for Korean News. A profile describes
how to identify the show and what extra elements it requires; the run engine
(daily_programming_run.run_market) consults it and conforms the schedule to it.

Recorded here as a code registry for now. The PRIMARY match key is the file-code
regex (deterministic and exactly what the operator selects); the network / day /
window fields are guard-rail metadata for later proactive hints and a future
"manage exceptions" UI. To add a show, append a dict — no engine change needed.

Element types:
  * fillers — handled separately (the pieces/fillers path in the modal).
  * bumpers — {code, event_type}; the engine inserts the bumper if it isn't
    pre-placed and conforms its type/order to the rule below:
      open  bumper → break 1, first position, EVENT_TYPE 'F' (locked anchor)
      close bumper → after the last segment, EVENT_TYPE 'T' (floats)
    Program segments themselves are EVENT_TYPE 'T'.
"""
from __future__ import annotations

import re

PROFILES = [
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


def profile_for(file_code: str):
    """Return the setup profile whose code_re matches this file code, or None."""
    code = (file_code or "").strip()
    for p in PROFILES:
        if re.match(p["code_re"], code, re.IGNORECASE):
            return p
    return None
