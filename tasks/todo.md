# Batch the Traffic → Commercial Log sync (multiple contracts, one read)

## Request (Lee 2026-07-24)
Each log read is slow (~40s parse of a 13.8k-row workbook). Doing 4 contracts
means 4 separate reads. Want: add N contracts (each its own search box +
own date range — mixed agencies, e.g. 4 Daviselen + 2 Admerasia with different
flights), then ONE read → preview all → apply all in a single save.

## Design (grounded in current code)
- `_log_sync_compute(cur, contract_id, ws, …)` already takes a shared sheet +
  one contract. Batch = load once, compute per contract, write all, sort+save once.
- Contracts have disjoint line ids → disjoint log rows → no write conflict.
- Perf: scan the sheet into an index ONCE (read_only re-streams per iteration),
  then compute each contract against the index. N reads → 1 read + 1 save.

## Changes
- [ ] `_log_sync_scan_log(ws)` → {(line, date): [{xlrow, old}]} for ALL lines (once).
- [ ] `_log_sync_compute(cur, contract_id, log_index, date_from, date_to)` — take the
      pre-built index instead of iterating `ws`; filter by line_ids + date range.
- [ ] Extract `_log_sync_write_changes(ws, changes)` + `_log_sync_sort_and_save(ws, wb,
      path)` from the single apply endpoint (DRY; used by single + batch).
- [ ] Update single `/preview` + `/apply` to scan→compute (behavior identical).
- [ ] Add `POST /api/traffic/log-sync/preview-batch` — body {path, contracts:[{contract_id,
      date_from, date_to}]}. One read-only load, scan once, compute each (per-contract
      error isolation), warm writable copy. Return per-contract results + totals.
- [ ] Add `POST /api/traffic/log-sync/apply-batch` — one writable (warm) load, scan once,
      compute+collect all changes, write all, sort+CF+save once. Return per-contract
      written + mismatches + total.
- [ ] `log_sync.html`: contract search adds a ROW to a list (code/client + own
      date-from/date-to + remove ✕) via "+ Add another contract"; "Preview all" /
      "Apply all" hit the batch endpoints; combined preview grouped per contract.

## Verify (read-only)
- [ ] Batch preview of 2+ real contracts loads the log ONCE, returns per-contract changes.
- [ ] Confirm one scan-index reproduces the single-contract change set exactly.
- [ ] Do NOT write the live log as a test.

## Review (2026-07-24)
Implemented. orders.py parses + imports; log_sync.html JS passes node --check; no
dangling single-contract refs.
- `_log_sync_scan_log(ws)` scans the sheet once → shared index.
- `_log_sync_compute(cur, cid, log_index, …)` filters the index (was: iterated ws).
  Behavior-preserving: same (line,date)→rows groups as the old inline loop.
- Extracted `_log_sync_write_changes` + `_log_sync_sort_and_save`; single apply now
  uses them too (no drift between single/batch).
- Single `/preview` + `/apply` updated to scan→compute (identical behavior).
- New `/preview-batch` (one read-only load, scan once, compute per contract, warm
  writable copy, per-contract error isolation) and `/apply-batch` (one writable/warm
  load, compute all, write all, ONE sort+CF+save).
- log_sync.html: search adds to a cart of contract rows (each own From/To, removable);
  "Preview all" / "Apply all" hit the batch endpoints; per-contract preview sections
  + one combined apply bar.

Could NOT e2e-test here: the log is on the Windows K: drive (unreachable from WSL).
Verify on the jumpbox — one read for the whole batch, correct per-contract diffs,
MRU colors + custom sort intact after Apply all. Single-contract endpoints remain
(unused by the new UI, kept as a safety net).
