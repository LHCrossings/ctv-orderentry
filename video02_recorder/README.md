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
(the AAC LC; when the AAC frames are damaged and mp4 refuses them, the AC-3 track
instead, logged as `audio: ac3 copy`) + a tmcd timecode track from the segment
start; about 2.5 GB per hour.

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

**Packet loss.** SRT drops any packet it cannot recover inside its latency window
(relay log: `RCV-DROPPED n packet(s) ... ++SUPPRESSED: k`). The recorder is a
remux, so its `.ts` carries fresh continuity counters and loss is invisible there;
count it in the relay log and in the recorder log's `Packet corrupt` lines. With
2 s the 10/15 03:53-04:55 window lost 269 packets in three episodes and every
hour's AAC track broke (mp4 refuses a damaged ADTS frame; re-encoding fails on the
same frames; the AC-3 survived). Latency is 8 s since 05:56 10/15; delay is
irrelevant for a recording.

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
