"""Fight the Bite media partnership — direct DB automation."""

from __future__ import annotations

import sqlite3
import traceback
from datetime import date
from pathlib import Path
from typing import Optional

from browser_automation.etere_client import EtereClient
from browser_automation.parsers.fightthebite_parser import FTBOrder, parse_fightthebite_file
from src.domain.enums import OrderType, SeparationInterval

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

FTB_MARKET      = "CVC"
FTB_SEPARATION  = SeparationInterval.FIGHTTHEBITE.value   # (15, 0, 0)
CUSTOMER_DB_PATH = Path(__file__).parent.parent / "data" / "customers.db"

_MONTH_NAMES: dict[str, int] = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date(date_str: str, year: int) -> date:
    """Parse 'Jun 8' (week_start_dates) or 'MM/DD/YYYY' (consolidate_weeks output)."""
    from datetime import datetime as _dt
    s = date_str.strip()
    if "/" in s:
        return _dt.strptime(s, "%m/%d/%Y").date()
    parts = s.split()
    if len(parts) != 2:
        raise ValueError(f"Unexpected date string: {date_str!r}")
    return date(year, _MONTH_NAMES[parts[0]], int(parts[1]))


def _secs_to_duration(seconds: int) -> str:
    """Convert spot duration in seconds to 'HH:MM:SS:FF' string."""
    hh = seconds // 3600
    mm = (seconds % 3600) // 60
    ss = seconds % 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}:00"


def _lookup_customer_id() -> Optional[int]:
    """Check local customers.db for an existing Fight the Bite record."""
    try:
        conn = sqlite3.connect(str(CUSTOMER_DB_PATH))
        row = conn.execute(
            "SELECT customer_id FROM customers "
            "WHERE order_type = ? OR LOWER(customer_name) LIKE ? LIMIT 1",
            ("fightthebite", "%fight%bite%"),
        ).fetchone()
        conn.close()
        return int(row[0]) if row else None
    except Exception:
        return None


