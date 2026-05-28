# API & Export Contracts

**Audience:** External engineers (SpotOps or other internal tools) consuming data from the Crossings TV Control Room.  
**Purpose:** Canonical contract reference for Control Room API endpoints — request shape, response shape, field semantics, and error behavior.  
**Last reviewed:** 2026-05-28

---

## Endpoints at a glance

| Endpoint | Auth | Purpose | Section |
|---|---|---|---|
| `GET /api/master-control/booked-business/load` | `X-ControlRoom-Token` | Scheduled revenue by AE and client for a broadcast month | [Booked Business](#booked-business) |
| `GET /api/reports/placement-by-week/{contract_id}` | `X-ControlRoom-Token` | Spot placement pivot by week for a single contract | [Placement by Week](#placement-by-week) |
| `GET /api/reports/placement-by-week/{contract_id}/excel` | `X-ControlRoom-Token` | Same as above, returned as `.xlsx` download | [Placement by Week (Excel)](#placement-by-week-excel) |
| `GET /api/reports/as-run` | `X-ControlRoom-Token` | Actual aired spots for a title across markets and date range | [As-Run](#as-run) |

---

## Common conventions

### Authentication

All endpoints require `X-ControlRoom-Token: <secret>` in the request header.  
The server validates against the `CONTROLROOM_EXPORT_TOKEN` environment variable.  
Contact Lee to obtain the token value.

| HTTP | Body | Meaning |
|---|---|---|
| `401` | `{"detail": "Invalid or missing token"}` | Wrong or absent token |
| `503` | `{"detail": "Export token not configured on server"}` | Server-side env var not set (operator fix) |

### Network / host

The Control Room runs on the same Tailscale network. Reach it by tailnet hostname on port `8000` (e.g., `http://crossings-server:8000`). All paths below are relative to that base URL.

### Date formats

- Query parameters accept **ISO format**: `YYYY-MM-DD`
- Response date fields: `YYYY-MM-DD` strings unless noted
- Broadcast month query params: integer `year` + `month` (e.g., `year=2026&month=5`)

### Trade exclusion (system-wide invariant)

Revenue figures from `/booked-business` exclude Trade spots via:
```
NEWTYPE NOT LIKE '%TRD%' AND (CAMBIOMERCE = 0 OR CAMBIOMERCE IS NULL) AND ID_PAGAMENTI != 4
```
This matches the SpotOps convention of `revenue_type != 'Trade'`. Pass `show_trade=true` to override.

---

## Booked Business

### `GET /api/master-control/booked-business/load`

Returns scheduled gross and net revenue grouped by AE and client for a given broadcast month. Revenue is counted from `TPALINSE` (actual scheduled spots) at the contract line rate — one spot = one rate unit. This matches the commercial log exactly.

#### Query parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `year` | integer | Yes | Calendar year (e.g., `2026`) |
| `month` | integer | Yes | Calendar month 1–12 (e.g., `5` for May) |
| `show_trade` | boolean | No (default `false`) | Include Trade revenue in response |

**Broadcast month vs. calendar month:** The server automatically computes the broadcast month window (Monday of the week containing the 1st of the calendar month through the day before the next broadcast month starts). `bcast_bounds` in the response shows the actual date range used.

#### Response shape

```json
{
  "month_label": "May 2026",
  "bcast_bounds": "Apr 27 – May 31, 2026",
  "cal_bounds": "May 1 – May 31, 2026",
  "ae_groups": [
    {
      "ae": "Charmaine Lane",
      "clients": [
        {
          "client": "Pechanga Resort Casino",
          "gross": 4500.00,
          "net": 3825.00,
          "billing": "Broadcast",
          "unset": false
        }
      ],
      "gross": 4500.00,
      "net": 3825.00
    }
  ],
  "grand_gross": 4500.00,
  "grand_net": 3825.00,
  "trade_groups": [],
  "trade_gross": 0.00,
  "trade_net": 0.00
}
```

#### Field notes

| Field | Notes |
|---|---|
| `ae_groups` | One entry per sales AE; sorted by AE name |
| `client.billing` | `"Broadcast"` or `"Calendar"` — which month boundary the contract is billed on; `"—"` if unset |
| `client.unset` | `true` if the contract has no billing type assigned — these need cleanup in Etere |
| `gross` / `net` | Gross = full rate; Net = Gross × 0.85 (agency commission deducted) |
| `trade_groups` | Same structure as `ae_groups`; empty unless `show_trade=true` |

---

## Placement by Week

### `GET /api/reports/placement-by-week/{contract_id}`

Returns a pivot table of scheduled spot counts by contract line description × broadcast week for a single Etere contract. Data comes from `TPALINSE` (actual scheduled spots).

#### Path parameters

| Parameter | Type | Description |
|---|---|---|
| `contract_id` | integer | Etere `ID_CONTRATTITESTATA` — the internal integer contract ID (not the code string) |

#### Response shape

```json
{
  "header": {
    "id": 2779,
    "code": "LEX EST 208 SFO",
    "description": "Lexus EST 208 SFO",
    "date_start": "04/07/2026",
    "date_end": "06/29/2026"
  },
  "weeks": ["Apr 7–Apr 13", "Apr 14–Apr 20"],
  "rows": [
    {
      "description": "South Asian 1p-4p [SA0101]",
      "spots": [4, 4],
      "total": 8,
      "is_bonus": false
    },
    {
      "description": "BNS South Asian 1p-4p [SA0101]",
      "spots": [2, 2],
      "total": 4,
      "is_bonus": true
    }
  ],
  "week_totals": [6, 6],
  "grand_total": 12
}
```

#### Field notes

| Field | Notes |
|---|---|
| `header.date_start` / `date_end` | `MM/DD/YYYY` format — contract flight dates from Etere |
| `weeks` | Broadcast weeks (Mon–Sun), sorted chronologically |
| `rows[].spots` | Parallel array to `weeks` — spot count for that line in that week |
| `rows[].is_bonus` | `true` for bonus (BNS) lines; `false` for paid |
| `week_totals` | Parallel array to `weeks` — sum of all line spots for that week |

---

## Placement by Week (Excel)

### `GET /api/reports/placement-by-week/{contract_id}/excel`

Same data as [Placement by Week](#placement-by-week), returned as a formatted `.xlsx` file download.

#### Response

- **Content-Type:** `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
- **Content-Disposition:** `attachment; filename="{CODE}_PlacementByWeek.xlsx"`
- Body is binary Excel bytes — save directly to disk.

---

## As-Run

### `GET /api/reports/as-run`

Returns actual aired spot records for a given asset title pattern, date range, and set of markets. Queries `TPALINSE` directly — these are spots that actually aired (or are scheduled to air), not contracted spots.

#### Query parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `spot` | string | Yes | Substring match against spot title (LIKE `%value%`) |
| `date_from` | string | Yes | Start date, `YYYY-MM-DD` |
| `date_to` | string | Yes | End date, `YYYY-MM-DD` |
| `markets` | string | Yes | Comma-separated list of market integer IDs (see Market IDs below) |

#### Market IDs

| ID | Code | Market |
|---|---|---|
| 1 | NYC | New York / New Jersey |
| 2 | CMP | Chicago / Minneapolis |
| 3 | HOU | Houston |
| 4 | SFO | San Francisco |
| 5 | SEA | Seattle |
| 6 | LAX | Los Angeles |
| 7 | CVC | Central Valley / Sacramento |
| 8 | WDC | Washington DC |
| 9 | MMT | Multimarket National |
| 10 | DAL | Dallas (Asian Channel) |

#### Response shape

```json
{
  "spot_query": "LEXUS15SA",
  "date_from": "2026-04-01",
  "date_to": "2026-04-30",
  "results": [
    {
      "market": "SFO",
      "count": 12,
      "airings": [
        {
          "date": "2026-04-07",
          "time": "13:02:45",
          "title": "LEXUS15SA107"
        }
      ]
    }
  ],
  "total": 12
}
```

#### Field notes

| Field | Notes |
|---|---|
| `spot_query` | The original `spot` parameter as received |
| `results[].market` | Market code string (e.g., `"SFO"`) — not the integer ID |
| `results[].airings[].time` | `HH:MM:SS` — converted from Etere frame offset at 29.97 fps |
| `results[].airings[].title` | Full asset title from `TPALINSE.TITLE` |
| `total` | Sum of all `count` values across all markets |

---

## Known gaps / planned

| Item | Notes |
|---|---|
| Customer canonical sync | Planned: SpotOps becomes source of truth for customer names; Control Room `customers.db` and Etere client IDs sync bidirectionally via SpotOps `/api/canon/*` endpoints |
| Contract search endpoint | No endpoint yet to look up a contract ID by code string — callers must know the integer `ID_CONTRATTITESTATA` for placement-by-week |
| Pagination | All endpoints return full result sets — no pagination; large date ranges on as-run may be slow |
