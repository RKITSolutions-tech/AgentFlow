#!/bin/bash
# Start the AgentFlow dev instance, separate from the prod instance that
# runs continuously as the agentflow.service systemd unit on port 5000
# against instance/agentflow.sqlite3. This defaults to its own port and its
# own database/artifacts (both under instance/, gitignored) so that
# starting/restarting/breaking the dev server while iterating never touches
# real project data, and the two can run side by side.
#
# Goes through wsgi.py, not `flask run`: `flask run --host ...` binds
# Werkzeug directly and never consults app.config["HOST"], so it bypassed the
# loopback-only bind check in app/config.py's Config.from_env() entirely.
# wsgi.py uses app.config["HOST"]/["PORT"], which does run that check --
# reach this beyond loopback by setting AGENTFLOW_HOST and
# AGENTFLOW_ALLOW_UNSAFE_BIND=1 (docs/HIGH_LEVEL_DESIGN.md section 20).

source venv/bin/activate
AGENTFLOW_PORT="${AGENTFLOW_PORT:-5050}" \
AGENTFLOW_DATABASE_PATH="${AGENTFLOW_DATABASE_PATH:-instance/dev.sqlite3}" \
AGENTFLOW_ARTIFACT_DIR="${AGENTFLOW_ARTIFACT_DIR:-instance/dev-artifacts}" \
FLASK_DEBUG=1 python wsgi.py
