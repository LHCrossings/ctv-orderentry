# Phoenix TV SRT recorder (VIDEO-02, Sacramento office)

`record.ps1` runs on VIDEO-02 (`C:\PhoenixSRT\record.ps1`) as the Scheduled Task
`PhoenixSrtRecorder` (SYSTEM, at startup, restart on failure, no time limit).
Phoenix's listener accepts only ONE caller. VIDEO-02 is that caller and the
Datavideo must NOT dial the Phoenix URL again or the recorder is locked out.

Two ffmpeg processes: the **relay** holds the SRT connection and forwards the
payload as opaque 1316-byte blocks (`-f rawvideo -video_size 1316x1`, byte-exact,
verified by MD5) to the Datavideo (`udp://10.1.50.98:6005`, its input is
`udp://@:6005`) and to loopback port 6010; the **recorder** reads loopback 6010
and segments. Any ffmpeg remux of the relay (parsed or `+noparse`) left the
Datavideo's Pi hardware decoder frozen or black: Phoenix sends one PES per
field with no PTS on the second field, and ffmpeg's parser invents one.
EDIT-02 Memory records in parallel from the Datavideo's SDI for now, but its
recordings off the relay stutter and EDIT-02 is being retired (Lee, 10/15): the
VIDEO-02 files are the product.
It writes hourly, clock-aligned files to
`C:\PhoenixSRT\rec\Phoenix_YYYYMMDD_HHMM.mp4` (recorded as .ts, remuxed to .mp4
once the hour closes; a segment is closed when a NEWER segment exists or the
recorder is not running, never by elapsed time: the 10/15 "not written for 90 s"
rule fired during every relay reconnect and remuxed the live hour at 38 and 40
minutes, so the NAS briefly held partial hours named like full ones). 1080i 29.97 H.264 + ONE audio track like Memory's export
(Phoenix's AAC LC copied, logged `audio: aac copy`; when one of its ADTS frames is
damaged and mp4 refuses the track, the same AAC decoded with errors ignored and
re-encoded at 256k, logged `audio: aac re-encoded, damaged frames dropped`, about
4 min per hour) + a tmcd timecode track from the segment start; about 2.5 GB per
hour. **The stream's second audio track (AC-3) is digital silence** (-91 dB over
whole hours, measured 10/17) and is never used: the 10/15-10/17 "ac3 copy"
fallback wrote 15 silent hours, which is what Maija reported on 10/17.

