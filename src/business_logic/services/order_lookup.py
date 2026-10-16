"""
Order Lookup — plain-English facts about one Etere contract.

The AEs ask the same three questions by email: is the order in, is it scheduled,
and which creatives are on it. `summarize()` turns the header, the lines and
the creative counts the reports route already fetched into the sentences the
AE reads at the top of /reports/order-lookup. Pure function; no DB access.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, Optional

# CTV_LineLanguage codes (browser_automation/line_language.LANGUAGE_CODES) → what the AE calls them.
LANGUAGE_NAMES = {
    "E": "English",
    "C": "Cantonese",
    "M": "Mandarin",
    "M/C": "Chinese (Mandarin & Cantonese)",
    "V": "Vietnamese",
    "T": "Filipino",
    "K": "Korean",
    "J": "Japanese",
    "SA": "South Asian",
    "Hm": "Hmong",
    "P": "Punjabi",
    "H": "Hindi",
    "L": "Multi-language",
}

# Search words → catalog codes. A word matches when it is a prefix (3+ chars) of a
# language name, or one of these aliases.
_LANGUAGE_ALIASES = {
    "chinese": {"M", "C", "M/C"},
    "mandarin": {"M", "M/C"},
    "cantonese": {"C", "M/C"},
    "tagalog": {"T"},
    "hindi": {"H", "SA"},
    "punjabi": {"P", "SA"},
    "indian": {"SA", "H", "P"},
    "multi": {"L"},
}


def language_codes_for(token: str) -> set:
    """Codes a search word refers to ('viet' → {'V'}, 'chinese' → {'M','C','M/C'}); empty if none."""
    t = token.strip().lower()
    if len(t) < 3:
        return set()
    codes = set()
    for alias, cs in _LANGUAGE_ALIASES.items():
        if alias.startswith(t):
            codes |= cs
    for code, name in LANGUAGE_NAMES.items():
        if any(w.startswith(t) for w in name.lower().replace("(", "").replace("&", "").split()):
            codes.add(code)
    return codes


def language_name(code) -> str:
    return LANGUAGE_NAMES.get(code or "", code or "")


def _d(v) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v)[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass
    try:  # mm/dd/yyyy
        m, d, y = s.split("/")
        return date(int(y), int(m), int(d))
    except Exception:
        return None


def fmt_date(v) -> str:
    d = _d(v)
    if d is None:
        return "?"
    return f"{d.month}/{d.day}/{d.strftime('%y')}"


def fmt_day(v) -> str:
    """'Sat 10/10/26' — the weekday helps the AE answer 'live on Tuesday?'."""
    d = _d(v)
    if d is None:
        return "?"
    return f"{d.strftime('%a')} {fmt_date(d)}"


def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return one if n == 1 else (many or one + "s")


def _join(items: Iterable[str], limit: int = 4) -> str:
    items = [i for i in items if i]
    if len(items) > limit:
        return ", ".join(items[:limit]) + f" and {len(items) - limit} more"
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


AIRCHECK_MATCH_MINUTES = 10
_AIRCHECK_UPCOMING = {"scheduled", "pending"}


def match_airchecks(
    spots: list, captures: list, tolerance_minutes: int = AIRCHECK_MATCH_MINUTES
) -> list:
    """Pair the recorder agent's captures with a contract's placed spots.

    spots:    {isci_code, network, capture_start (naive PT ISO), air_datetime_local}
    captures: agent rows {id, isci_code, network, start_time (naive PT ISO), status, ...}
    A capture matches when ISCI and market agree and it starts within
    `tolerance_minutes` of the spot's capture window. Returns one entry per
    matched capture (a spot may match at most one capture), sorted by start.
    """
    by_key: dict = {}
    for sp in spots:
        try:
            t = datetime.fromisoformat(str(sp.get("capture_start"))[:19])
        except (TypeError, ValueError):
            continue
        key = (str(sp.get("isci_code") or "").strip().upper(), str(sp.get("network") or ""))
        by_key.setdefault(key, []).append((t, sp))

    tol = timedelta(minutes=tolerance_minutes)
    out = []
    for cap in captures:
        key = (str(cap.get("isci_code") or "").strip().upper(), str(cap.get("network") or ""))
        try:
            start = datetime.fromisoformat(str(cap.get("start_time"))[:19])
        except (TypeError, ValueError):
            continue
        best = None
        for t, sp in by_key.get(key, ()):
            delta = abs(start - t)
            if delta <= tol and (best is None or delta < best[0]):
                best = (delta, sp)
        if best is None:
            continue
        out.append(
            {
                "id": cap.get("id"),
                "isci": key[0],
                "network": key[1],
                "status": str(cap.get("status") or "").lower(),
                "start_time": start.isoformat(),
                "air_local": best[1].get("air_datetime_local") or "",
            }
        )
    out.sort(key=lambda c: c["start_time"])
    return out


def airchecks_sentence(airchecks: list) -> Optional[str]:
    """One sentence on the contract's air checks, or None when it has none."""
    if not airchecks:
        return None
    upcoming = [c for c in airchecks if c.get("status") in _AIRCHECK_UPCOMING]
    done = [c for c in airchecks if c.get("status") == "complete"]
    failed = [c for c in airchecks if c.get("status") == "error"]
    parts = []
    if upcoming:
        nxt = upcoming[0]
        where = f" (next {nxt['air_local']} on {nxt['network']})" if nxt.get("air_local") else ""
        parts.append(
            f"{len(upcoming)} {_plural(len(upcoming), 'is', 'are')} scheduled to record{where}"
        )
    if done:
        parts.append(f"{len(done)} {_plural(len(done), 'has', 'have')} been recorded")
    if failed:
        parts.append(f"{len(failed)} failed to record")
    if not parts:
        return None
    n = len(airchecks)
    return f"Air checks: {n} {_plural(n, 'capture is', 'captures are')} booked on the recorder; {'; '.join(parts)}."


