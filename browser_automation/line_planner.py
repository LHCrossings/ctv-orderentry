"""
Shared late-start line planner for direct-DB agency automations.

Extracted from crispin_automation (order 212735 lesson): a late IO does not
reduce a week's spot count — it compresses those spots into fewer days, so the
truncated first week needs a HIGHER max-per-day than the full weeks behind it,
and when the two disagree the short week gets its own Etere line at its own cap.

`add_contract_line`'s auto-calculation cannot see this: it divides by the day
PATTERN's width (M-F → 5) with no idea the line actually opens on a Wednesday.
Every automation whose source gives weekly spot columns should plan its ranges
through `plan_ranges` and drive both the gather preview and the entry loop from
the SAME plan, so what the user approves is exactly what gets written.

Used by: crispin_automation, ntooitive_automation.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

# Python weekday() 0=Mon … 6=Sun → the Italian keys parse_day_bits returns.
WEEKDAY_KEYS = ('lun', 'mar', 'mer', 'gio', 'ven', 'sab', 'dom')


def fmt_mon_dd(d: date) -> str:
    return f"{MONTH_ABBR[d.month - 1]} {d.day}"


def fmt_mmddyyyy(d: date) -> str:
    return d.strftime('%m/%d/%Y')


def parse_date(s) -> date:
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d', '%m/%d', '%b %d'):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


def active_days(start: date, end: date, day_bits: dict) -> int:
    """How many of the line's own days fall in [start, end]."""
    n, d = 0, start
    while d <= end:
        if day_bits.get(WEEKDAY_KEYS[d.weekday()]):
            n += 1
        d += timedelta(days=1)
    return n


def plan_ranges(consolidated: list[dict], days: str,
                start_from: date, line_end: date) -> tuple[list[dict], list[str]]:
    """Apply a late start date to a line's consolidated week ranges.

    Returns (ranges, notes) where each range carries an explicit `max_daily`,
    and `notes` records any spots the new start date makes undeliverable.
    A truncated first week whose cap differs from the full weeks is split into
    its own range (→ its own Etere line).
    """
    from math import ceil

    from browser_automation.etere_direct_client import parse_day_bits

    day_bits = parse_day_bits(days)
    full_active = sum(1 for v in day_bits.values() if v) or 7

    out: list[dict] = []
    notes: list[str] = []

    def add(d_from: date, d_to: date, spw: int, weeks: int, cap: int, tag: str = "") -> None:
        out.append({'date_from': d_from, 'date_to': d_to, 'spots_per_week': spw,
                    'weeks': weeks, 'max_daily': cap, 'tag': tag})

    for rng in consolidated:
        w0 = parse_date(rng['start_date'])
        r_end = min(parse_date(rng['end_date']), line_end)
        spw, weeks = rng['spots_per_week'], rng['weeks']
        mdr_full = max(1, ceil(spw / full_active))

        if r_end < start_from:
            notes.append(f"{spw * weeks} spot(s) dropped: the week(s) of "
                         f"{w0.month}/{w0.day} end before the new start")
            continue

        # Whole weeks that now sit before the start date cannot be delivered.
        d0 = max(w0, start_from)
        skipped = (d0 - w0).days // 7
        if skipped:
            notes.append(f"{spw * skipped} spot(s) dropped: {skipped} whole week(s) "
                         f"from {w0.month}/{w0.day} now precede the start date")
            w0 += timedelta(days=7 * skipped)
            weeks -= skipped

        if d0 <= w0:                                  # full first week — as before
            add(d0, r_end, spw, weeks, mdr_full)
            continue

        # Truncated first week.
        first_end = min(w0 + timedelta(days=6), r_end)
        p_active = active_days(d0, first_end, day_bits)
        if p_active == 0:
            notes.append(f"{spw} spot(s) dropped: no {days} day left in the week of "
                         f"{w0.month}/{w0.day} on or after {d0.month}/{d0.day}")
            if weeks > 1:
                add(w0 + timedelta(days=7), r_end, spw, weeks - 1, mdr_full)
            continue

        mdr_partial = max(1, ceil(spw / p_active))
        if weeks == 1:
            add(d0, r_end, spw, 1, mdr_partial,
                f"short week: {p_active} day(s), {mdr_partial}/day")
        elif mdr_partial == mdr_full:
            add(d0, r_end, spw, weeks, mdr_full)
        else:
            # The short week earns its own line at its own cap.
            add(d0, first_end, spw, 1, mdr_partial,
                f"short week: {p_active} of {full_active} day(s) → {mdr_partial}/day")
            add(w0 + timedelta(days=7), r_end, spw, weeks - 1, mdr_full)

    return out, notes


