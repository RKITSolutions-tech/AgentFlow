"""Built-in prompt content. It is *seeded* into the library at start-up; at
runtime the engine and Ralph read the library, so an edit there takes effect
and these constants are only the fallback for a library that was never seeded."""

DEFAULT_TEMPLATES = {
    "implement-task": (
        "Implement the task described below. Make the smallest change that "
        "satisfies the acceptance criteria, then stop.\n\n${vars.task}"
    ),
    "verify-acceptance": (
        "Verify each acceptance criterion below against the current code and "
        "report which pass.\n\n${vars.task}"
    ),
}

DEFAULT_INSTRUCTIONS = (
    ("keep-minimal", "Work only on the task below and keep changes minimal."),
    ("extend-existing", "Extend existing code rather than replacing it."),
    ("no-commit", "Do not commit; AgentFlow commits after verification passes."),
    ("stop-when-done", "Stop when you believe the acceptance criteria are met; verification decides."),
)
