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

Model: Sonnet 5 (built-in high-res vision → reads both text and scanned PDFs
natively, no OCR path needed). Structured output via messages.parse() forces the
response to match the Pydantic schema below. Sonnet 5 matches Opus 4.8's
extraction on these orders (verified byte-identical on a WorldLink scan) at a
fraction of the latency and cost, and — with a hard timeout — never hangs the way
a synchronous no-timeout Opus call could under a loaded API. Override with
AI_PARSER_MODEL if a specific order needs Opus.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from pydantic import BaseModel, Field

MODEL = os.getenv("AI_PARSER_MODEL", "claude-sonnet-5")
TIMEOUT_S = 180  # hard ceiling so a slow/loaded API fails fast instead of hanging

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
    is_billboard: bool = Field(default=False, description="True if this line is a BILLBOARD — a short sponsor spot placed at the top of the break. Set it when the length/type says 'Billboard' or 'BB' (e.g. a length column reading '10 seconds Billboard', or ':10 BB'). Applies to paid AND bonus billboard lines. Default False.")
    kind: str = Field(default="airtime", description="Line kind: 'airtime' for any TV spot line (paid OR bonus, has a spot count); 'web' for a web/banner/digital/online placement (e.g. 'Top Banner 880x120'); 'production' for a production / creative-service line item. Default 'airtime'. Web and production lines have no TV spot count — set total_spots=0 for them.")
    start_date: str = Field(description="Flight start date for this line as MM/DD/YYYY.")
    end_date: str = Field(description="Flight end date for this line as MM/DD/YYYY.")


class AIOrder(BaseModel):
    client: str = Field(description="The advertiser / client the campaign is for.")
    agency: str = Field(description="The media buying agency placing the order. Empty string if the order is placed directly by the advertiser.")
    markets: list[str] = Field(description="Distinct market codes appearing in the order.")
    flight_start: str = Field(description="Earliest flight start across all lines, MM/DD/YYYY.")
    flight_end: str = Field(description="Latest flight end across all lines, MM/DD/YYYY.")
    rates_are_net: bool = Field(description="True if the rate column is labeled Net; False if Gross (the default).")
    lines: list[AILine] = Field(description="Every line item in the order grid — paid airtime, bonus airtime, AND any web/banner or production/service rows (tag each with `kind`). Do not omit web or production rows; tag them so the app can decide how to handle them.")
    stated_total_spots: int = Field(default=0, description="The order's OWN stated grand-total spot count — the 'Total Spot #' subtotal / airtime-summary total exactly as printed (e.g. 195). This counts airtime spots only (web/banner rows show n/a and are NOT included in it). 0 if the order states no such total.")
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
- BILLBOARD: set is_billboard=true when the line's length/spot-type indicates a billboard — e.g. a length column reading "10 seconds Billboard", or a type of "BB"/"Billboard". Billboards air at the top of the break. This applies to paid and bonus billboard lines alike; duration is still the plain seconds (a ":10 Billboard" has duration 10).
- DATES: MM/DD/YYYY. Use the start/end dates shown for each line; if a single flight range applies to all lines, use it for every line.
- Distinguish the ADVERTISER (who the campaign is for) from the AGENCY (the buyer placing it). They are often different.
- LINE KINDS: tag every grid line-item with `kind`. A TV spot line (paid or bonus) = 'airtime'. A web/banner/digital/online placement (e.g. "Top Banner 880x120") = 'web'. A production or creative-service line = 'production'. Include web/production rows in `lines` (tagged) — do NOT drop them — but set their total_spots to 0 since they carry no TV spot count.
- STATED TOTAL: read the order's own printed grand-total spot count (the "Total Spot #" subtotal or airtime-summary total, e.g. 195) into stated_total_spots. This is airtime spots only (web shows n/a). 0 if not stated.
- Do NOT invent lines. Do NOT include pure summary/subtotal/total ROWS (e.g. the "Subtotal" or "Airtime Summary" band) as lines — those are totals, not line items. If a value is unclear, extract your best reading AND add a specific note to warnings.
- Reconcile: the airtime line spot counts (paid + bonus) should sum to stated_total_spots, and paid line costs (rate × total_spots) should sum to the order's stated paid total. If they don't, say so in warnings."""

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

    # resolves ANTHROPIC_API_KEY from env; timeout is a hard ceiling so a
    # slow/loaded API surfaces an error instead of hanging silently.
    client = anthropic.Anthropic(timeout=TIMEOUT_S)
    resp = client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        thinking={"type": "disabled"},  # PDF-to-structured-data — no reasoning needed
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
        # rough Sonnet 5 cost: $3/1M in, $15/1M out (the default model)
        "est_cost_usd": round(resp.usage.input_tokens * 3e-6 + resp.usage.output_tokens * 15e-6, 4),
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
