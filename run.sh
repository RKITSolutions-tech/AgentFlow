#!/bin/bash
# Start the AgentFlow Flask development server

source venv/bin/activate
flask --app app --debug run --host 0.0.0.0
