# Off-Air Gap Finder (/billing/offair-gaps) — 2026-07-16

Lee: billing utility to find spots stuck at "never aired" after an AU crash +
EE "Reset transmitted events", and mass-mark them manually verified.

Background (discovered via live EE experiment, see memory `ee-status-asrun`):
- Stuck signature: `STATUS='I' AND ASRUN_STATUS_M='I' AND LIVELLO=0` (only a
  reconciled past day can carry ASRUN_STATUS_M).
- Fix write (byte-identical to EE "check manually the selected events"):
  `STATUS='Q', ASRUN_STATUS_O='M', LASTUPDATE=GETDATE()`.
- MC re-airs truly-missed commercials after restart, so stuck rows ≈ all aired.
- Billing window = broadcast ∪ calendar month (July 2026 = 6/29–7/31).

## Plan

- [x] Discovery: status alphabet, verify/un-verify writes, reset signature
- [x] Validate signature on real incidents (7/8 WDC 380, 6/4 HOU 397,
      6/2 DAL 429, 4/5 SFO 362; 1,631 stuck rows since 6/1)
- [x] Restore Lee's test row 15092444 to pre-experiment state
- [x] GET /billing/offair-gaps page route + template (billing/offair_gaps.html)
- [x] GET /api/billing/offair-gaps/scan?year&month — window calc, stuck-row
      query, group contiguous gaps per station/day (split on >60 min),
      client/contract via trafficTPalinse→CONTRATTITESTATA→ANAGRAF
- [x] POST /api/billing/offair-gaps/verify — {ids:[...]} → guarded UPDATE
      (only rows still matching the stuck signature), return count
- [x] Card on billing.html
- [x] Verify: scan July 2026 (expect 7/8 + 7/11 WDC, 7/2 / 7/13 / 7/15 NYC,
      7/6 CVC…) and June 2026 (6/2 DAL, 6/4 HOU, 6/8). First real
      mark-verified click stays with Lee.

## Review (2026-07-16)

- Scan verified live against the WSL test server: July 2026 window 6/29–7/31 →
  679 stuck rows / 115 COM / 27 gaps incl. 7/8 WDC 377-row incident;
  June 2026 → 953 rows / 147 COM / 40 gaps incl. 6/4 HOU 396 and 6/2 DAL 429.
  Page + hub card render (HTTP 200). Post-midnight times show as e.g. 5:30a⁺¹.
- Verify endpoint is guarded (`AND STATUS='I' AND ASRUN_STATUS_M='I'`) so a
  double-click or stale scan can never touch rows that changed since.
  Lee runs the first real mark-verified through the UI.
- Streaming stations (st11/st12) are listed too — informational; unchecking
  them is fine, marking them is harmless.
