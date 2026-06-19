"""
AI fallback parser (PROTOTYPE — read-only).

When no deterministic parser matches an order, this module asks Claude to read
the PDF and return the SAME normalized line data a hand-written parser would —
which the existing entry engine (EtereDirectClient.add_contract_line) already
knows how to consume. The model never sees Etere internals; it only extracts the
per-line facts (market, language, days, time, duration, spots, rate, paid/bonus,
flight dates) plus header facts.

This is the extraction half only. It does NOT enter anything into Etere — the
intended flow is extract → human review/edit in the web preview → enter. Output
field names match parser_bridge._normalize_line so the existing preview UI can
render an AIOrder with no special-casing.

Model: claude-opus-4-8 (built-in high-res vision → reads both text and scanned
PDFs natively, no OCR path needed). Structured output via messages.parse() forces
the response to match the Pydantic schema below.
"""

from __future__ import annotations

import base64
from pathlib import Path

from pydantic import BaseModel, Field

MODEL = "claude-opus-4-8"

# ─── Output schema (field names mirror parser_bridge._normalize_line) ─────────

class AILine(BaseModel):
    market: str = Field(description="Market code: CVC, SFO, LAX, SEA, HOU, CMP, WDC, NYC, MMT, or DAL. Use 'UNKNOWN' if not determinable.")
    language: str = Field(description="Language / language block, e.g. Chinese, Filipino, Hmong, Vietnamese, Mandarin, Cantonese, Punjabi, Korean, Japanese. Empty string if none.")
    description: str = Field(description="Short human-readable label for the line, e.g. 'Chinese Weekday M-F (7p-12a)'.")
    days: str = Field(description="Day pattern using M T W R(Thu) F Sa Su, e.g. 'M-F', 'Sa-Su', 'M-Su'. For run-of-schedule/ROS bonus with no stated days, use 'M-Su'.")
    time_range: str = Field(description="Air-time window exactly as written, e.g. '7p-12a', '4p-6p', '10a-1p'. Use 'ROS' for run-of-schedule. 12a/12m = midnight (end of broadcast day); 12n/12p = noon.")
    duration: int = Field(description="Spot length in SECONDS, e.g. 30, 15.")
    total_spots: int = Field(description="Total number of spots for this line across the whole flight (the sum of week_spots when a weekly grid is present).")
    spots_per_week: int = Field(description="Spots per week ONLY when the order states a single weekly cadence and gives no per-week grid; otherwise 0. When you fill week_dates/week_spots, set this to 0.")
    week_dates: list[str] = Field(default_factory=list, description="When the order shows a grid of weekly spot counts, the start date (MM/DD/YYYY) of each listed week column, in order. Empty when there is no weekly grid.")
    week_spots: list[int] = Field(default_factory=list, description="Spot count under each week column, same order and length as week_dates. Empty when there is no weekly grid. Do NOT merge non-contiguous weeks — list each week exactly as shown.")
    rate: float = Field(description="Per-spot GROSS rate in dollars. 0 for bonus/BNS/promo/added-value lines.")
    is_bonus: bool = Field(description="True if this is a bonus/BNS/promo/added-value line (rate is 0). A line is paid only if it has a rate > 0.")
    start_date: str = Field(description="Flight start date for this line as MM/DD/YYYY.")
    end_date: str = Field(description="Flight end date for this line as MM/DD/YYYY.")


class AIOrder(BaseModel):
    client: str = Field(description="The advertiser / client the campaign is for.")
    agency: str = Field(description="The media buying agency placing the order. Empty string if the order is placed directly by the advertiser.")
    markets: list[str] = Field(description="Distinct market codes appearing in the order.")
    flight_start: str = Field(description="Earliest flight start across all lines, MM/DD/YYYY.")
    flight_end: str = Field(description="Latest flight end across all lines, MM/DD/YYYY.")
    rates_are_net: bool = Field(description="True if the rate column is labeled Net; False if Gross (the default).")
    lines: list[AILine] = Field(description="Every airtime line in the order, paid and bonus.")
    warnings: list[str] = Field(default_factory=list, description="Anything you were unsure about, ambiguous, or could not read cleanly. Be specific (cite the line).")


# ─── Prompt ───────────────────────────────────────────────────────────────────