def summarize(
    hdr: dict,
    lines: list,
    creatives: list,
    today: Optional[date] = None,
    airchecks: Optional[list] = None,
) -> dict:
    """
    hdr:   id, code, description, customer, agency, ae, date_start, date_end,
           entered_on, first_air, last_air, first_unassigned
    lines: description, market, ordered, scheduled, with_creative, aired, is_bonus
    creatives: isci, title, duration_sec, spots, aired

    Returns {"sentences": [...], "scheduled_state": full|partial|none|empty,
             "creative_state": full|partial|none|na, "aired": n}
    """
    today = today or date.today()
    ordered = sum(int(ln.get("ordered") or 0) for ln in lines)
    scheduled = sum(int(ln.get("scheduled") or 0) for ln in lines)
    with_creative = sum(int(ln.get("with_creative") or 0) for ln in lines)
    aired = sum(int(ln.get("aired") or 0) for ln in lines)
    bonus = sum(int(ln.get("ordered") or 0) for ln in lines if ln.get("is_bonus"))
    markets = sorted({ln.get("market") for ln in lines if ln.get("market")})

    code = hdr.get("code") or f"contract {hdr.get('id')}"
    desc = (hdr.get("description") or "").strip()
    who = hdr.get("customer") or "an unnamed client"
    if hdr.get("agency") and hdr.get("agency") != hdr.get("customer"):
        who += f" via {hdr['agency']}"

    sentences: list[str] = []

    # 1. Is it in?
    s = f"Order {code}"
    if desc:
        s += f" ({desc})"
    s += f" for {who} is entered in Etere."
    sentences.append(s)

    flight = ""
    if hdr.get("date_start") or hdr.get("date_end"):
        flight = f"It runs {fmt_date(hdr.get('date_start'))} to {fmt_date(hdr.get('date_end'))}"
        if markets:
            flight += f" in {_join(markets, limit=10)}"
        langs = sorted({language_name(ln.get("language")) for ln in lines if ln.get("language")})
        if langs:
            flight += f" ({_join(langs, limit=6)})"
        if hdr.get("ae"):
            flight += f", sold by {hdr['ae']}"
        flight += "."
        sentences.append(flight)

    # 2. Is it scheduled?
    if ordered == 0:
        sentences.append("No spots are ordered on it yet.")
        scheduled_state = "empty"
    else:
        bonus_note = f" (including {bonus} bonus)" if bonus else ""
        if scheduled >= ordered:
            sentences.append(
                f"All {ordered} ordered {_plural(ordered, 'spot')}{bonus_note} are scheduled on the log."
            )
            scheduled_state = "full"
        elif scheduled == 0:
            sentences.append(
                f"None of the {ordered} ordered {_plural(ordered, 'spot')}{bonus_note} "
                "are scheduled on the log yet."
            )
            scheduled_state = "none"
        else:
            short = [
                ln.get("description") or ""
                for ln in lines
                if int(ln.get("scheduled") or 0) < int(ln.get("ordered") or 0)
            ]
            s = (
                f"{scheduled} of {ordered} ordered spots{bonus_note} are scheduled on the log; "
                f"{ordered - scheduled} {_plural(ordered - scheduled, 'is', 'are')} not placed yet"
            )
            if short:
                s += f" (lines: {_join(short)})"
            sentences.append(s + ".")
            scheduled_state = "partial"

    # 3. Which creatives?
    if scheduled == 0:
        creative_state = "na"
    elif with_creative >= scheduled:
        n = len(creatives)
        if n == 1:
            tail = "the creative is listed below"
        elif n:
            tail = f"the {n} creatives are listed below"
        else:
            tail = "see the list below"
        sentences.append(f"Every scheduled spot has a creative assigned; {tail}.")
        creative_state = "full"
    elif with_creative == 0:
        sentences.append("No creative is assigned to the scheduled spots yet.")
        creative_state = "none"
    else:
        s = (
            f"{with_creative} of {scheduled} scheduled spots have a creative assigned; "
            f"{scheduled - with_creative} still need one"
        )
        if hdr.get("first_unassigned"):
            s += f" (first unassigned spot {fmt_day(hdr['first_unassigned'])})"
        sentences.append(s + ".")
        creative_state = "partial"

    # 4. Has anything aired?
    first_air = _d(hdr.get("first_air"))
    last_air = _d(hdr.get("last_air"))
    if scheduled:
        if aired == 0 and first_air and first_air >= today:
            sentences.append(
                f"Nothing has aired yet; the first spot is scheduled for {fmt_day(first_air)}."
            )
        elif aired == 0:
            sentences.append("Nothing has aired yet.")
        elif last_air and last_air < today:
            sentences.append(
                f"The flight has ended; {aired} of {scheduled} scheduled spots aired "
                f"(last on {fmt_day(last_air)})."
            )
        else:
            s = f"{aired} {_plural(aired, 'spot has', 'spots have')} aired so far"
            if last_air:
                s += f"; the last is scheduled for {fmt_day(last_air)}"
            sentences.append(s + ".")

    # 5. Air checks booked on the recorder?
    ac = airchecks_sentence(airchecks or [])
    if ac:
        sentences.append(ac)

    return {
        "sentences": sentences,
        "airchecks": len(airchecks or []),
        "scheduled_state": scheduled_state,
        "creative_state": creative_state,
        "ordered": ordered,
        "scheduled": scheduled,
        "with_creative": with_creative,
        "aired": aired,
        "markets": markets,
        "languages": sorted(
            {language_name(ln.get("language")) for ln in lines if ln.get("language")}
        ),
    }
