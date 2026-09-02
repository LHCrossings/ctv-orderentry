#!/usr/bin/env bash
# Deploy datamover_agent/agent.py to the Datamover over SSM: pull the repo there, copy agent.py to
# C:\datamover_agent\, restart the AirchecksAgentSvc NSSM service, verify /health.
# (Replaces the manual reminder in memory "feedback_datamover_reminder".) Needs AWS profile "crossings".
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-crossings}" AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-west-2}"
DATAMOVER=i-00350e9c4ab66f867
read -r -d '' PS <<'EOF' || true
$ErrorActionPreference = 'Continue'
$repo = 'C:\windev\ctv-orderentry'
Set-Location $repo
$pull = (git -c safe.directory=* pull --ff-only 2>&1 | ForEach-Object { "$_" }) -join "`n"
$code = $LASTEXITCODE
"--- pull (exit $code): $($pull.Trim())"
if ($code -ne 0) { throw "pull failed (exit $code)" }
"--- repo: $(git -c safe.directory=* log --oneline -1 2>&1)"
Copy-Item -Force "$repo\datamover_agent\agent.py" 'C:\datamover_agent\agent.py'
"--- copied agent.py ($((Get-FileHash 'C:\datamover_agent\agent.py').Hash.Substring(0,12)))"
& 'C:\datamover_agent\nssm.exe' restart AirchecksAgentSvc | Out-String
Start-Sleep -Seconds 5
$svc = Get-Service AirchecksAgentSvc
"--- service: $($svc.Status)"
try { $h = Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:8765/health' -TimeoutSec 10; "--- /health: $($h.StatusCode) $($h.Content)" } catch { throw "agent /health failed: $_" }
EOF
params=$(python3 -c 'import json,sys; print(json.dumps({"commands":[sys.argv[1]]}))' "$PS")
cmd=$(aws ssm send-command --instance-ids "$DATAMOVER" --document-name AWS-RunPowerShellScript \
  --comment "deploy datamover agent.py + restart AirchecksAgentSvc" --timeout-seconds 300 \
  --parameters "$params" --query Command.CommandId --output text)
echo "ssm command $cmd"
while :; do
  st=$(aws ssm get-command-invocation --command-id "$cmd" --instance-id "$DATAMOVER" --query Status --output text 2>/dev/null || echo Pending)
  [[ "$st" == InProgress || "$st" == Pending ]] || break
  sleep 5
done
aws ssm get-command-invocation --command-id "$cmd" --instance-id "$DATAMOVER" --query '[StandardOutputContent,StandardErrorContent]' --output text
echo "status: $st"; [[ "$st" == Success ]]