def _upsert_customer(customer_id: int, customer_name: str = "Fight the Bite") -> None:
    """Upsert the Fight the Bite record in the local customers DB."""
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer

        repo = CustomerRepository(CUSTOMER_DB_PATH)
        repo.save(Customer(
            customer_id=str(customer_id),
            customer_name=customer_name,
            order_type=OrderType.FIGHTTHEBITE,
            billing_type="client",
            default_market=FTB_MARKET,
            separation_customer=FTB_SEPARATION[0],
            separation_event=FTB_SEPARATION[1],
            separation_order=FTB_SEPARATION[2],
        ))
        print(f"[CUSTOMER DB] ✓ Saved: {customer_name} → ID {customer_id}")
    except Exception as exc:
        print(f"[CUSTOMER DB] ⚠ Save failed (non-fatal): {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Line description builder
# ─────────────────────────────────────────────────────────────────────────────

_TIME_DISPLAY: dict[str, str] = {
    "18:00-20:00": "6p-8p",
    "06:00-23:59": "6a-12m",
    "10:00-13:00": "10a-1p",
}


def _line_desc(ftb_line, adjusted_days: str, is_bonus: bool) -> str:
    if is_bonus:
        return f"{adjusted_days} ROS BNS"
    time_disp = _TIME_DISPLAY.get(ftb_line.time_range, ftb_line.time_range)
    return f"{adjusted_days} {time_disp} {ftb_line.language}"


# ─────────────────────────────────────────────────────────────────────────────
# Contract creation
# ─────────────────────────────────────────────────────────────────────────────

def _create_ftb_contract_direct(order: FTBOrder, inputs: dict) -> Optional[int]:
    """Enter Fight the Bite order directly via DB stored procedures.

    One Etere line per language per consecutive run of equal spot counts.
    Returns contract_id on success, None on failure (rolls back fully).
    """
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id  = inputs["customer_id"]
    separation   = inputs.get("separation", FTB_SEPARATION)
    duration_str = _secs_to_duration(order.duration)
    year         = order.year
    conn = None

    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")   # master market always NYC

        all_lines = list(order.paid_lines) + list(order.bonus_lines)
        # Derive overall flight dates from first paid line week dates
        first_week  = (order.paid_lines or order.bonus_lines)[0].week_start_dates[0]
        flight_start_dt = _parse_date(first_week, year)
        flight_end_dt   = _parse_date(order.flight_end, year)

        contract_id = client.create_contract_header(
            code=inputs["contract_code"],
            description=inputs["description"],
            customer_id=int(customer_id),
            contract_date=flight_start_dt,
            contract_end_date=flight_end_dt,
            contract_type=1,
            billing_type="client",
            note=inputs.get("notes", ""),
            customer_order_ref="",
            allow_rename=True,
        )
        if not contract_id:
            print("[FTB DIRECT] ✗ Failed to create contract header")
            return None
        print(f"[FTB DIRECT] ✓ Contract header ID={contract_id}  code={inputs['contract_code']}")

        line_count = 0

        for ftb_line in all_lines:
            is_bonus     = ftb_line.is_bonus
            booking_code = 10 if is_bonus else 2
            rate         = 0.0 if is_bonus else ftb_line.rate

            adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(
                ftb_line.days, ftb_line.time_range
            )

            segments = EtereClient.consolidate_weeks(
                weekly_spots=ftb_line.weekly_spots,
                week_start_dates=ftb_line.week_start_dates,
                flight_end=order.flight_end,
            )

            for seg in segments:
                spw   = seg["spots_per_week"]
                weeks = seg["weeks"]
                total = spw * weeks

                desc = _line_desc(ftb_line, adjusted_days, is_bonus)
                tag  = "BNS" if is_bonus else "PAID"
                line_count += 1
                print(f"  [LINE {line_count}] {tag} {ftb_line.language:<28} "
                      f"{seg['start_date']} → {seg['end_date']}  "
                      f"{spw}/wk × {weeks} wk = {total} spots")

                client.add_contract_line(
                    market=FTB_MARKET,
                    days=adjusted_days,
                    time_range=ftb_line.time_range,
                    description=desc,
                    rate=rate,
                    total_spots=total,
                    spots_per_week=spw,
                    date_from=_parse_date(seg["start_date"], year),
                    date_to=_parse_date(seg["end_date"], year),
                    duration=duration_str,
                    is_bonus=is_bonus,
                    booking_code=booking_code,
                    separation_intervals=separation,
                )

        conn.commit()
        conn.close()
        print(f"[FTB DIRECT] ✓ {line_count} lines committed.")
        return contract_id

    except Exception as exc:
        print(f"[FTB DIRECT] ✗ {exc}")
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Upfront input gathering
# ─────────────────────────────────────────────────────────────────────────────

def _default_code(order: FTBOrder) -> tuple[str, str]:
    yr2 = str(order.year)[2:]   # "26"
    yr4 = str(order.year)       # "2026"
    first_week = (order.paid_lines or order.bonus_lines)[0].week_start_dates[0]
    mon = _MONTH_NAMES.get(first_week.split()[0], 1)
    mm = f"{mon:02d}"
    return f"Sac Yolo FTB {yr2}{mm}", f"Sac Yolo Mosquito Fight the Bite {yr4}"


def gather_fightthebite_inputs(file_path: str) -> Optional[dict]:
    """Parse the Fight the Bite order file and collect user inputs.

    Returns dict with contract_code, description, customer_id, duration,
    separation, notes, and a cached _order. Returns None if cancelled.
    """
    print("\n" + "=" * 70)
    print("FIGHT THE BITE — INPUT COLLECTION")
    print("=" * 70)

    print("\n[PARSE] Reading file…")
    try:
        order = parse_fightthebite_file(file_path)
    except Exception as exc:
        print(f"[PARSE] ✗ Failed: {exc}")
        traceback.print_exc()
        return None

    if not order:
        print("[PARSE] ✗ No order found in file")
        return None

    paid_spots  = sum(sum(ln.weekly_spots) for ln in order.paid_lines)
    bonus_spots = sum(sum(ln.weekly_spots) for ln in order.bonus_lines)

    print(f"\n  Title:      {order.title}")
    print(f"  Source:     {order.source.upper()}")
    print(f"  Market:     {FTB_MARKET}  (:{order.duration}s default)")
    print(f"  Flight end: {order.flight_end}")
    print()
    for ln in order.paid_lines:
        total = sum(ln.weekly_spots)
        print(f"  PAID  {ln.language:<28} {ln.days:<8} {ln.time_range}  "
              f"{total:>4} spots  ${total * ln.rate:,.2f}")
    for ln in order.bonus_lines:
        total = sum(ln.weekly_spots)
        print(f"  BONUS {ln.language:<28} {ln.days:<8} {ln.time_range}  "
              f"{total:>4} spots")
    print(f"\n  Total: {paid_spots} paid + {bonus_spots} bonus  ${order.total_cost:,.2f}")
    print()

    # ── Spot duration ──────────────────────────────────────────────────────
    raw = input(f"  Spot duration in seconds [{order.duration}]: ").strip()
    try:
        order.duration = int(raw) if raw else order.duration
    except ValueError:
        print(f"  ✗ Invalid — keeping :{order.duration}s")
    print()

    # ── Customer ID ────────────────────────────────────────────────────────
    customer_id = _lookup_customer_id()
    if customer_id is not None:
        print(f"  [CUSTOMER DB] Found: Fight the Bite → ID {customer_id}")
    else:
        raw = input("  Etere customer ID for Fight the Bite: ").strip()
        try:
            customer_id = int(raw)
        except ValueError:
            print("  ✗ Invalid customer ID — aborting")
            return None

    _upsert_customer(customer_id)
    print()

    # ── Contract code & description ────────────────────────────────────────
    default_code, default_desc = _default_code(order)

    raw = input(f"  Contract code [{default_code}]: ").strip()
    contract_code = raw or default_code

    raw = input(f"  Description   [{default_desc}]: ").strip()
    description = raw or default_desc

    # ── Notes ──────────────────────────────────────────────────────────────
    raw = input("  Notes [Enter to skip]: ").strip()
    notes = raw

    print("\n" + "=" * 70)
    print("✓ All inputs gathered — ready for automation")
    print("=" * 70 + "\n")

    return {
        "contract_code": contract_code,
        "description":   description,
        "notes":         notes,
        "customer_id":   customer_id,
        "separation":    FTB_SEPARATION,
        "_order":        order,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main processing function
# ─────────────────────────────────────────────────────────────────────────────

def process_fightthebite_order(
    file_path: str,
    shared_session=None,
    pre_gathered_inputs: Optional[dict] = None,
) -> bool:
    """Process a Fight the Bite order and enter it into Etere via direct DB.

    Args:
        file_path: Path to .xlsm, .xlsx, or .pdf order file
        shared_session: Unused — direct DB only
        pre_gathered_inputs: Dict from gather_fightthebite_inputs()

    Returns:
        True on success, False on failure
    """
    inputs = dict(pre_gathered_inputs) if pre_gathered_inputs else {}

    order: Optional[FTBOrder] = inputs.pop("_order", None)
    if order is None:
        print("[FTB] Re-parsing file…")
        order = parse_fightthebite_file(file_path)
        if not order:
            print("[FTB] ✗ Could not parse order file")
            return False

    return _create_ftb_contract_direct(order, inputs) is not None


# ─────────────────────────────────────────────────────────────────────────────
# Standalone
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: uv run python browser_automation/fightthebite_automation.py <file>")
        sys.exit(1)
    result = gather_fightthebite_inputs(sys.argv[1])
    if result:
        process_fightthebite_order(sys.argv[1], pre_gathered_inputs=result)
    else:
        print("\nInput collection cancelled or failed")
