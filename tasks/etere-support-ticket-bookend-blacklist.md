# Etere Support Ticket — DRAFT

**Subject:** Blacklisting one Top&Bottom (bookend) spot deletes a *different* advertiser's bookend pair from the same break, with no blacklist record

**Severity:** High — silent loss of paid spots, broken passage accounting, missed air with no operator-visible trace

**System:** Etere Web — reproduced in both Executive Editor and Strategic Editor
**Station:** CVC (Cod_User 7)

---

## Setup

Break: **"M - Primetime News REV"**, first commercial segment, 20:20:00, duration 2:00
(traffic_block 9973, traffic_segment 68297, offset 2193804 frames)

Two independent bookend (Top&Bottom) contract lines were scheduled into this same daily break:

| Line | Contract | Flags | Pair placed |
|---|---|---|---|
| 71064 | Impact BVFL 26Q3 | capofila=1, finefila=1, **uniquetopbottom=1**, priorità 3 | 2026-01-26 (operation 174641) |
| 79607 | Imprenta PGE WS 26Q2 | capofila=1, finefila=1, priorità 3 | 2026-06-16 (operation 190728) |

(Break length is guidance for us, not a hard limit — overfilling is normal practice here. The issue is not capacity; it is that two independent Top&Bottom pairs ended up in the same break.)

---

## Incident 1 — Monday 2026-07-06 (Executive Editor)

Operator blacklisted the PGE bookend in the 20:20 break and re-placed it into the second break (20:31).

Result, verified directly in the database:
- The **BVFL pair for 7/6 was deleted**: no TPALINSE rows remain in any state (not even LIVELLO=666), no trafficPalinse rows, and **no Traffic_ScheduleList entry** was written for line 71064.
- Line 71064 orders 52 passages; only 51 remained placed. The order screen shows one passage "not scheduled," but there is nothing in BL to reschedule and no trace anywhere of what happened.
- The spot missed air on 7/6 (makegood now required).

## Incident 2 — Tuesday 2026-07-07 17:52 (Strategic Editor, controlled repro with DB monitoring)

To rule out operator error we repeated only the **first step** on the identical 7/7 break, while polling TPALINSE / trafficPalinse / Traffic_ScheduleList every 10 seconds.

Action: blacklist the PGE bookend (line 79607) only. The UI warned *"it's top bottom so both pieces will be removed"* — confirmed.

Captured result — a single atomic change:

**Deleted (schedule + traffic rows):**
- TPALINSE 13176345 — 20:20:00 BVFL15E195 (line **71064** — not the blacklisted line)
- TPALINSE 14758453 — 20:20:15 PGE15M77 (line 79607)
- TPALINSE 13176346 — 20:21:45 BVFL15E196 (line **71064** — not the blacklisted line)
- TPALINSE 14758454 — 20:22:00 PGE15M78 (line 79607)
- plus all four corresponding trafficPalinse rows

**Inserted:**
- ONE Traffic_ScheduleList row (ID 139434): line 79607, BlackList=1, PassageMiss=1, "Moved from Strategic Editor"
- **Nothing** for line 71064 — its pair was hard-deleted with zero accounting

## Expected vs. actual

- **Expected:** removing a Top&Bottom spot removes the two pieces of *its own linked pair* (line 79607) and records the miss for that line.
- **Actual:** ALL four capofila/finefila pieces in the break are removed — including the other advertiser's pair — and only the selected line is accounted for.

**Suspected cause:** the "both pieces will be removed" logic selects pieces by top/bottom *position within the break* rather than by the linked pair of the selected spot, so when two bookend pairs coexist in one break it deletes both pairs.

## Impact

1. Silent, unrecoverable deletion of another advertiser's paid bookend pair (BVFL missed air 7/6).
2. Passage accounting broken: placed + PassageMiss < N_PASSAGGI, with no BL entry to reschedule from.
3. No warning, log entry, or UI trace referencing the collateral line.

## Requests

1. Fix pair-removal to follow the selected spot's linked pair only.
2. Review whether the scheduler should place two Top&Bottom pairs into the same break at all (6/16, operation 190728): with two pairs, neither line's top/bottom guarantee can be honored (one pair's pieces end up 2nd and 2nd-from-last), and line 71064 has "unique top and bottom" set, which the second placement violated. This double-pair state is also the precondition that triggers the deletion bug in request 1.

## Attachments

- Operator screenshots (Executive Editor 7/6, Strategic Editor 7/7) — *from the MC operator's email*
- Database change log from the 7/7 repro — `tasks/etere-ticket-attachment-changelog.md`

## Incident 3 — Wednesday 2026-07-08 15:25 (Strategic Editor, retest AFTER updating to latest Etere version)

All workstations and the database were updated to the latest Etere version, per support's
advice that the issue could not be reproduced in their lab on that version. We then repeated
the controlled repro on the identical break for 2026-07-09 (same two lines, same 20:20 break,
DB polled every 10 seconds).

Action: blacklist the PGE bookend (line 79607) only.

Captured result — **identical to Incident 2, the bug is NOT fixed:**

**Deleted (schedule + traffic rows), one atomic change at 15:25:**
- TPALINSE 13176349 — 20:20:00 BVFL15E195 (line **71064** — not the blacklisted line)
- TPALINSE 14758457 — 20:20:15 PGE15M77 (line 79607)
- TPALINSE 13176350 — 20:21:45 BVFL15E196 (line **71064** — not the blacklisted line)
- TPALINSE 14758458 — 20:22:00 PGE15M78 (line 79607)
- plus all four corresponding trafficPalinse rows (5225520, 5225521, 5856757, 5856758)

**Inserted:**
- ONE Traffic_ScheduleList row (ID 139466): line 79607, BlackList=1, PassageMiss=1, "Moved from Strategic Editor"
- **Nothing** for line 71064 — its pair was again hard-deleted with zero accounting

## Disclosure — manual repairs we made (in case you inspect our database)

To recover, we made the following manual writes; these rows are ours, not the system's:
- Re-inserted the deleted line 71064 pair on 7/7 (TPALINSE 13176345/13176346 + trafficPalinse) from a backup taken before the repro.
- Re-inserted the deleted line 71064 pair on 7/9 (TPALINSE 13176349/13176350 + trafficPalinse 5225520/5225521) from a backup taken before the Incident 3 retest. The 7/9 PGE blacklist itself (TSL 139466) was intentional and stands.
- Inserted the Traffic_ScheduleList row (ID 139433) that the 7/6 incident failed to write, so the orphaned passage appeared in BL and could be rescheduled (it has since been re-placed on 7/11 and the row consumed).
