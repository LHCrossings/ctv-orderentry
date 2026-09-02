# Deploy the Control Room server on the Jumpbox: fast-forward pull, stop the running web_main.py,
# relaunch it through the "CTV OrderEntry Server" task (usrjp's interactive session, K: mapped),
# then prove port 8000 answers. Runs as SYSTEM via SSM; git needs no credential (public fetch works).
$ErrorActionPreference = 'Continue'
$repo = 'C:\Users\usrjp\windev\ctv-orderentry'
$name = 'CTV OrderEntry Server'
Set-Location $repo
"--- before: $(git -c safe.directory=* log --oneline -1 2>&1)"
# git writes progress to stderr; PowerShell wraps those lines as NativeCommandError records
# (the "weird red messages" of a normal pull). Stringify every line and trust the exit code.
$pull = (git -c safe.directory=* pull --ff-only 2>&1 | ForEach-Object { "$_" }) -join "`n"
$code = $LASTEXITCODE
"--- pull (exit $code): $($pull.Trim())"
if ($code -ne 0) { throw "pull failed (exit $code)" }
"--- after:  $(git -c safe.directory=* log --oneline -1 2>&1)"
$procs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*web_main.py*' })
"--- stopping $($procs.Count) web_main.py process(es): $(($procs | ForEach-Object { $_.ProcessId }) -join ',')"
$procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
if (-not (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue)) { throw "task '$name' not registered - run jumpbox_register_task.ps1 first" }
Start-ScheduledTask -TaskName $name
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:8000/' -TimeoutSec 5; if ($r.StatusCode -eq 200) { $ok = $true; break } } catch { }
}
$new = @(Get-Process python -IncludeUserName -ErrorAction SilentlyContinue | Where-Object { (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine -like '*web_main.py*' })
$who = ($new | ForEach-Object { "$($_.UserName)/session$($_.SessionId)" } | Sort-Object -Unique) -join ' '
"--- server: $(if ($ok) {'UP'} else {'NOT ANSWERING'}) on :8000 after $(2*($i+1))s; process user/session: $who"
if (-not $ok) { throw 'server did not answer on :8000' }
