#!/bin/bash
# Start the AgentFlow Flask development server.
#
# Goes through wsgi.py, not `flask run`: `flask run --host ...` binds
# Werkzeug directly and never consults app.config["HOST"], so it bypassed the
# loopback-only bind check in app/config.py's Config.from_env() entirely.
# wsgi.py uses app.config["HOST"]/["PORT"], which does run that check --
# reach this beyond loopback by setting AGENTFLOW_HOST and
# AGENTFLOW_ALLOW_UNSAFE_BIND=1 (docs/HIGH_LEVEL_DESIGN.md section 20).

source venv/bin/activate
FLASK_DEBUG=1 python wsgi.py