**Archive to the NAS.** `archive.ps1` runs as the Scheduled Task `PhoenixSrtArchive`
in the logged-on `CROSSINGSTV\video` session (trigger: at that user's logon) and
copies every finished `.mp4` to `\\192.168.50.29\V Drive\Programming\Phoenix SRT\YYYY-MM-DD\`
(V: on VIDEO-02; ~100 MB/s), one folder per recording day taken from the file
name (team request 10/16). Copy to `.part`, rename, size check; a file whose NAS
copy already has the same size is skipped. NAS retention is 5 days (Lee 10/16,
`$NasKeepDays`): a day folder dated before today - 5 has its `Phoenix_*.mp4`
deleted and is removed once empty; nothing else on the NAS is touched. Files
archived flat before 10/16 were moved into `2026-10-15\` on the first pass.
It cannot run as SYSTEM: the NAS rejects the computer account and Windows then
blocks the guest fallback. So nothing is archived while nobody is logged on
(after a reboot, until someone logs in); the recorder keeps recording and the
72 h local retention gives the archive time to catch up. Status:
`C:\PhoenixSRT\archive_status.json`; log `C:\PhoenixSRT\logs\archive_YYYYMMDD.log`.

**Live view (team 10/16).** The relay also writes the raw bytes to loopback
`udp://127.0.0.1:6011` (`$MonitorTarget`). The "Phoenix Live View" shortcut on the
VIDEO-02 desktop opens VLC on `udp://@127.0.0.1:6011`, a second or two behind air,
to check that Phoenix is sending programming and not its Daily Talk fallback.
VLC is a separate process on a separate port: opening or closing it cannot touch
the recorder. Software decode (no GPU) uses about one core while it is open.
One viewer at a time (unicast port).

**Confidence monitor (Maija 10/16).** `thumb.ps1` (Scheduled Task `PhoenixSrtThumb`,
SYSTEM) reads a third loopback tap (`udp://127.0.0.1:6012`, `$ThumbTarget`), grabs one
deinterlaced 640 px JPEG a minute and serves it on `http://100.90.230.98:8081/live.jpg`
(+ `/live.json` with the capture time). The Control Room Monitor Wall shows it in a
"Phoenix TV feed" panel via the app's `/api/master-control/phoenix/thumb` proxy, with
the frame's age; amber past 3 minutes, red when VIDEO-02 does not answer. Firewall
rule "Phoenix SRT thumbnail 8081": TCP 8081 from 100.64.0.0/10, 192.168.50.0/24,
10.1.50.0/24. Separate process, separate port: it cannot touch the recorder.
Every grab is also kept for 24 h in `C:\PhoenixSRT\frames\Phoenix_YYYYMMDD_HHMMSS.jpg`
(Lee 10/16; ~65 MB), listed at `/frames/` and served at `/frames/<name>`, so a Daily
Talk fallback can be matched against the relay's break times after the fact.

**Audio meter (Maija 10/19).** The same ffmpeg call in `thumb.ps1` measures 3 s of the AAC
track (`-map 0:a:0?`, never the silent AC-3) with `astats`: RMS and peak per channel in dBFS.
`live.json` carries them as `audio`; `levels.json` keeps 24 h of readings (one a minute,
reloaded on restart). The Monitor Wall panel draws an L/R meter (-60..0 dBFS, zones at -20
and -9), says NO AUDIO when both channels sit at or below -60 dBFS, and shows a 24 h strip
with red marks for silent minutes (`/api/master-control/phoenix/levels`). The tap sits before
the recorder: silence on the meter is Phoenix's or the SRT path's; sound on the meter with a
mute mp4 is ours. The thumb log gets one line when the feed goes silent and one when it comes back.

**Packet loss.** SRT drops any packet it cannot recover inside its latency window
(relay log: `RCV-DROPPED n packet(s) ... ++SUPPRESSED: k`). The recorder is a
remux, so its `.ts` carries fresh continuity counters and loss is invisible there;
count it in the relay log and in the recorder log's `Packet corrupt` lines. With
2 s the 10/15 03:53-04:55 window lost 269 packets in three episodes and every
hour's AAC track broke (mp4 refuses a damaged ADTS frame). Latency is 8 s since
05:56 10/15 and no packet has been dropped since; delay is irrelevant for a
recording.

**Reconnects tear one frame.** Phoenix's SRT session breaks several times a day
(10/16: ten times) and every reconnect joins the stream mid-frame, so the hour's
`.ts` holds one torn AAC frame and `aac copy` refuses the whole track. Two layers
handle it: the recorder reads its loopback input with `-fflags +discardcorrupt`
(the continuity jump at the join flags the torn PES corrupt and it is dropped
before it reaches the `.ts`), and the remux falls back to re-encoding the AAC with
decoder errors ignored (verified on a deliberately spliced sample: `aac copy`
fails, the re-encode keeps the full duration with sound across the join).

**A reconnect also tears the VIDEO, and the torn join poisons decoders (10/30).**
`Phoenix_20261030_0800.mp4` (reconnect at 08:12) played in VLC but showed random
black flashes in EDIUS from the join to the end of the file (Maija/Jake 10/30).
The `.ts` carried, in order: the last field before the drop cut off at slice 11 of
28, two P-fields of the NEW session that reference pictures not in the file (the
new session joined mid-GOP), then its first IDR. The remux is a stream copy, so
the mp4 has the same three bad fields with squashed timestamps (three fields in
531 ticks). ffmpeg's own decoder with frame threads flagged every third frame
"corrupt decoded frame" for the remaining 47 minutes (28,299 frames); with one
thread only the join itself errors, so the stream after the IDR is clean and the
damage is decoder state poisoned by the join. EDIUS evidently does the same.
Rule since 10/30: a relay exit closes the segment (`Mark-CutTail` + a fresh
recorder, which drops everything up to the first keyframe), so the reconnected
stream opens a NEW file named for its own start minute (`Phoenix_20261030_0812`)
on a clean IDR, and `Remux-Closed` trims the cut segment to its last keyframe
(`Last-KeyframeSeconds`, `-to`; ffmpeg compares DTS in output time). One
reconnect = one extra file for that hour; never a join inside a file. Keeping the
`.ts` as a "backup" would not help: it holds the same bytes.
Hand repair of an already-recorded hour: cut the mp4 at the first IDR after the
join with `-noaccurate_seek -ss <a little after the IDR dts>` (a plain `-ss` lands
on the keyframe BEFORE the join and keeps the junk as discard-flagged pre-roll),
and the pre-join part with `-to <dts of the last complete field>`.

Local retention: 72 hours (three days, Lee 10/15 and again 10/16; the NAS keeps five), and oldest-first below 80 GB free on C:. At 5.6 Mbps that is ~180 GB, which fits the 465 GB C: with the floor.
Status: `C:\PhoenixSRT\status.json`. Logs: `C:\PhoenixSRT\logs\`.

VIDEO-02 is SSM hybrid node `mi-02adb6308ecd7a523` (us-west-2, profile `crossings`).

Deploy a change: copy the script to `C:\PhoenixSRT\` via SSM
`AWS-RunPowerShellScript` as a plain-text here-string (a base64 blob trips the
secret classifier), then
`Stop-ScheduledTask PhoenixSrtRecorder; Stop-Process -Name ffmpeg; Start-ScheduledTask PhoenixSrtRecorder`
(for archive.ps1: `Stop-ScheduledTask PhoenixSrtArchive; Start-ScheduledTask PhoenixSrtArchive`).

Register the task (once):
```powershell
$a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\PhoenixSRT\record.ps1"
$t = New-ScheduledTaskTrigger -AtStartup
$s = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName PhoenixSrtRecorder -Action $a -Trigger $t -Settings $s -User SYSTEM -RunLevel Highest -Force
Start-ScheduledTask PhoenixSrtRecorder

# archive task, in the video user's session (no stored password; see above)
$a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\PhoenixSRT\archive.ps1"
$t = New-ScheduledTaskTrigger -AtLogOn -User 'CROSSINGSTV\video'
$pr = New-ScheduledTaskPrincipal -UserId 'CROSSINGSTV\video' -LogonType Interactive
Register-ScheduledTask -TaskName PhoenixSrtArchive -Action $a -Trigger $t -Settings $s -Principal $pr -Force
Start-ScheduledTask PhoenixSrtArchive
```

Timecode note: the tmcd start is written drop-frame (`HH:MM:SS;00`). At 59.94 DF the first
four frames of any minute not divisible by 10 are dropped, so a segment that started at e.g.
00:53:00 reads 00:52:59;56 (two frames early). Clock-aligned hours start at :00:00 and are
exact; only restart partials are affected. Switch to non-drop (`HH:MM:SS:00`) at the next
script touch if that matters to the editors.
