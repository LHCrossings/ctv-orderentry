#!/usr/bin/env bash
# Pull + restart the Control Room server on the Jumpbox over SSM (see jumpbox_deploy.ps1).
# Usage: scripts/deploy/deploy_jumpbox.sh [script.ps1]   (needs AWS profile "crossings")
#        default script = jumpbox_deploy.ps1 (ctv-orderentry pull + server restart);
#        jumpbox_reportsort.ps1 pulls the sibling ReportSort checkout (no restart needed).
# One-time prerequisite on the box: jumpbox_register_task.ps1 (the "CTV OrderEntry Server" task).
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-crossings}" AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-west-2}"
JUMPBOX=i-0a9f88ee0ce8f1420
here="$(cd "$(dirname "$0")" && pwd)"
ps1="${1:-$here/jumpbox_deploy.ps1}"
params=$(python3 -c 'import json,sys; print(json.dumps({"commands":[open(sys.argv[1]).read()]}))' "$ps1")
cmd=$(aws ssm send-command --instance-ids "$JUMPBOX" --document-name AWS-RunPowerShellScript \
  --comment "deploy $(basename "$ps1" .ps1)" --timeout-seconds 300 \
  --parameters "$params" --query Command.CommandId --output text)
echo "ssm command $cmd"
while :; do
  st=$(aws ssm get-command-invocation --command-id "$cmd" --instance-id "$JUMPBOX" --query Status --output text 2>/dev/null || echo Pending)
  [[ "$st" == InProgress || "$st" == Pending ]] || break
  sleep 5
done
aws ssm get-command-invocation --command-id "$cmd" --instance-id "$JUMPBOX" --query '[StandardOutputContent,StandardErrorContent]' --output text
echo "status: $st"; [[ "$st" == Success ]]
