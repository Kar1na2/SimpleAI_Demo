#!/usr/bin/env bash
# Print what's needed to open the demo from another machine on the tailnet.
# Read-only: `wslinfo --networking-mode`, `tailscale.exe ip -4`, `tailscale.exe status --json`.
# Never runs tailscale serve/funnel/cert and never changes any Tailscale or WSL configuration.
set -uo pipefail

PORT=${FRONTEND_PORT:-5180}
TS="tailscale.exe"
command -v "$TS" >/dev/null 2>&1 || TS="/mnt/c/Program Files/Tailscale/tailscale.exe"

mode=$(wslinfo --networking-mode 2>/dev/null || echo "unknown (no wslinfo; check 'ip addr')")
ip=$("$TS" ip -4 2>/dev/null | tr -d '\r' | head -n1)
dns=$("$TS" status --json 2>/dev/null | tr -d '\r' | python3 -c \
  "import json,sys; print((json.load(sys.stdin).get('Self') or {}).get('DNSName','').rstrip('.'))" 2>/dev/null)

repo=$(cd "$(dirname "$0")/.." && pwd)
win_path="\\\\wsl.localhost\\${WSL_DISTRO_NAME:-Ubuntu}${repo//\//\\}\\scripts\\windows"

echo "WSL networking mode: $mode"
echo "Tailscale IPv4:      ${ip:-unavailable}${dns:+  ($dns)}"
echo
echo "Expose (elevated PowerShell on Windows; re-run after a WSL restart in NAT mode):"
echo "  powershell -NoProfile -ExecutionPolicy Bypass -File \"$win_path\\expose-demo.ps1\" -Distro ${WSL_DISTRO_NAME:-Ubuntu}"
echo "Undo:"
echo "  powershell -NoProfile -ExecutionPolicy Bypass -File \"$win_path\\unexpose-demo.ps1\""
echo
if [ -n "$ip" ]; then
  echo "On your Mac:"
  echo "  app:          http://$ip:$PORT/   (the customer page is /s/<token>)"
  echo "  screenshots:  http://$ip:$PORT/dev/screenshots"
  [ -n "$dns" ] && echo "  (or http://$dns:$PORT/...)"
  echo "  live call:    make live S=order MODE=manual   -> prints the link to open"
fi
