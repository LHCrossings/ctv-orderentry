<#
.SYNOPSIS
  Weekly: copy the newest Etere SQL .bak up to OneDrive and keep only the 4 most
  recent there. Replaces the manual Monday "drag newest in / delete oldest" chore.

.CONTEXT
  - The .bak is already produced DAILY by a SQL Agent job into the Source folder
    below; this script only handles the OneDrive side (copy newest + prune to 4).
  - Destination is the operations@crossingstv.com OneDrive folder
    "Documents / Etere SQL Backups (Keep 4 most recent)", reached headlessly via
    rclone (no OneDrive sync client on the server).

.ONE-TIME SETUP (on the SQL server)
  1. Download rclone (single exe) -> C:\Scripts\rclone.exe
  2. rclone config
       n  (new remote) -> name: operations
       storage -> onedrive
       client_id / client_secret -> blank
       region -> 1 (Global)
       browser opens -> SIGN IN AS operations@crossingstv.com -> approve
       drive type -> OneDrive Personal or Business (for that account)
     (If the tenant blocks third-party OAuth apps, an M365 admin must consent once.)
  3. Test:  rclone lsf "operations:Etere SQL Backups (Keep 4 most recent)"

.SCHEDULE
  Task Scheduler -> weekly Monday -> "Run only when user is logged on" ->
    powershell.exe -ExecutionPolicy Bypass -File "C:\Scripts\onedrive_backup_sync.ps1"

.NOTES
  - Retention is by COUNT (newest 4) -> a skipped week never leaves you with zero.
  - Only the remote is pruned; local daily-backup retention stays with the SQL job.
#>

$Source = "C:\Program Files\Microsoft SQL Server\MSSQL15.MSSQLSERVER\MSSQL\Backup\Etere_crossing"
$Rclone = "C:\Scripts\rclone.exe"
$Remote = "operations:Etere SQL Backups (Keep 4 most recent)"   # 'operations' = the rclone remote name
$Keep   = 4

# 1) newest local .bak
$newest = Get-ChildItem "$Source\*.bak" -ErrorAction Stop |
          Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $newest) { Write-Output "No .bak found in $Source"; exit 1 }

# 2) upload it (skip if it's already on the remote)
& $Rclone copyto $newest.FullName "$Remote/$($newest.Name)" --ignore-existing
if ($LASTEXITCODE -ne 0) { Write-Output "rclone upload failed ($LASTEXITCODE)"; exit 1 }

# 3) prune remote: keep newest $Keep, delete the rest (by modified time)
& $Rclone lsf $Remote --files-only --format "tp" --separator "|" |
    Sort-Object { ($_ -split '\|',2)[0] } -Descending |
    Select-Object -Skip $Keep |
    ForEach-Object { & $Rclone deletefile "$Remote/$(($_ -split '\|',2)[1])" }

Write-Output "Done: uploaded $($newest.Name); kept newest $Keep on remote."
