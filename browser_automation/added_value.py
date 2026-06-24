"""
Shared "offer to add Added Value" logic for Hoffman Lewis (H/L) orders.

Hoffman Lewis used to include bonus on every order. CVC/Sacramento still does;
SFO stopped putting bonus on the IO. When an order has NO bonus at all, we offer
to add a single Added Value line per estimate/contract:

  - M-Su, one spot per calendar day across the flight (max 1/day), Rotation
  - Spot type AV (booking code 1, "Added Value"), 'AV' in the description
  - Description lists the languages ordered (full name if one, comma-joined
    abbreviations if several) instead of the time window
  - Airs in the widest daypart window the order actually bought

Bonus is detected the canonical H/L way: a line with rate == 0.0.
"""
from __future__ import annotations

from datetime import date

from browser_automation.parsers.hl_bdr_parser import _BLOCK_PREFIX

# trf_bookingcode: id_bookingcode 1 = code 'AV', description 'Added Value'
SPOT_CODE_AV = 1
# Added Value whitelist priority (from trf_bookingcode.whitelistpriority)
AV_WHITELIST_PRIORITY = 70

_KNOWN_LANGS = set(_BLOCK_PREFIX.keys())


def prompt_add_av(has_bonus: bool) -> bool:
    """
    If the order already has bonus, return False (no prompt). Otherwise ask the
    operator whether to add an Added Value line.
    """
    if has_bonus:
        return False
    print("\n[AV] There are no bonus spots on this order.")
    resp = input("  Would you like to include Added Value? [y/N]: ").strip().lower()
    return resp in ("y", "yes")


def widest_window(times: list[str]) -> str:
    """
    Return "HH:MM-HH:MM" spanning the earliest start to the latest end across the
    given time strings. Falls back to a full broadcast day if none parse.
    """
    from browser_automation.etere_client import EtereClient

    starts: list[str] = []
    ends: list[str] = []
    for t in times:
        try:
            time_from, time_to = EtereClient.parse_time_range(t)
        except Exception:
            continue
        starts.append(time_from)
        ends.append(time_to)
    if not starts:
        return "06:00-23:59"
    return f"{min(starts)}-{max(ends)}"


def format_languages(names: list[str]) -> str:
    """
    Dedupe (order-preserving) the recognized languages and render a display token:
      - one language  → full title-case name (e.g. "Filipino")
      - many          → comma-joined abbreviations (e.g. "M,C,V")
    Returns "" when none of the names are recognized languages.
    """
    seen: list[str] = []
    for n in names:
        key = (n or "").strip().upper()
        if key in _KNOWN_LANGS and key not in seen:
            seen.append(key)
    if not seen:
        return ""
    if len(seen) == 1:
        return seen[0].title()
    return ",".join(_BLOCK_PREFIX[k] for k in seen)


def av_total_spots(date_from: date, date_to: date) -> int:
    """One spot per calendar day across the flight, inclusive of both ends."""
    return (date_to - date_from).days + 1


def add_av_line(
    client,
    *,
    contract_id: int,
    market: str,
    date_from: date,
    date_to: date,
    duration: str,
    separation: tuple[int, int, int],
    languages: list[str],
    fallback_time: str,
) -> int:
    """
    Add one Added Value contract line spanning [date_from, date_to]:
    M-Su, 1 spot/day, Rotation (via is_added_value), spot type AV.

    The description lists the languages ordered (falling back to the time window
    if no language could be determined); the line still airs in `fallback_time`.
    Returns the new line ID (>0 on success).
    """
    lang_token = format_languages(languages) or fallback_time
    description = f"M-Su {lang_token} AV ROS"
    total = av_total_spots(date_from, date_to)
    return client.add_contract_line(
        contract_id=contract_id,
        market=market,
        days="M-Su",
        time_range=fallback_time,
        description=description,
        rate=0.0,
        total_spots=total,
        spots_per_week=7,
        max_daily_run=1,
        date_from=date_from,
        date_to=date_to,
        duration=duration,
        is_added_value=True,
        booking_code=SPOT_CODE_AV,
        whitelist_priority=AV_WHITELIST_PRIORITY,
        separation_intervals=separation,
    )
