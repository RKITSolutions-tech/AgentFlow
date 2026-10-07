#!/bin/bash
# Run this remote instance in the foreground (Ctrl-C to stop). For something
# that survives logout/reboot, use agentflow-remote.service instead.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="$SCRIPT_DIR/agentflow.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE -- run deploy/remote-instance/setup.sh first." >&2
  exit 1
fi

cd "$REPO_ROOT"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
# shellcheck disable=SC1091
source venv/bin/activate
exec python wsgi.py
