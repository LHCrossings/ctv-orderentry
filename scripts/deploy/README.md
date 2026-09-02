# Deploy scripts

The Control Room server lives on the **Jumpbox** (`C:\Users\usrjp\windev\ctv-orderentry`,
`python web_main.py`, `reload=False`). A `git pull` alone never changes what it serves — the
process must be restarted. These scripts make that one command, and chain the other machines.

| Script | What it does |
|---|---|
| `post_push.sh` | Run after every `git push` to main. Pulls Lee's local Windows checkout, runs `deploy_jumpbox.sh` (skipped for docs-only pushes), runs `deploy_datamover_agent.sh` only when `datamover_agent/agent.py` changed. Remembers the last deployed commit in `.git/last-deployed`. |
| `deploy_jumpbox.sh` | SSM → Jumpbox: `git pull --ff-only`, stop `web_main.py`, `Start-ScheduledTask "CTV OrderEntry Server"`, verify `:8000` answers. |
| `jumpbox_deploy.ps1` | The PowerShell the above sends. |
| `jumpbox_register_task.ps1` | One-time: registers the `CTV OrderEntry Server` task — runs the server as `CTVETERE\usrjp` in his **interactive** RDP session (the K: drive mapping the program grid and traffic logs need lives there), no stored password, no execution time limit. Registered 2026-09-01. |
| `deploy_datamover_agent.sh` | SSM → Datamover: pull `C:\windev\ctv-orderentry`, copy `agent.py` to `C:\datamover_agent\`, `nssm restart AirchecksAgentSvc`, check `/health`. |

All need AWS profile `crossings` (SSM `send-command`). Git on both boxes fetches this public
repo without credentials, even as SYSTEM.

## Making it automatic (Claude Code hook)

The auto-mode permission classifier refuses to let Claude write hook configuration itself, so
Claude follows this as a standing rule by hand (memory `feedback-deploy-after-push`). To make it
mechanical, add this to `.claude/settings.json` in the repo (or via `/hooks`):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 -c 'import json,sys;c=json.load(sys.stdin).get(\"tool_input\",{}).get(\"command\",\"\");sys.exit(0 if \"git push\" in c else 1)' && \"$CLAUDE_PROJECT_DIR\"/scripts/deploy/post_push.sh",
            "timeout": 420,
            "statusMessage": "post-push deploy: local checkout, Jumpbox server, Datamover agent if changed"
          }
        ]
      }
    ]
  }
}
```

The command reads the hook's stdin JSON, exits quietly unless the Bash command contained
`git push`, and otherwise runs `post_push.sh`. (`jq` is not installed here, hence python.)

## PowerShell red text is not an error

`git pull` writes its progress lines to stderr; PowerShell paints those as a red
`NativeCommandError` block even when the pull succeeded. Judge a pull by `$LASTEXITCODE`
(the scripts do), never by the colour or by matching the word "error".
