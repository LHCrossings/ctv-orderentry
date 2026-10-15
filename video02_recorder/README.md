# Phoenix TV SRT recorder (VIDEO-02, Sacramento office)

`record.ps1` runs on VIDEO-02 (`C:\PhoenixSRT\record.ps1`) as the Scheduled Task
`PhoenixSrtRecorder` (SYSTEM, at startup, restart on failure, no time limit).
Phoenix's listener accepts only ONE caller. VIDEO-02 is that caller and the
Datavideo must NOT dial the Phoenix URL again or the recorder is locked out.
For now the recorder also relays the untouched TS to the Datavideo as
`udp://10.1.50.98:6005` (its input is `udp://@:6005`) so EDIT-02 Memory records
in parallel; the Datavideo is to be retired later (Lee, 2026-10-15).
It writes hourly, clock-aligned files to
`C:\PhoenixSRT\rec\Phoenix_YYYYMMDD_HHMM.mp4` (recorded as .ts, remuxed to .mp4
once the hour closes). 1080i 29.97 H.264 + AAC LC + AC-3 (two audio tracks), about 2.5 GB per hour.

Retention: 120 hours (five days, Lee 10/15), and oldest-first below 80 GB free on C:. At 5.6 Mbps that is ~300 GB; the 465 GB C: holds ~4 days before the floor governs, so a second drive or a NAS copy tier is needed for a true five days.
Status: `C:\PhoenixSRT\status.json`. Logs: `C:\PhoenixSRT\logs\`.

VIDEO-02 is SSM hybrid node `mi-02adb6308ecd7a523` (us-west-2, profile `crossings`).

Deploy a change: copy this file to `C:\PhoenixSRT\record.ps1` via SSM
`AWS-RunPowerShellScript`, then
`Stop-ScheduledTask PhoenixSrtRecorder; Stop-Process -Name ffmpeg; Start-ScheduledTask PhoenixSrtRecorder`.

Register the task (once):
```powershell
$a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\PhoenixSRT\record.ps1"
$t = New-ScheduledTaskTrigger -AtStartup
$s = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName PhoenixSrtRecorder -Action $a -Trigger $t -Settings $s -User SYSTEM -RunLevel Highest -Force
Start-ScheduledTask PhoenixSrtRecorder
```
