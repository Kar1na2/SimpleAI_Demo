#!/usr/bin/env bash
# Start a scripted call on the running backend and print its live link (open it on your Mac).
#
#   scripts/live-call.sh [order|reservation] [manual|auto] [speed]
#   make live S=order MODE=manual SPEED=1
#
# manual: you tap Confirm on the link page yourself (the call waits up to 180s for it).
# auto:   the scripted customer taps Confirm by itself.
# The link URL uses PUBLIC_BASE_URL (default http://<tailscale-ip>:5180).
set -euo pipefail

SCENARIO=${1:-order}
MODE=${2:-manual}
SPEED=${3:-1}
API=${API:-http://127.0.0.1:8000}

json() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }

call_id=$(curl -fsS -X POST "$API/api/calls/start" -H 'content-type: application/json' \
  -d "{\"scenario\":\"$SCENARIO\",\"mode\":\"$MODE\",\"speed\":$SPEED}" | json '["call_id"]')

url=""
for _ in $(seq 1 150); do # the token is minted at `connected` (a few seconds after start)
  if url=$({ curl -fsS "$API/api/dev/calls/$call_id/link" | json '["url"]'; } 2>/dev/null); then break; fi
  sleep 0.1
done
[ -n "$url" ] || { echo "no link after 15s (call $call_id)" >&2; exit 1; }

base=${url%%/s/*}
echo "call:   $call_id ($SCENARIO, $MODE, speed $SPEED)"
echo "link:   $url"
echo "events: $base/dev/events/$call_id"
echo "Open the link once the agent says it's sent (opening it earlier logs 'You opened this link' early)."
