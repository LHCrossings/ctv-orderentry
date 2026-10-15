# Phoenix TV SRT archive - copies every FINISHED recording (C:\PhoenixSRT\rec\Phoenix_*.mp4)
# to the NAS folder V:\Programming\Phoenix SRT (Lee 10/15: people pulling files straight off
# VIDEO-02 see slow transfers, so the finished hours live on the NAS).
#
# Runs as the Scheduled Task "PhoenixSrtArchive" in the logged-on CROSSINGSTV\video session,
# NOT as SYSTEM like the recorder: the NAS (QNAP 192.168.50.29) accepts the domain user and
# rejects the computer account, after which Windows refuses the guest fallback
# ("your organization's security policies block unauthenticated guest access"). No password
# is stored anywhere; the cost is that nothing is copied while nobody is logged on (after a
# reboot, until someone logs in - the recorder keeps recording, and the 72 h local retention
# gives the archive that long to catch up).
#
# Rules: a file is finished once it is an .mp4 (the recorder writes .ts and renames after the
# remux). Copy to <name>.part on the NAS, then rename, so a half-copied file never looks
# complete; skip a file whose NAS copy already has the same size. Never delete on the NAS.
# Status: $Root\archive_status.json. Log: $Root\logs\archive_YYYYMMDD.log.

$Root       = "C:\PhoenixSRT"
$RecDir     = "$Root\rec"
$LogDir     = "$Root\logs"
$Prefix     = "Phoenix"
$Dest       = "\\192.168.50.29\V Drive\Programming\Phoenix SRT"   # V: on VIDEO-02 (UNC: no drive letter needed)
$LoopSleep  = 60

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Log($msg) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $msg
    Add-Content -Path ("$LogDir\archive_{0:yyyyMMdd}.log" -f (Get-Date)) -Value $line
}

function Archive-Once {
    $done = 0; $pending = @()
    if (-not (Test-Path $Dest)) { return @{ reachable = $false; copied = 0; pending = @() } }
    $have = @{}
    Get-ChildItem $Dest -Filter "${Prefix}_*.mp4" -ErrorAction Stop | ForEach-Object { $have[$_.Name] = $_.Length }
    foreach ($f in Get-ChildItem $RecDir -Filter "${Prefix}_*.mp4" | Sort-Object Name) {
        if ($have.ContainsKey($f.Name) -and $have[$f.Name] -eq $f.Length) { continue }
        $part = Join-Path $Dest ($f.Name + ".part")
        $final = Join-Path $Dest $f.Name
        $sw = [Diagnostics.Stopwatch]::StartNew()
        try {
            Copy-Item $f.FullName $part -Force -ErrorAction Stop
            $len = (Get-Item $part -ErrorAction Stop).Length
            if ($len -ne $f.Length) { throw "size mismatch after copy: $len vs $($f.Length)" }
            Move-Item $part $final -Force -ErrorAction Stop
            $done++
            Log ("archived {0} ({1:N0} MB, {2:N0} s, {3:N1} MB/s)" -f $f.Name, ($f.Length/1MB), $sw.Elapsed.TotalSeconds, (($f.Length/1MB)/[Math]::Max(1,$sw.Elapsed.TotalSeconds)))
        } catch {
            Log "archive FAILED for $($f.Name): $($_.Exception.Message)"
            Remove-Item $part -Force -ErrorAction SilentlyContinue
            $pending += $f.Name
        }
    }
    return @{ reachable = $true; copied = $done; pending = $pending }
}

Log "archive loop starting (user $env:USERNAME, dest $Dest)"
$wasReachable = $true
while ($true) {
    try {
        $r = Archive-Once
        if (-not $r.reachable -and $wasReachable) { Log "NAS not reachable: $Dest" }
        if ($r.reachable -and -not $wasReachable) { Log "NAS reachable again" }
        $wasReachable = $r.reachable
        $st = [ordered]@{
            updated       = (Get-Date).ToString("s")
            user          = $env:USERNAME
            dest          = $Dest
            nas_reachable = $r.reachable
            copied_now    = $r.copied
            not_archived  = @($r.pending | Where-Object { $_ })   # finished locally, copy to the NAS failed this pass
        }
        ($st | ConvertTo-Json) | Set-Content "$Root\archive_status.json" -Encoding UTF8
    } catch {
        Log "loop error: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $LoopSleep
}
