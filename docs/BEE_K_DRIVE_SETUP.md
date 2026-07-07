# K-Drive Mount Setup for "the Bee" (Linux)

The Control Room web app reads/writes several trees on the K: network share
(programming grids, weekly traffic logs, report archives). On Windows these are
reached via the mapped `K:` drive. On Linux the code expects the same share
mounted at **`/mnt/k`** (Docker maps host `/mnt/k-drive` → container `/mnt/k`).

**No physical access is required** — everything below can be done over SSH by
anyone with sudo on the Bee.

## What the app expects on Linux

| Purpose | Path used by code | Override env var |
|---|---|---|
| Weekly programming grids | `/mnt/k/Programming` | `PROGRAMMING_GRID_ROOT` |
| Weekly traffic logs (.xlsm) | `/mnt/k/Traffic/logs` | `TRAFFIC_LOG_ROOT` |
| ReportSort pre/post log archives | `/mnt/k/!Archives` | `K_ARCHIVES_ROOT` |

The env vars are only needed if the share is mounted somewhere other than
`/mnt/k` (bare metal) or `/mnt/k-drive` (Docker host).

## Steps (run on the Bee, via SSH)

1. **Install CIFS support** (Debian/Ubuntu):
   ```bash
   sudo apt-get install -y cifs-utils
   ```

2. **Create a credentials file** (fill in the real values — the K: share's
   server name/IP, share name, and an AD/local account with read-write access):
   ```bash
   sudo tee /etc/cifs-kdrive.cred >/dev/null <<'EOF'
   username=SERVICE_ACCOUNT
   password=PASSWORD
   domain=DOMAIN_IF_ANY
   EOF
   sudo chmod 600 /etc/cifs-kdrive.cred
   ```

3. **Find the UNC path** the Windows machines map K: to — on any Windows box
   that has K:, run `net use` in cmd; it shows something like
   `\\fileserver\Media`. That becomes `//fileserver/Media` below.

4. **Add the fstab entry** (systemd automount so a slow/offline share never
   hangs boot; `nofail` so the Bee still boots without it):
   ```bash
   sudo mkdir -p /mnt/k-drive
   echo '//FILESERVER/SHARE /mnt/k-drive cifs credentials=/etc/cifs-kdrive.cred,uid=1000,gid=1000,file_mode=0664,dir_mode=0775,iocharset=utf8,nofail,x-systemd.automount,_netdev 0 0' | sudo tee -a /etc/fstab
   sudo systemctl daemon-reload
   sudo mount /mnt/k-drive
   ```
   (`uid`/`gid` should match the account the app/container runs as.)

5. **Verify**:
   ```bash
   ls "/mnt/k-drive/Programming/! Crossings TV"
   ls "/mnt/k-drive/Traffic/logs"
   touch /mnt/k-drive/Traffic/logs/.rw-test && rm /mnt/k-drive/Traffic/logs/.rw-test
   ```
   The write test matters: the traffic-log air-time fill writes `.xlsm` files
   back to the share.

6. **App-level check**: with the container running
   (`docker-compose.yml` already mounts `/mnt/k-drive:/mnt/k`), open
   `/master-control/daily-programming` and confirm the grid lineup loads.

## Caveats

- **Read-write is required** for `Traffic/logs` (air-time fill saves the
  workbook in place). If someone has that `.xlsm` open in Excel, the SMB lock
  can make the save fail — retry after they close it.
- **Case sensitivity**: filename globs (`*.xlsx`) match case-sensitively on
  Linux. A grid saved with an uppercase `.XLSX` extension will be missed —
  keep extensions lowercase on the share.
- **Compile Logs stays on Windows for now**: the "Compile Logs" page runs the
  `BillingMacro` VBA inside each log workbook via Excel COM (PowerShell). That
  cannot run on Linux; it is deliberately out of scope for this setup (planned
  separately — either a small Windows Excel-worker agent or a Python port of
  the macro).

## WSL dev note (this repo's dev machine)

WSL does not auto-mount mapped network drives. To test the Linux code paths
locally:
```bash
sudo mount -t drvfs 'K:' /mnt/k
```
(add `K: /mnt/k drvfs defaults,nofail 0 0` to `/etc/fstab` in WSL to persist).