_SYSTEM = """You extract television advertising orders for Crossings TV / The Asian Channel into structured data.

Rules:
- MARKET CODES: NYC=New York, CMP=Chicago/Minneapolis, HOU=Houston, SFO=San Francisco, SEA=Seattle, LAX=Los Angeles, CVC=Central Valley/Sacramento/Fresno, WDC=Washington DC, MMT=Multimarket National, DAL=Dallas. Map any market name to its code (e.g. "Sacramento"→CVC). If the order is single-market, every line uses that market.
- PAID vs BONUS: a line is PAID only if it has a rate greater than 0. A line marked BNS / Bonus / Promo / Added Value / ROS with no charge is a bonus line: set is_bonus=true and rate=0.
- DAYS: use single letters M T W R(Thursday) F Sa Su and ranges like M-F, Sa-Su, M-Su. "Weekday" alone usually means M-F; "Weekend" usually means Sa-Su — but always prefer explicitly listed days.
- TIME: copy the air-time window exactly as written (e.g. "7p-12a"). 12a or 12m means midnight / end of broadcast day; 12n or 12p means noon. Use "ROS" for run-of-schedule lines that have no specific time window.
- WEEKLY COLUMNS (important): if the order shows a grid of per-week spot counts (columns headed by week-start dates), populate week_dates (each listed week's start date, MM/DD/YYYY, in order) and week_spots (the count under each of those columns, same order and length). Set total_spots to their sum and spots_per_week=0. List each week EXACTLY as shown — do NOT merge or fill gaps between non-contiguous weeks; the entry system splits non-contiguous runs into separate contract lines on its own.
- If the order gives only a single flight total or a monthly figure with NO weekly grid, leave week_dates and week_spots empty, put the total in total_spots, and set spots_per_week only if a single weekly cadence is explicitly stated.
- DURATION: spot length in seconds (a ":30" or "30 seconds" spot is 30).
- DATES: MM/DD/YYYY. Use the start/end dates shown for each line; if a single flight range applies to all lines, use it for every line.
- Distinguish the ADVERTISER (who the campaign is for) from the AGENCY (the buyer placing it). They are often different.
- Do NOT invent lines. Do NOT include summary/total rows as lines. If a value is unclear, extract your best reading AND add a specific note to warnings.
- Reconcile: the paid line costs (rate × total_spots) should sum to the order's stated paid total. If they don't, say so in warnings."""

_INSTRUCTION = "Extract this advertising order into the required structured schema. Include every paid and bonus airtime line. Flag anything ambiguous in warnings."


# ─── Parser ───────────────────────────────────────────────────────────────────

def parse_ai_pdf(path: str, model: str = MODEL, max_tokens: int = 16000):
    """
    Extract an order PDF into an AIOrder via Claude structured output.

    Returns (AIOrder, usage_dict). Reads ANTHROPIC_API_KEY from the environment
    (the app's .env is loaded by the caller). Raises on missing key / API error.
    """
    import anthropic

    pdf_b64 = base64.standard_b64encode(Path(path).read_bytes()).decode("utf-8")

    client = anthropic.Anthropic()  # resolves ANTHROPIC_API_KEY from env
    resp = client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=_SYSTEM,
        messages=[{
            "role": "user",
            "content": [
                {"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
                {"type": "text", "text": _INSTRUCTION},
            ],
        }],
        output_format=AIOrder,
    )

    usage = {
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        # rough Opus 4.8 cost: $5/1M in, $25/1M out
        "est_cost_usd": round(resp.usage.input_tokens * 5e-6 + resp.usage.output_tokens * 25e-6, 4),
    }
    return resp.parsed_output, usage


# ─── Cached entry point (preview and entry must use the SAME extraction) ──────

def _sidecar_path(path: str) -> str:
    return str(path) + ".ai.json"


def parse_ai_order(path: str, refresh: bool = False) -> AIOrder:
    """
    Cached AI extraction. The web preview and the CLI entry step both call this;
    caching the result in a `<file>.ai.json` sidecar guarantees they see the
    SAME extraction (the model is non-deterministic and entry re-parses the file)
    and avoids paying for a second API call.

    Delete the sidecar (or pass refresh=True) to re-extract.
    """
    import json
    import os

    sidecar = _sidecar_path(path)
    if not refresh and os.path.exists(sidecar):
        data = json.loads(Path(sidecar).read_text())
        return AIOrder.model_validate(data["order"])

    order, usage = parse_ai_pdf(str(path))
    try:
        Path(sidecar).write_text(json.dumps(
            {"order": order.model_dump(), "usage": usage}, indent=2, default=str
        ))
    except Exception as exc:
        print(f"[AI] Warning: could not cache extraction: {exc}")
    return order
