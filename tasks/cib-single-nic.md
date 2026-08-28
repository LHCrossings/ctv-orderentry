# CIB single-NIC cleanup — runbook (drafted 2026-08-28, read-only survey)

## Why
Every CIB has two NICs with tied interface metrics (CIB01 already fixed to 300):
- Primary NIC, subnet 10.0.0.192/26 "Res-subnet" → route 0.0.0.0/0 via NAT gateway → internet OK.
- "WAN" NIC, subnet 10.0.0.0/26 "mgmt-subnet" → route 0.0.0.0/0 via IGW, but NO public IP → every
  internet-bound flow hashed onto it is black-holed. Half the SSM/credential/Windows flows die.
Symptoms: SSM "Online but never executes", CredentialRefresher errors all day, agent falling back to
ec2messages (the AWS Health notice lists exactly CIB05 + CIB06), July "random off-air" noise.
History (Lee): WAN NICs were added so Comcast could SRT-call the CIBs directly; superseded by the
Haivision gateway (10.0.0.32). MediaLive outputs only go to 10.0.0.32 now. WAN NICs are unused.

## Inventory
| Box | Instance | Primary | WAN ENI (delete) | WAN metric |
|---|---|---|---|---|
| CIB01 | i-0ff950a64257f46cb | 10.0.0.248 | 10.0.0.62 "CIB01 WAN" | 300 (fixed) |
| CIB03 | i-0d991ada339ad8750 | 10.0.0.218 | 10.0.0.28 "CIB03 WAN" | tie |
| CIB04 | i-047c0def44d5a1a09 | 10.0.0.203 | 10.0.0.21 "CIB04 WAN" | tie |
| CIB05 | i-0e5fd27b8b08e8112 | 10.0.0.238 | 10.0.0.55 "CIB05 Egress" | tie 15/15 |
| CIB06 | i-0eb6756aa1edcea83 | 10.0.0.224 | 10.0.0.20 "CIB WAN" | tie 20/20 |
Orphans to delete too: "CIB02 WAN" 10.0.0.43 + 10.0.0.51, "vpn-nic" 10.0.0.18 (confirm unused).

## Dependency check results (all clear, with caveats)
- Primary-NIC SG `DVR-CIB-Datamover-SG` allows ALL from 0.0.0.0/0 → nothing inbound is lost.
- Nothing binds to the WAN IP except mDNS 5353 (binds every NIC). All WAN-NIC traffic is
  intra-VPC (SQL .146, Jumpbox .45 :5959/5961, DFSR .59, SMB from Datamover .199) = tie spray.
- Etere master `RESOURCE_INI` (COD_USER 1, modified 7/23): every ETX-NN / LOGO-NN IPADDRESS is
  already the PRIMARY IP. ✔
- ⚠ STALE per-user caches still carry WAN IPs: Jumpbox `usrjp` user.011 (ETX-01/05/06),
  Datamover `lee.hudson` user.001 (ETX-01/03/04/05), DC `administrator.CTVETERE` user.001.
  Jumpbox is LIVE-connected to CIB04 on 10.0.0.21:5961 from that cache. Ashley's caches are fine.
  → Need to learn whether Etere re-pulls `resource\*\device.ini` from RESOURCE_INI at app start;
    verify on box #1 before touching the rest.
- ⚠ `NFY_SUBSCRIPTIONS.CLIENT_IP` = 10.0.0.62 for CIB01 (created 8/26 21:30, deadline 9/2):
  CIB01's own Etere apps registered from the WAN IP. After removal, SCHEDULE_CHANGED pushes to
  .62 fail until re-subscribe. Other CIBs registered from primary. `WORKSTATIONS.IPADDRESS` also
  .62 for CIB01 (cosmetic/licensing? verify Etere still licensed after change).
- DNS CTVEtere.local has dual A records (dynamic, WAN ones dated 7/20-7/21) → delete WAN A records.
  hosts files on Jumpbox + Datamover already pin every CIB to its primary IP.
- MediaLive inputs are RTP_PUSH to 10.0.0.x endpoints (VPC-local) — source NIC irrelevant.

## Per-box procedure (one box at a time; verify before next)
Window: KL afternoon = US late night; AVOID 18:00-21:00 KL (06:00 rollovers ET/CT/PT).
0. Snapshot: `Get-NetIPInterface`, `Get-NetRoute`, ENI id, SG list → scratch file.
1. DRY RUN (reversible, instant): `Set-NetIPInterface -InterfaceAlias "<WAN>" -InterfaceMetric 300`.
   Wait 10 min: SSM command executes first try; agent log shows no new CredentialRefresher error;
   Etere Air Control/EE still controls the channel; MLFD flat.
2. `aws ec2 detach-network-interface --attachment-id <att>` (hot-detach OK). Windows drops the NIC.
3. Verify: `Get-NetAdapter` single NIC; `Get-NetRoute 0.0.0.0/0` single default; IMDS token PUT x5 OK;
   SSM round-trip OK; Etere control OK; playout OK.
4. DNS: `Remove-DnsServerResourceRecord -ZoneName CTVEtere.local -RRType A -Name <host> -RecordData <wanip>` on DC.
5. Etere: on workstations with stale caches, restart Etere apps (or clear `resource\ETX-NN`) and
   confirm device.ini shows primary IP. On the CIB, if NFY_SUBSCRIPTIONS still shows WAN IP,
   restart Etere Au at a safe moment so it re-subscribes from primary.
6. Next day: MLFD scan (`mlfd_scan.ps1`) + agent-log check; then `aws ec2 delete-network-interface`.
7. After all five: delete orphan ENIs; drop "SRT Streams SG" from CIB06 primary if unneeded;
   Health event should flip to Resolved ~7 days later.

## Suggested order
CIB01 (metric already 300 → lowest risk, proves the Etere cache/NFY mechanics) → CIB06 → CIB05
(the two flagged) → CIB03 → CIB04.

## CIB01 test-bench plan (Lee, 8/28)
NYC + WDC overnight (00:00–06:00 ET = 12:00–18:00 KL): Lee re-routes Haivision to deliver alternate
content to those two networks, CIB01 goes offline, we do the full procedure with reboots/tests as
needed, then hand back. Must be back on air before the 06:00 ET rollover / Aligner burst (18:00 KL)
— target hand-back ≤17:30 KL. Use CIB01 to settle: Etere device.ini cache refresh behaviour,
NFY_SUBSCRIPTIONS re-registration, DNS cleanup, and the SSM/IMDS error rate afterwards.
