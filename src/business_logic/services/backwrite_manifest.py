"""Backwrite manifest writer — Phase 0 of tasks/backwrite-pipeline.md.

When an order is entered successfully, everything the backwrite will later need
is in hand RIGHT NOW: the parsed IO (line structure as the agency wrote it,
rates_are_net), the gathered user inputs, and the created contract codes with
their Etere IDs. This module freezes that knowledge into a JSON sidecar so the
backwrite step never has to re-ask a human.

The manifest is written to  <incoming>/Entered/<io-filename>.manifest.json
(the IO file itself stays put in Phase 0; Phase 1 moves the pair together and
adds the "Awaiting Backwrite" UI).

Principles (from the spec):
  * The manifest stores lines AS THE PARSER SAW THEM ON THE IO — the backwrite
    Excel mimics the IO's line structure, never Etere's internal line splits.
  * A manifest failure must NEVER fail (or even slow) the entry itself — the
    caller wraps every write in try/except; this module also degrades per-field.
  * An IO that no longer parses still gets a manifest (io_parse_error=true) so
    the awaiting-backwrite queue can show the order loudly instead of losing it.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from datetime import datetime
from pathlib import Path

MANIFEST_VERSION = 1
ENTERED_DIRNAME = "Entered"


def manifest_path_for(io_path: Path) -> Path:
    """Where the manifest for this IO file lives."""
    return io_path.parent / ENTERED_DIRNAME / f"{io_path.name}.manifest.json"


def _jsonable(obj):
    """json.dumps default: dataclasses become dicts, everything else a string."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return str(obj)


def _parse_io_detail(io_path: Path, order_type_value: str) -> dict:
    """Normalized IO detail via the same parser bridge the web UI uses.

    Returns {"error": ...} instead of raising — the manifest still gets written
    so the order isn't lost from the awaiting-backwrite queue.
    """
    try:
        from web.parser_bridge import get_order_detail
        return get_order_detail(io_path, str(order_type_value))
    except Exception as exc:  # noqa: BLE001 - manifest must not break entry
        return {"error": f"IO detail parse failed: {exc}"}


def write_backwrite_manifest(orders: list, result) -> Path | None:
    """Write the manifest for one successful ProcessingResult.

    `orders` is the list of Order entities that produced `result` — one entry
    normally, several for a TCAA-style multi-estimate group (all sharing one
    PDF). Returns the manifest path, or None if there was nothing to write.
    """
    if not orders or not getattr(result, "success", False):
        return None

    io_path = Path(orders[0].pdf_path)
    otype = getattr(result.order_type, "value", str(result.order_type))
    detail = _parse_io_detail(io_path, otype)
    # Some parsers swallow errors and return an empty order instead of raising
    # (e.g. WorldLink) — an IO with no lines at all is a failed parse too.
    parse_failed = bool(detail.get("error")) or not (detail.get("lines") or detail.get("sub_orders"))

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "io_filename": io_path.name,
        "io_path": str(io_path),
        "order_type": otype,
        "entered_at": datetime.now().isoformat(timespec="seconds"),
        "customer_name": orders[0].customer_name,
        "estimates": [o.estimate_number for o in orders if o.estimate_number],
        "contracts": [
            {
                "code": str(c.contract_number),
                "etere_id": c.etere_id,
                "market": c.market,
                "highest_line": c.highest_line,
            }
            for c in result.contracts
        ],
        # Gathered dicts pass through verbatim; OrderInput dataclasses are
        # converted by the json default (_jsonable).
        "user_inputs": [o.order_input for o in orders],
        "rates_are_net": bool(detail.get("rates_are_net", False)),
        "io_parse_error": parse_failed,
        # The IO's own line structure — what the backwrite Excel must mimic.
        "io_detail": detail,
    }

    out = manifest_path_for(io_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, default=_jsonable), encoding="utf-8")
    print(f"[manifest] wrote backwrite manifest: {out}")
    _move_io_to_entered(io_path)
    return out


def _move_io_to_entered(io_path: Path) -> bool:
    """Move the entered IO next to its manifest — the 'Awaiting Backwrite'
    queue state (spec Phase 1). Re-entering the same filename (a corrected
    run) replaces the earlier copy, matching the manifest overwrite.

    Best-effort: on Windows the PDF is often still open in a viewer, so a
    locked file is left in place with a note — the orders API sweeps such
    strays into Entered/ on the next queue load."""
    try:
        dest = io_path.parent / ENTERED_DIRNAME / io_path.name
        if not io_path.exists():
            return False
        if dest.exists():
            dest.unlink()
        shutil.move(str(io_path), str(dest))
        print(f"[manifest] moved entered IO to {dest}")
        return True
    except OSError as exc:
        print(f"[manifest] NOTE: IO stays in incoming for now ({exc}) — "
              f"it will be swept into Entered/ on the next queue load")
        return False


def write_backwrite_manifests(order_groups: list[list], results: list) -> None:
    """Best-effort batch write — one manifest per successful (group, result) pair.

    order_groups[i] must correspond to results[i] (the processing service builds
    them in lock-step). Never raises: entry success must not depend on this.
    """
    for orders, result in zip(order_groups, results):
        if not (result and getattr(result, "success", False) and orders):
            continue
        try:
            write_backwrite_manifest(orders, result)
        except Exception as exc:  # noqa: BLE001 - manifest must not break entry
            name = Path(orders[0].pdf_path).name if orders else "?"
            print(f"[manifest] WARNING: could not write backwrite manifest for {name}: {exc}")
