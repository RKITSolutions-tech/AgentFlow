"""Pipeline definition format (docs/PIPELINE_ENGINE.md §2-§6, §14).

A definition is a plain JSON-compatible dict, stored relationally per version
in SQLite (persistence.py). Shape::

    {
      "name": "web-feature", "type": "DEVELOPMENT", "description": "...",
      "elements": [
        {"name": "start_app", "type": "PROCESS_START", "phase": "SETUP",
         "config": {"command": "flask run"},
         "compensation": {"action": "STOP_AND_MESSAGE"}},
        {"name": "tests", "type": "TEST", "depends_on": ["implement"],
         "config": {"command": "pytest -q"},
         "compensation": {"action": "LOOP", "step": "implement", "max_loops": 3}}
      ]
    }

JSON, not YAML, because PyYAML is not a dependency; YAML import/export can
layer on later (§3).
"""
from __future__ import annotations

PIPELINE_TYPES = ("SETUP", "DEVELOPMENT", "VERIFICATION", "TEARDOWN", "RIGGING", "CUSTOM")
PHASES = ("SETUP", "MAIN", "TEARDOWN")  # SETUP first, TEARDOWN runs finally-style (§15)
ENABLEMENT = ("ENABLED", "DISABLED", "SKIPPED")

DEVELOPMENT_TYPES = ("COMMAND", "AGENT", "TEST", "PLAYWRIGHT", "SCREENSHOT", "GIT", "ACCEPTANCE")
RIGGING_TYPES = (
    "PROCESS_START", "PROCESS_STOP", "HEALTHCHECK", "WAIT", "HTTP_REQUEST", "SSH_COMMAND",
    "DOCKER_COMMAND", "DOCKER_COMPOSE", "FILE_CHECK", "FILE_OPERATION", "ARTIFACT_CAPTURE",
)
HUMAN_TYPES = ("MANUAL_APPROVAL", "MANUAL_INPUT", "MANUAL_REVIEW")
COMPOSITION_TYPES = ("SUB_PIPELINE",)
ELEMENT_TYPES = DEVELOPMENT_TYPES + RIGGING_TYPES + HUMAN_TYPES + COMPOSITION_TYPES

COMPENSATION_ACTIONS = ("STOP", "STOP_AND_MESSAGE", "CONTINUE", "RUN_STEP", "LOOP", "START_PIPELINE")
BACKOFFS = ("fixed", "exponential")
MAX_LOOPS_LIMIT = 20
MAX_COMPOSE_DEPTH = 5

# Config keys an element type must carry; at least one of each group.
REQUIRED_CONFIG: dict[str, tuple[tuple[str, ...], ...]] = {
    "COMMAND": (("command",),),
    "TEST": (("command",),),
    "PLAYWRIGHT": (("command",),),
    "PROCESS_START": (("command",),),
    "PROCESS_STOP": (("process", "command"),),
    "DOCKER_COMMAND": (("command",),),
    "SSH_COMMAND": (("command",),),
    "AGENT": (("prompt_template", "prompt"),),
    "HEALTHCHECK": (("url", "command"),),
    "HTTP_REQUEST": (("url",),),
    "WAIT": (("seconds",),),
    "FILE_CHECK": (("path",),),
    "MANUAL_APPROVAL": (("prompt",),),
    "MANUAL_INPUT": (("prompt",),),
    "MANUAL_REVIEW": (("prompt",),),
    "SUB_PIPELINE": (("pipeline",),),
}


def category(element_type: str) -> str:
    if element_type in RIGGING_TYPES:
        return "RIGGING"  # rigging must look different from development work (§5)
    if element_type in HUMAN_TYPES:
        return "HUMAN"
    if element_type in COMPOSITION_TYPES:
        return "COMPOSITION"
    return "DEVELOPMENT"
