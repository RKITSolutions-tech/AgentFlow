#!/bin/bash
# Bootstrap a *remote* AgentFlow instance (the one a master instance will
# federate with) -- see docs/REMOTE_INSTANCE_SETUP.md for the full walkthrough.
#
# Run this from inside the AgentFlow repo you already cloned on the remote
# machine (ideally inside WSL2 if that machine is Windows -- see the doc for
# why). It is idempotent: safe to re-run after pulling updates.
#
# Usage: deploy/remote-instance/setup.sh <host-address> [allowed-root ...]
#   host-address  This machine's private-network address (Tailscale IP or
#                 LAN hostname) that the master instance will connect to.
#   allowed-root  One or more directories this instance may open as a
#                 Project. Defaults to the parent of this repo checkout.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="$SCRIPT_DIR/agentflow.env"

HOST="${1:-}"
if [ -z "$HOST" ]; then
  echo "Usage: $0 <host-address> [allowed-root ...]" >&2
  echo "  <host-address> is this machine's Tailscale/LAN address -- not localhost." >&2
  exit 1
fi
shift || true
ALLOWED_ROOTS=("$@")
if [ "${#ALLOWED_ROOTS[@]}" -eq 0 ]; then
  ALLOWED_ROOTS=("$(dirname "$REPO_ROOT")")
fi
ALLOWED_ROOTS_JOINED=$(IFS=:; echo "${ALLOWED_ROOTS[*]}")

cd "$REPO_ROOT"

echo "==> Python environment"
if [ ! -d venv ]; then
  python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo "==> Federation token"
if [ -f "$ENV_FILE" ] && grep -q '^AGENTFLOW_FEDERATION_TOKEN=.\+' "$ENV_FILE"; then
  # Re-running setup.sh must not rotate the token out from under an already
  # registered master -- reuse whatever is already on disk.
  TOKEN=$(grep '^AGENTFLOW_FEDERATION_TOKEN=' "$ENV_FILE" | cut -d= -f2-)
  echo "Reusing existing token from $ENV_FILE"
else
  if command -v openssl >/dev/null 2>&1; then
    TOKEN=$(openssl rand -hex 32)
  else
    TOKEN=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
  fi
  echo "Generated a new federation token."
fi

echo "==> Writing $ENV_FILE"
cat > "$ENV_FILE" <<EOF
AGENTFLOW_HOST=$HOST
AGENTFLOW_ALLOW_UNSAFE_BIND=1
AGENTFLOW_PORT=5000
AGENTFLOW_FEDERATION_TOKEN=$TOKEN
AGENTFLOW_ALLOWED_ROOTS=$ALLOWED_ROOTS_JOINED
AGENTFLOW_DATABASE_PATH=instance/agentflow.sqlite3
EOF

GENERATED_UNIT="$SCRIPT_DIR/agentflow-remote.generated.service"
echo "==> Writing $GENERATED_UNIT"
sed \
  -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
  -e "s|__ENV_FILE__|$ENV_FILE|g" \
  -e "s|__USER__|$(whoami)|g" \
  "$SCRIPT_DIR/agentflow-remote.service" > "$GENERATED_UNIT"

echo
echo "Done. Next steps:"
echo "1. Start this instance, either:"
echo "     deploy/remote-instance/run-foreground.sh        # foreground, Ctrl-C to stop"
echo "   or, to survive logout/reboot (systemd; WSL2 needs 'systemd=true' in wsl.conf):"
echo "     sudo cp $GENERATED_UNIT /etc/systemd/system/agentflow-remote.service"
echo "     sudo systemctl enable --now agentflow-remote"
echo "2. Open http://$HOST:5000/ on this machine, create a Project, and add"
echo "   a repository under one of: ${ALLOWED_ROOTS[*]}"
echo "3. On the master instance, add this as a Remote Instance:"
echo "     Base URL: http://$HOST:5000"
echo "     Token:    $TOKEN"
