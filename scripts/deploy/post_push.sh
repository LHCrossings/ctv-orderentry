#!/usr/bin/env bash
# After every `git push` (Claude Code PostToolUse hook, see .claude/settings.json):
#   1. pull Lee's local Windows checkout (C:\Users\scrib\windev\ctv-orderentry) — pull only, no restart
#   2. Jumpbox: pull + restart the Control Room server (scripts/deploy/deploy_jumpbox.sh)
#   3. Datamover: ONLY when datamover_agent/agent.py changed — pull, copy agent.py, restart the NSSM service
# Docs-only pushes (tasks/, *.md, .claude/) skip the server restart: nothing the server loads changed.
# State: .git/last-deployed holds the commit last deployed so the diff is against what is live.
set -uo pipefail
fail=0
cd "$(git rev-parse --show-toplevel)" || exit 0
[[ "$(git rev-parse --abbrev-ref HEAD)" == "main" ]] || { echo "post_push: not on main, skipping deploy"; exit 0; }
head=$(git rev-parse HEAD)
state=.git/last-deployed
last=$(cat "$state" 2>/dev/null || echo "")
if [[ -n "$last" ]] && git cat-file -e "$last" 2>/dev/null; then
  changed=$(git diff --name-only "$last" "$head")
else
  changed=$(git diff --name-only HEAD~1 "$head" 2>/dev/null || git ls-files)
fi
[[ -z "$changed" && "$last" == "$head" ]] && { echo "post_push: $head already deployed"; exit 0; }

code_changed=$(grep -v -E '^(tasks/|\.claude/|logs/|.*\.md$)' <<<"$changed" || true)
agent_changed=$(grep -x 'datamover_agent/agent.py' <<<"$changed" || true)

echo "post_push: deploying $head ($(wc -l <<<"$changed" | tr -d ' ') files changed since ${last:-<none>})"

# 0. Wait until GitHub actually serves the pushed commit. The PostToolUse hook fires the instant
# `git push` returns, and on 2026-09-07 both pulls below came back "Already up to date" at the
# OLD commit (replication lag) while the state file recorded the new one as deployed.
for _i in 1 2 3 4 5 6; do
  remote=$(timeout 40 git ls-remote origin refs/heads/main 2>/dev/null | cut -f1)
  [[ "$remote" == "$head" ]] && break
  echo "post_push: origin/main is ${remote:-unreachable}, waiting for $head ($_i/6)"; sleep 5
done
[[ "$remote" == "$head" ]] || { echo "post_push: WARNING origin/main never showed $head — not deploying"; exit 2; }

# 1. local Windows checkout (pull only) — verified: pulled HEAD must BE the pushed commit
win=/mnt/c/Users/scrib/windev/ctv-orderentry
if [[ -d "$win/.git" ]]; then
  win_ok=0
  for _i in 1 2 3; do
    if git -C "$win" pull --ff-only -q 2>/tmp/post_push_win.err && [[ "$(git -C "$win" rev-parse HEAD)" == "$head" ]]; then
      win_ok=1; break
    fi
    sleep 5
  done
  if [[ "$win_ok" -eq 1 ]]; then
    echo "post_push: local Windows checkout -> $(git -C "$win" log --oneline -1)"
  else
    echo "post_push: WARNING local Windows checkout is at $(git -C "$win" rev-parse --short HEAD), not $head: $(tr '\n' ' ' </tmp/post_push_win.err | cut -c1-200)"; fail=1
  fi
fi

# 2. Jumpbox
if [[ -n "$code_changed" ]]; then
  if scripts/deploy/deploy_jumpbox.sh > /tmp/post_push_jumpbox.log 2>&1 \
     && grep -q "^--- after:  ${head:0:7}" /tmp/post_push_jumpbox.log; then
    grep -E '^--- (after|server)' /tmp/post_push_jumpbox.log | sed 's/^/post_push: jumpbox /'
  elif grep -q '^--- after:' /tmp/post_push_jumpbox.log && ! grep -q "^--- after:  ${head:0:7}" /tmp/post_push_jumpbox.log; then
    echo "post_push: WARNING Jumpbox pulled $(grep '^--- after:' /tmp/post_push_jumpbox.log | cut -c13-19), not ${head:0:7} — server restarted on OLD code, rerun post_push.sh"
    exit 2
  else
    echo "post_push: WARNING Jumpbox deploy FAILED — see /tmp/post_push_jumpbox.log"; tail -5 /tmp/post_push_jumpbox.log
    exit 2
  fi
else
  echo "post_push: docs-only change — Jumpbox server not restarted (checkout pulls on next code deploy)"
fi

# 3. Datamover agent, only when agent.py changed
if [[ -n "$agent_changed" ]]; then
  echo "post_push: agent.py changed — deploying to the Datamover"
  if scripts/deploy/deploy_datamover_agent.sh > /tmp/post_push_datamover.log 2>&1; then
    tail -3 /tmp/post_push_datamover.log | sed 's/^/post_push: datamover /'
  else
    echo "post_push: WARNING Datamover agent deploy FAILED — see /tmp/post_push_datamover.log"; tail -5 /tmp/post_push_datamover.log
    exit 2
  fi
fi

# exit 2 makes the hook surface the warning instead of hiding it; state advances only on a clean run
if [[ "$fail" -ne 0 ]]; then exit 2; fi
echo "$head" > "$state"
