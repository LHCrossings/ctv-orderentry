# Phoenix TV SRT archive - copies every FINISHED recording (C:\PhoenixSRT\rec\Phoenix_*.mp4)
# to the NAS folder V:\Programming\Phoenix SRT\<YYYY-MM-DD>\ (Lee 10/15: people pulling files
# straight off VIDEO-02 see slow transfers, so the finished hours live on the NAS; the team
# 10/16: one folder per recording day, like the old Memory exports, so files are easy to find
# and old days easy to delete).
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
# remux). The day folder comes from the file NAME (the segment's start time, which is what the
# team matches against Phoenix's programming grid), never from a timestamp. Copy to
# <name>.part on the NAS, then rename, so a half-copied file never looks complete; skip a file
# whose NAS copy already has the same size (a re-remuxed hour is copied again).
# NAS retention (Lee 10/16): keep $NasKeepDays days on the NAS (VIDEO-02 itself keeps three);
# a day folder dated before today - $NasKeepDays has its Phoenix_*.mp4 files deleted and the
# folder removed once empty. Nothing else on the NAS is ever touched.
# Files archived flat before 10/16 are moved into their day folder on the first pass.
# Status: $Root\archive_status.json. Log: $Root\logs\archive_YYYYMMDD.log.

$Root        = "C:\PhoenixSRT"
$RecDir      = "$Root\rec"
$LogDir      = "$Root\logs"
$Prefix      = "Phoenix"
$Dest        = "\\192.168.50.29\V Drive\Programming\Phoenix SRT"   # V: on VIDEO-02 (UNC: no drive letter needed)
$NasKeepDays = 5
$LoopSleep   = 60

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Log($msg) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $msg
    Add-Content -Path ("$LogDir\archive_{0:yyyyMMdd}.log" -f (Get-Date)) -Value $line
}

function Day-Of($name) {
    # Phoenix_20261016_0600.mp4 -> 2026-10-16 ; anything else -> $null
    if ($name -match ("^" + [regex]::Escape($Prefix) + "_(\d{4})(\d{2})(\d{2})_\d{4}\.mp4$")) {
        return "$($Matches[1])-$($Matches[2])-$($Matches[3])"
    }
    return $null
}

function Archive-Once {
    if (-not (Test-Path $Dest)) { return @{ reachable = $false; copied = 0; pending = @(); deleted = 0 } }

    # One-time migration: hours archived flat (before 10/16) move into their day folder.
    foreach ($f in Get-ChildItem $Dest -Filter "${Prefix}_*.mp4" -File -ErrorAction Stop) {
        $day = Day-Of $f.Name
        if (-not $day) { continue }
        $dir = Join-Path $Dest $day
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        Move-Item $f.FullName (Join-Path $dir $f.Name) -Force -ErrorAction Stop
        Log "moved $($f.Name) into $day\"
    }

    $done = 0; $pending = @()
    foreach ($f in Get-ChildItem $RecDir -Filter "${Prefix}_*.mp4" -File | Sort-Object Name) {
        $day = Day-Of $f.Name
        if (-not $day) { continue }
        $dir   = Join-Path $Dest $day
        $final = Join-Path $dir $f.Name
        $part  = "$final.part"
        $have  = Get-Item $final -ErrorAction SilentlyContinue
        if ($have -and $have.Length -eq $f.Length) { continue }
        $sw = [Diagnostics.Stopwatch]::StartNew()
        try {
            New-Item -ItemType Directory -Force -Path $dir | Out-Null
            Copy-Item $f.FullName $part -Force -ErrorAction Stop
            $len = (Get-Item $part -ErrorAction Stop).Length
            if ($len -ne $f.Length) { throw "size mismatch after copy: $len vs $($f.Length)" }
            Move-Item $part $final -Force -ErrorAction Stop
            $done++
            Log ("archived {0}\{1} ({2:N0} MB, {3:N0} s, {4:N1} MB/s)" -f $day, $f.Name, ($f.Length/1MB), $sw.Elapsed.TotalSeconds, (($f.Length/1MB)/[Math]::Max(1,$sw.Elapsed.TotalSeconds)))
        } catch {
            Log "archive FAILED for $($f.Name): $($_.Exception.Message)"
            Remove-Item $part -Force -ErrorAction SilentlyContinue
            $pending += $f.Name
        }
    }

    # NAS retention: day folders older than $NasKeepDays days, by the folder's date.
    $deleted = 0
    $cutoff = (Get-Date).Date.AddDays(-$NasKeepDays)
    foreach ($d in Get-ChildItem $Dest -Directory -ErrorAction Stop | Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}$' }) {
        $dayDate = [datetime]::ParseExact($d.Name, 'yyyy-MM-dd', $null)
        if ($dayDate -ge $cutoff) { continue }
        foreach ($f in Get-ChildItem $d.FullName -File | Where-Object { $_.Name -like "${Prefix}_*.mp4" -or $_.Name -like "${Prefix}_*.mp4.part" }) {
            Remove-Item $f.FullName -Force -ErrorAction Stop
            $deleted++
            Log "NAS retention: deleted $($d.Name)\$($f.Name) (older than $NasKeepDays days)"
        }
        if (-not (Get-ChildItem $d.FullName -Force | Select-Object -First 1)) {
            Remove-Item $d.FullName -Force
            Log "NAS retention: removed empty day folder $($d.Name)"
        }
    }
    return @{ reachable = $true; copied = $done; pending = $pending; deleted = $deleted }
}

Log "archive loop starting (user $env:USERNAME, dest $Dest, keep $NasKeepDays days)"
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
            keep_days     = $NasKeepDays
            nas_reachable = $r.reachable
            copied_now    = $r.copied
            deleted_now   = $r.deleted
            not_archived  = @($r.pending | Where-Object { $_ })   # finished locally, copy to the NAS failed this pass
        }
        ($st | ConvertTo-Json) | Set-Content "$Root\archive_status.json" -Encoding UTF8
    } catch {
        Log "loop error: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $LoopSleep
}