class WeekCol:
    """Week-column shim for `EtereClient.consolidate_weeks`.

    The helper's plain-string branch ("Aug 10") takes the year from flight_end,
    which silently misdates a flight that crosses New Year. Parsers that hand us
    real `date` objects should always feed the `.start_date` branch instead.
    """
    __slots__ = ('start_date',)

    def __init__(self, d: date) -> None:
        self.start_date = fmt_mmddyyyy(d)


def mon_of_week_with_first(year: int, month: int) -> date:
    """Monday of the broadcast week that contains the 1st of (year, month)."""
    first = date(year, month, 1)
    return first - timedelta(days=first.weekday())


def broadcast_yymm(d: date) -> str:
    """Broadcast-month code 'YYMM' for a date (weeks Mon–Sun; month begins on the
    Monday of the week containing the 1st). E.g. 7/27/2026 → '2608'."""
    for delta in (-1, 0, 1):
        m = d.month + delta
        y = d.year
        if m > 12:
            m -= 12
            y += 1
        elif m < 1:
            m += 12
            y -= 1
        start = mon_of_week_with_first(y, m)
        nm, ny = (m + 1, y) if m < 12 else (1, y + 1)
        nstart = mon_of_week_with_first(ny, nm)
        if start <= d < nstart:
            return f"{y % 100:02d}{m:02d}"
    return f"{d.year % 100:02d}{d.month:02d}"


def confirm_start_date(flight_start, flight_end=None,
                       always_ask: bool = False):
    """Ask what date the order should actually start.

    Fires whenever the IO is late (starts tomorrow or earlier), or always when
    `always_ask` is set (for order types the team habitually enters after the
    flight opens). Re-prompts until the answer parses and lands inside the
    flight — the value feeds every line's date arithmetic and the max-per-day
    recalculation, so a bad answer must fail here rather than mid-entry.

    Returns the chosen date, or None when flight_start is missing.
    """
    if not flight_start:
        return None
    earliest = parse_date(flight_start)
    is_late = earliest <= date.today() + timedelta(days=1)
    if not is_late and not always_ask:
        return earliest
    latest = parse_date(flight_end) if flight_end else None

    def _f(d: date) -> str:
        return f"{d.month}/{d.day}/{d.strftime('%y')}"

    if is_late:
        print(f"\n  ⚠ This order starts {_f(earliest)} — today is {_f(date.today())}, "
              f"so the IO is late.")
    else:
        print(f"\n  This order's flight starts {_f(earliest)}.")
    print("    A later start compresses each week's spots into fewer days, so the "
          "first")
    print("    partial week may need a higher max-per-day (its own Etere line).")
    while True:
        raw = input(f"  What date should this order start? [{_f(earliest)}]: ").strip()
        if not raw or raw.lower() in ('y', 'yes'):
            return earliest
        try:
            chosen = parse_date(raw)
        except ValueError:
            print(f"    ✗ Could not read '{raw}' — use M/D/YY or MM/DD/YYYY.")
            continue
        if chosen < earliest:
            print(f"    ✗ {_f(chosen)} is before the IO's start {_f(earliest)}.")
            continue
        if latest and chosen > latest:
            print(f"    ✗ {_f(chosen)} is after the flight ends {_f(latest)}.")
            continue
        return chosen


def verify_production_charge(cursor, line_id: int, expected: float,
                             label: str = "order") -> None:
    """Confirm the SP really turned @production into a CONTRATTISPESE row.

    `web_sales_InsertContractLine` is encrypted, so "the Production box writes a
    charge" is an observation about historical rows, not a contract we control.
    If a future Etere build stops honouring the parameter, the money would
    silently vanish and the contract would enter as airtime-only. Checking
    inside the transaction turns that into a rollback instead.
    """
    cursor.execute(
        """
        SELECT ISNULL(SUM(IMPORTO), 0)
        FROM CONTRATTISPESE
        WHERE ID_CONTRATTIRIGHE = %s AND DESCRIZIONE = 'Production'
        """,
        (int(line_id),),
    )
    row = cursor.fetchone()
    got = float(row[0]) if row else 0.0
    if abs(got - expected) > 0.01:
        raise RuntimeError(
            f"{label}: line {line_id} Production box was ${expected:,.2f} but "
            f"CONTRATTISPESE holds ${got:,.2f}. Etere did not record the production "
            f"charge — rolling back rather than entering the order without it."
        )
