# Attachment: Database change log — bookend blacklist repro, CVC 2026-07-07

All timestamps US Pacific, 2026-07-07. Captured by polling TPALINSE,
trafficPalinse, and Traffic_ScheduleList every 10 seconds during the
controlled reproduction. `-` = row deleted, `+` = row inserted.
Frame values at 29.97 fps; times shown as broadcast HH:MM:SS.

## Baseline (17:51:32) — 20:20 break ("M - Primetime News REV", segment 68297)

```
20:20:00 id=13176345 dur=:15 line=71064 BVFL15E195: BVF_EvergreenTruck_Jan2026_15   <- BVFL top
20:20:15 id=14758453 dur=:15 line=79607 PGE15M77: 3 Tips C01 v02 MAN                <- PGE top piece
20:20:30 id=14046503 dur=:30 line=74752 SPARE30M23: STA Mandarin 0511
20:21:00 id=14131214 dur=:30 line=75131 SMUD30M50: Power of the Moment May 2026 MAN
20:21:30 id=14535144 dur=:15 line=78088 CALTRANS15M20: TSC_GoSafely_AAPI_YOUTUBE
20:21:45 id=13176346 dur=:15 line=71064 BVFL15E196: BVF_ExplorerEvergreen_Jan2026_15 <- BVFL bottom piece
20:22:00 id=14758454 dur=:15 line=79607 PGE15M78: 3 Tips C02 v01 MAN                <- PGE bottom
```

Two Top&Bottom bookend lines in the same break:
- Line 71064 (Impact BVFL 26Q3) — capofila=1, finefila=1, uniquetopbottom=1
- Line 79607 (Imprenta PGE WS 26Q2) — capofila=1, finefila=1

Blacklist state before action: no Traffic_ScheduleList rows for line 79607.

## 17:52:35 — operator blacklists the PGE bookend ONLY (Strategic Editor)

UI prompt: "it's top bottom so both pieces will be removed" — confirmed.
One atomic change observed:

```
- sched 20:20:00 id=13176345 line=71064 BVFL15E195      <- NOT the blacklisted line
- sched 20:20:15 id=14758453 line=79607 PGE15M77
- sched 20:21:45 id=13176346 line=71064 BVFL15E196      <- NOT the blacklisted line
- sched 20:22:00 id=14758454 line=79607 PGE15M78
- trafficPalinse id_tp=13176345 date=2026-07-07 seg=68297 line=71064
- trafficPalinse id_tp=13176346 date=2026-07-07 seg=68297 line=71064
- trafficPalinse id_tp=14758453 date=2026-07-07 seg=68297 line=79607
- trafficPalinse id_tp=14758454 date=2026-07-07 seg=68297 line=79607
+ BL id=139434 line=79607 blacklist=1 passagemiss=1 notes="Moved from Strategic Editor"
```

Result: ALL FOUR bookend pieces deleted — both pairs. A blacklist record was
written for line 79607 only. Line 71064's pair was hard-deleted with no
Traffic_ScheduleList entry, no LIVELLO=666 soft-delete, no trace.

This matches the 2026-07-06 incident (Executive Editor) where line 71064's
pair vanished the same way: after that day, trafficPalinse held rows for every
scheduled day of the flight except 7/6 (51 of 52 passages), with no blacklist
row — placed + missed < ordered.

## After the repro (for completeness)

- ~18:00 — station manually re-inserted the deleted line 71064 pair
  (TPALINSE 13176345/13176346) from a pre-repro backup.
- 17:59–18:01 — operator re-placed the line 79607 pair from BL into the
  20:59:00 break (segment 68303); BL id 139434 consumed as expected.
  Note: on re-placement both pieces were assigned the SAME creative
  (ID_FILMATI 140778 top and bottom) where the original pair alternated
  140778/140781 — corrected manually.
- 18:06 — the 7/6 missed passage was rescheduled from BL to 2026-07-11
  (segment 68299); line 71064 accounting restored to 52/52. (The BL row it
  was rescheduled from was inserted manually by the station, since the 7/6
  incident never wrote one — see "Disclosure" in the ticket.)
