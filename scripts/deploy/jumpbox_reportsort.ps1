# Pull the ReportSort checkout on the Jumpbox (sibling repo of ctv-orderentry, own remote).
# main.py is invoked per run by scripts/run_reportsort.py, so a pull is the whole deploy — no restart.
$ErrorActionPreference = 'Continue'
$repo = 'C:\Users\usrjp\windev\ReportSort'
Set-Location $repo
"--- before: $(git -c safe.directory=* log --oneline -1 2>&1)"
$pull = (git -c safe.directory=* pull --ff-only 2>&1 | ForEach-Object { "$_" }) -join "`n"
$code = $LASTEXITCODE
"--- pull (exit $code): $($pull.Trim())"
if ($code -ne 0) { throw "pull failed (exit $code)" }
"--- after:  $(git -c safe.directory=* log --oneline -1 2>&1)"
