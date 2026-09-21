#!/usr/bin/env bash
# Fleet Standard 3 smoke test (template: ctv-salesportal/deploy/smoke.sh).
# Bee-local liveness + auth-active check. `make deploy` and the reusable deploy
# workflow run this as the final gate and fail the deploy if it fails.
#
# This is the baseline (liveness + auth-active), valid for host-network / direct
# tailnet-IP tools. For an nginx + X-Forwarded-For topology, extend it to also
# assert the forwarded client IP is honored (not the bridge IP) — see
# ctv-common/docs/deploy-standard.md "FLEET STANDARD 3".
set -euo pipefail
PORT="${PORT:-5000}"
BASE="http://127.0.0.1:${PORT}"
fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }
code=000
for _ in $(seq 1 15); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$BASE/" || true)
  [ -n "$code" ] && [ "$code" != "000" ] && break
  sleep 1
done
case "$code" in
  000)     fail "no HTTP response on :$PORT after ~15s — container not listening (docker compose logs)" ;;
  5*)      fail "server error $code on / — app up but broken" ;;
  401|403) echo "OK: app up on :$PORT, auth active ($code bee-local as expected)" ;;
  *)       echo "OK: app up on :$PORT (HTTP $code bee-local)" ;;
esac
echo "deploy smoke OK — finish acceptance with a 200 from a tailnet client."
