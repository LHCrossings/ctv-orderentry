"""
Parser bridge — dispatches to each agency's parser and normalizes the result
into a single JSON-safe dict for the order detail API endpoint.

Uses duck-typing with multi-alias getattr fallbacks so we don't need a
custom normalizer for every one of the 21 parsers.
"""

import importlib
import sys
from pathlib import Path

# Ensure both src/ and project root (for browser_automation/) are on the path
_project_root = Path(__file__).parent.parent.parent
_src_path = _project_root / "src"
for p in [str(_src_path), str(_project_root)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# Parser registry: OrderType.value -> (module, function)
# ---------------------------------------------------------------------------

_DISPLAY_NAMES = {
    "ADMERASIA":       "Admerasia",
    "CHARMAINE":       "Charmaine",
    "DART":            "DART",
    "DAVISELEN":       "Davis Elen",
    "GALEFORCE":       "GaleForce Media",
    "HL":              "H&L Partners",
    "HL_BDR":          "H&L BDR",
    "HYPHEN":          "Hyphen (DPR)",
    "IGRAPHIX":        "iGraphix",
    "IMPACT":          "Impact Marketing",
    "IMPRENTA":        "Imprenta",
    "LEXUS":           "Lexus",
    "MISFIT":          "Misfit",
    "OPAD":            "OPAD",
    "POLARIS":         "Polaris",
    "PROSIO":          "Prosio",
    "RPM":             "RPM",
    "SACCOUNTYVOTERS": "Sac County Voters",
    "SAGENT":          "Sagent Marketing",
    "SCWA":            "SCWA",
    "TCAA":            "TCAA",
    "TIMEADVERTISING": "Time Advertising",
    "WORLDLINK":       "WorldLink",
    "XML":             "AAAA XML",
}

_REGISTRY = {
    "HYPHEN":           ("browser_automation.parsers.hyphen_parser",          "parse_hyphen_pdf"),
    "HL":               ("browser_automation.parsers.hl_parser",               "parse_hl_pdf"),
    "HL_BDR":           ("browser_automation.parsers.hl_bdr_parser",           "parse_bdr_pdf"),
    "RPM":              ("browser_automation.parsers.rpm_parser",              "parse_rpm_pdf"),
    "GALEFORCE":        ("browser_automation.parsers.galeforce_parser",        "parse_galeforce_pdf"),
    "TCAA":             ("browser_automation.parsers.tcaa_parser",             "parse_tcaa_pdf"),
    "SAGENT":           ("browser_automation.parsers.sagent_parser",           "parse_sagent_pdf"),
    "MISFIT":           ("browser_automation.parsers.misfit_parser",           "parse_misfit_pdf"),
    "ADMERASIA":        ("browser_automation.parsers.admerasia_parser",        "parse_admerasia_pdf"),
    "CHARMAINE":        ("browser_automation.parsers.charmaine_parser",        "parse_charmaine_pdf"),
    "DAVISELEN":        ("browser_automation.parsers.daviselen_parser",        "parse_daviselen_pdf"),
    "IMPACT":           ("browser_automation.parsers.impact_parser",           "parse_impact_pdf"),
    "IGRAPHIX":         ("browser_automation.parsers.igraphix_parser",         "parse_igraphix_pdf"),
    "IMPRENTA":         ("browser_automation.parsers.imprenta_parser",         "parse_imprenta_file"),
    "LEXUS":            ("browser_automation.parsers.lexus_parser",            "parse_lexus_file"),
    "OPAD":             ("browser_automation.parsers.opad_parser",             "parse_opad_pdf"),
    "SACCOUNTYVOTERS":  ("browser_automation.parsers.saccountyvoters_parser",  "parse_saccountyvoters_pdf"),
    "SCWA":             ("browser_automation.parsers.scwa_parser",             "parse_scwa_pdf"),
    "TIMEADVERTISING":  ("browser_automation.parsers.timeadvertising_parser",  "parse_timeadvertising_pdf"),
    "WORLDLINK":        ("browser_automation.parsers.worldlink_parser",        "parse_worldlink_pdf"),
    "XML":              ("browser_automation.parsers.aaaa_xml_parser",         "parse_aaaa_xml"),
    "PROSIO":           ("browser_automation.parsers.prosio_parser",           "parse_prosio_excel"),
    "DART":             ("browser_automation.parsers.dart_parser",             "parse_dart_xlsx"),
    "POLARIS":          ("browser_automation.parsers.polaris_parser",          "parse_polaris_xlsx"),
}

_MISSING = object()

# Import ROS schedules for bonus line overrides
try:
    from browser_automation.ros_definitions import ROS_SCHEDULES as _ROS_SCHEDULES
except Exception:
    _ROS_SCHEDULES = {}


def _apply_ros_overrides(lines: list[dict]) -> list[dict]:
    """For bonus lines, replace days/time with the standard ROS schedule."""
    result = []
    for ln in lines:
        if ln.get("is_bonus"):
            # Language is the first word of the description (e.g. "Hmong BONUS" → "Hmong")
            lang = (ln.get("description") or "").split()[0].title()
            ros = _ROS_SCHEDULES.get(lang)
            if ros:
                ln = dict(ln, days=ros["days"], time=ros["time"])
        result.append(ln)
    return result


def _get(obj, *attrs, default=None):
    """Return the first non-None attribute found on obj from the given names."""
    for attr in attrs:
        val = getattr(obj, attr, _MISSING)
        if val is not _MISSING and val is not None:
            return val
    return default


def _str(val, default="") -> str:
    if val is None:
        return default
    return str(val).strip()


def _float(val, default=0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _int(val, default=0) -> int:
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Line normalizer
# ---------------------------------------------------------------------------

def _normalize_line(line, idx: int) -> dict:
    description = _str(_get(
        line, "description", "program", "daypart", "daypart_code",
        "program_name", "line_description", "language_block", "language"
    ), default=f"Line {idx + 1}")

    # If the parser has both a language name and a daypart, combine them
    # (e.g. Charmaine: language="Filipino", daypart="M-Sun 8p-9p")
    lang_val    = _str(_get(line, "language", "language_block"))
    daypart_val = _str(_get(line, "daypart", "daypart_code"))
    if lang_val and daypart_val and description == daypart_val and lang_val != daypart_val:
        # Only combine if daypart looks like a real schedule (has a time marker)
        import re as _re2
        if _re2.search(r'\d+[ap]|\d:\d{2}', daypart_val, _re2.IGNORECASE):
            description = f"{lang_val} — {daypart_val}"
        else:
            description = lang_val  # garbled OCR — just show language name

    days = _str(_get(line, "days", "day_pattern", "day_string", "day_code", "fix_ros"))
    time = _str(_get(line, "time_str", "time", "time_period", "time_range", "time_slot"))

    # Charmaine: split combined daypart string into days + time when not separately stored
    # e.g. "M-Sun 8p-9p" → days="M-Sun", time="8p-9p"
    if not days and not time and daypart_val and not getattr(line, "is_bonus", False):
        import re as _re
        _dp_match = _re.match(
            r'^((?:M(?:on)?[-–]?(?:Sun?|Sat?|F(?:ri)?|Tu?|W(?:ed)?|Th?)?|'
            r'Sa(?:t)?[-–]?Su(?:n)?|Su(?:n)?|Mon?|Fri?)\S*)\s+(.+)$',
            daypart_val.strip(), _re.IGNORECASE
        )
        if _dp_match:
            days = _dp_match.group(1).strip()
            time = _dp_match.group(2).strip()
    duration = _str(_get(line, "duration", "spot_length", "length", "spot_duration", "duration_seconds"))

    # Weekly spots — may be a list or a scalar
    ws_raw = _get(line, "weekly_spots", "spots_per_week")
    if isinstance(ws_raw, (list, tuple)):
        weekly_spots = [_int(x) for x in ws_raw if x is not None]
    elif ws_raw is not None:
        weekly_spots = [_int(ws_raw)]
    else:
        weekly_spots = []

    total_spots = _int(_get(line, "total_spots", "spots", "num_spots"))
    if total_spots == 0 and weekly_spots:
        total_spots = sum(weekly_spots)

    rate = _float(_get(line, "rate", "gross_rate", "net_rate", "rate_per_spot", "cost"))
    is_bonus = bool(_get(line, "is_bonus", "bonus", default=False))
    market = _str(_get(line, "market", "market_code"))
    language = _str(_get(line, "language", "language_code"))
    start_date = _str(_get(line, "start_date", "flight_start", "start"))
    end_date   = _str(_get(line, "end_date",   "flight_end",   "end"))

    return {
        "description": description,
        "days": days,
        "time": time,
        "duration": duration,
        "weekly_spots": weekly_spots,
        "total_spots": total_spots,
        "rate": rate,
        "is_bonus": is_bonus,
        "market": market,
        "language": language,
        "start_date": start_date,
        "end_date": end_date,
    }


# ---------------------------------------------------------------------------
# Order header normalizer
# ---------------------------------------------------------------------------

def _normalize_order(order_obj) -> dict:
    """Extract common header fields from any parser result object."""
    client = _str(_get(
        order_obj, "client", "advertiser", "client_name", "agency_name",
        "customer_name", "company"
    ))
    estimate = _str(_get(
        order_obj, "estimate_number", "estimate", "order_number",
        "io_number", "order_id", "estimate_id"
    ))
    description = _str(_get(
        order_obj, "description", "campaign", "product", "campaign_name",
        "order_description", "title"
    ))
    market_raw = _get(order_obj, "market", "markets", "market_code")
    if isinstance(market_raw, (list, tuple)):
        markets = [_str(m) for m in market_raw if m]
    elif market_raw:
        markets = [_str(market_raw)]
    else:
        markets = []

    flight_start = _str(_get(order_obj, "flight_start", "flight_begin", "start"))
    flight_end   = _str(_get(order_obj, "flight_end",   "flight_stop",  "end"))

    # Some parsers (SCWA) put dates on lines rather than the order header
    if not flight_start or not flight_end:
        lines_for_dates = _get(order_obj, "lines", "line_items", "entries") or []
        starts = [_str(_get(ln, "start_date", "flight_start")) for ln in lines_for_dates]
        ends   = [_str(_get(ln, "end_date",   "flight_end"))   for ln in lines_for_dates]
        starts = [s for s in starts if s]
        ends   = [e for e in ends   if e]
        if starts and not flight_start:
            flight_start = sorted(starts)[0]
        if ends and not flight_end:
            flight_end = sorted(ends)[-1]
    buyer        = _str(_get(order_obj, "buyer", "contact", "buyer_name", "rep"))
    total_spots  = _int(_get(order_obj, "total_spots", "spots_total"))
    total_cost   = _float(_get(order_obj, "total_cost", "gross_cost", "net_cost", "cost_total"))

    # Lines
    lines_raw = _get(order_obj, "lines", "line_items", "spots", "entries")
    if lines_raw is None:
        lines_raw = []
    normalized_lines = [_normalize_line(ln, i) for i, ln in enumerate(lines_raw)]

    # Recalculate totals from lines if header fields are zero
    if total_spots == 0 and normalized_lines:
        total_spots = sum(ln["total_spots"] for ln in normalized_lines)
    if total_cost == 0.0 and normalized_lines:
        total_cost = sum(
            ln["rate"] * ln["total_spots"]
            for ln in normalized_lines
            if not ln["is_bonus"]
        )

    # Warnings
    warnings = []
    if getattr(order_obj, "rates_are_net", False):
        warnings.append("Rates in this PDF are NET — gross-up required before entry.")
    if getattr(order_obj, "rate_missing", False):
        warnings.append("One or more lines have a missing rate.")

    return {
        "client": client,
        "estimate_number": estimate,
        "description": description,
        "markets": markets,
        "flight_start": flight_start,
        "flight_end": flight_end,
        "buyer": buyer,
        "total_spots": total_spots,
        "total_cost": round(total_cost, 2),
        "lines": normalized_lines,
        "warnings": warnings,
        "rates_are_net": getattr(order_obj, "rates_are_net", False),
    }


# ---------------------------------------------------------------------------
# Agency-specific normalizers (where duck-typing isn't enough)
# ---------------------------------------------------------------------------

def _normalize_admerasia(order) -> dict:
    """Admerasia uses methods rather than plain attributes for key values."""
    # Flight dates
    try:
        start, end = order.get_flight_dates()
        flight_start = start.strftime("%m/%d/%Y") if start else ""
        flight_end   = end.strftime("%m/%d/%Y")   if end   else ""
    except Exception:
        flight_start = flight_end = ""

    # Market — convert DMA name to Etere code
    try:
        market = order.get_market_code()
    except Exception:
        market = _str(_get(order, "markets", default=""))
        if isinstance(market, list):
            market = ", ".join(market)

    # Lines
    lines = []
    for i, ln in enumerate(getattr(order, "lines", []) or []):
        try:
            total = ln.get_total_spots()
        except Exception:
            total = _int(_get(ln, "total_spots", "spots"))

        try:
            rate = float(ln.get_gross_rate())
        except Exception:
            rate = _float(_get(ln, "rate", "gross_rate"))

        try:
            desc = ln.get_description()
        except Exception:
            desc = _str(_get(ln, "description", "program", "days"), default=f"Line {i+1}")

        daily = getattr(ln, "_daily_spots", None) or getattr(ln, "weekly_spots", None) or []

        lines.append({
            "description": desc,
            "days": _str(getattr(ln, "days", "")),
            "time": _str(getattr(ln, "time", "")),
            "duration": _str(getattr(ln, "duration", getattr(ln, "spot_length", ""))),
            "weekly_spots": [int(x) for x in daily if x is not None],
            "total_spots": total,
            "rate": rate,
            "is_bonus": bool(getattr(ln, "is_bonus", False)),
            "market": market,
            "language": _str(getattr(order, "language", "")),
        })

    total_spots = sum(ln["total_spots"] for ln in lines)
    total_cost  = sum(ln["rate"] * ln["total_spots"] for ln in lines if not ln["is_bonus"])

    return {
        "client": "McDonald's",
        "estimate_number": _str(getattr(order, "order_number", "")),
        "description": _str(getattr(order, "language", "")),
        "markets": [market] if market else [],
        "flight_start": flight_start,
        "flight_end": flight_end,
        "buyer": "",
        "total_spots": total_spots,
        "total_cost": round(total_cost, 2),
        "lines": lines,
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_parsers() -> list[dict]:
    """Return display info for every registered parser, sorted by display name."""
    return sorted(
        [{"key": k, "name": _DISPLAY_NAMES.get(k, k)} for k in _REGISTRY],
        key=lambda x: x["name"].lower(),
    )


def get_order_detail(file_path: Path, order_type: str) -> dict:
    """
    Parse the given file and return a normalized detail dict.

    Args:
        file_path: Path to the order file
        order_type: OrderType.value string (e.g. "HYPHEN", "HL")

    Returns:
        dict with keys: client, estimate_number, description, markets,
        flight_start, flight_end, buyer, total_spots, total_cost, lines, warnings
        Plus a top-level "error" key if parsing fails.
    """
    order_type = order_type.upper()
    if order_type not in _REGISTRY:
        return {"error": f"No parser available for order type '{order_type}'."}

    module_name, func_name = _REGISTRY[order_type]
    try:
        module = importlib.import_module(module_name)
        parse_fn = getattr(module, func_name)
    except Exception as e:
        return {"error": f"Could not load parser: {e}"}

    try:
        raw = parse_fn(str(file_path))
    except Exception as e:
        return {"error": f"Parser error: {e}"}

    # Handle parsers that return tuples (RPM returns (order, lines))
    if isinstance(raw, tuple):
        order_obj, lines = raw[0], raw[1] if len(raw) > 1 else []
        if order_obj is None:
            return {"error": "Parser returned no order data."}
        # Attach lines to order_obj if it doesn't already have them
        if not getattr(order_obj, "lines", None) and not getattr(order_obj, "line_items", None):
            object.__setattr__(order_obj, "lines", lines) if hasattr(order_obj, "__slots__") else setattr(order_obj, "lines", lines)
        result = _normalize_order(order_obj)
        if not result["lines"] and lines:
            result["lines"] = [_normalize_line(ln, i) for i, ln in enumerate(lines)]
        result["lines"] = _apply_ros_overrides(result["lines"])
        return result

    # Handle parsers that return a list (HL, TCAA, CHARMAINE, IMPACT, etc.)
    if isinstance(raw, list):
        if not raw:
            return {"error": "Parser returned an empty list."}
        if len(raw) == 1:
            result = _normalize_order(raw[0])
            result["lines"] = _apply_ros_overrides(result["lines"])
            return result
        # Multiple orders in one PDF — return each as a sub_order
        def _norm_with_ros(item):
            r = _normalize_order(item)
            r["lines"] = _apply_ros_overrides(r["lines"])
            return r
        sub_orders = [_norm_with_ros(item) for item in raw]
        # Roll up totals for the top-level summary
        all_warnings = []
        for s in sub_orders:
            all_warnings.extend(s.get("warnings", []))
        return {
            "client": sub_orders[0].get("client", ""),
            "description": sub_orders[0].get("description", ""),
            "markets": list({m for s in sub_orders for m in s.get("markets", [])}),
            "flight_start": sub_orders[0].get("flight_start", ""),
            "flight_end": sub_orders[-1].get("flight_end", "") or sub_orders[0].get("flight_end", ""),
            "buyer": sub_orders[0].get("buyer", ""),
            "total_spots": sum(s.get("total_spots", 0) for s in sub_orders),
            "total_cost": round(sum(s.get("total_cost", 0.0) for s in sub_orders), 2),
            "lines": [],
            "warnings": list(dict.fromkeys(all_warnings)),
            "sub_orders": sub_orders,
        }

    # Single object — agency-specific normalizers where needed
    if order_type == "ADMERASIA":
        result = _normalize_admerasia(raw)
    else:
        result = _normalize_order(raw)
    result["lines"] = _apply_ros_overrides(result["lines"])
    return result
