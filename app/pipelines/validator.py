"""Definition validation: returns readable error strings, never raises for
bad user data (the caller decides whether errors are fatal)."""
from __future__ import annotations

from typing import Callable

from app.pipelines import schema


def validate(definition: dict, resolver: Callable[[str], dict | None] | None = None) -> list[str]:
    """Check one definition. `resolver(name)` returns another definition (or
    None) so SUB_PIPELINE / START_PIPELINE references can be checked."""
    errors: list[str] = []
    if not isinstance(definition, dict):
        return ["A pipeline definition must be an object"]
    name = definition.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("The pipeline needs a name")
    if definition.get("type", "CUSTOM") not in schema.PIPELINE_TYPES:
        errors.append(f"Unknown pipeline type {definition.get('type')!r}")
    elements = definition.get("elements")
    if not isinstance(elements, list) or not elements:
        return errors + ["The pipeline needs at least one element"]

    names: list[str] = []
    for n, el in enumerate(elements, start=1):
        label = f"Element {n}"
        if not isinstance(el, dict):
            errors.append(f"{label} must be an object")
            continue
        ename = el.get("name")
        if not isinstance(ename, str) or not ename.strip():
            errors.append(f"{label} needs a name")
            continue
        label = f"Element {ename!r}"
        if ename in names:
            errors.append(f"{label} is defined twice")
        names.append(ename)
        errors += _check_element(label, el)

    known = set(names)
    graph: dict[str, set[str]] = {}
    for el in (e for e in elements if isinstance(e, dict) and isinstance(e.get("name"), str)):
        deps = el.get("depends_on") or []
        if not isinstance(deps, list):
            errors.append(f"Element {el['name']!r}: depends_on must be a list of names")
            continue
        for dep in deps:
            if dep not in known:
                errors.append(f"Element {el['name']!r} depends on unknown element {dep!r}")
            elif dep == el["name"]:
                errors.append(f"Element {el['name']!r} depends on itself")
        graph[el["name"]] = {d for d in deps if d in known and d != el["name"]}
        comp = el.get("compensation") or {}
        step = comp.get("step") if isinstance(comp, dict) else None
        if step is not None and step not in known:
            errors.append(f"Element {el['name']!r}: compensation step {step!r} does not exist")
        errors += _check_references(el, resolver)

    cycle = _find_cycle(graph)
    if cycle:
        errors.append("Dependency cycle: " + " -> ".join(cycle))
    return errors


def _check_element(label: str, el: dict) -> list[str]:
    errors = []
    etype = el.get("type")
    if etype not in schema.ELEMENT_TYPES:
        return [f"{label} has unknown type {etype!r}"]
    if el.get("phase", "MAIN") not in schema.PHASES:
        errors.append(f"{label} has unknown phase {el.get('phase')!r}")
    if el.get("enabled", "ENABLED") not in schema.ENABLEMENT:
        errors.append(f"{label} has unknown enablement {el.get('enabled')!r}")
    config = el.get("config", {})
    if not isinstance(config, dict):
        return errors + [f"{label}: config must be an object"]
    for group in schema.REQUIRED_CONFIG.get(etype, ()):
        if not any(config.get(key) not in (None, "") for key in group):
            errors.append(f"{label} ({etype}) needs config: {' or '.join(group)}")
    comp = el.get("compensation", {})
    if not isinstance(comp, dict):
        return errors + [f"{label}: compensation must be an object"]
    action = comp.get("action", "STOP")
    if action not in schema.COMPENSATION_ACTIONS:
        errors.append(f"{label} has unknown compensation action {action!r}")
    if action == "RUN_STEP" and not comp.get("step"):
        errors.append(f"{label}: RUN_STEP compensation needs a 'step'")
    if action == "LOOP":
        if not comp.get("step"):
            errors.append(f"{label}: LOOP compensation needs a 'step' to return to")
        limit = comp.get("max_loops")
        if not isinstance(limit, int) or not 1 <= limit <= schema.MAX_LOOPS_LIMIT:
            errors.append(f"{label}: LOOP needs max_loops between 1 and {schema.MAX_LOOPS_LIMIT}")
    if action == "START_PIPELINE" and not comp.get("pipeline"):
        errors.append(f"{label}: START_PIPELINE compensation needs a 'pipeline'")
    if "attempts" in comp and (not isinstance(comp["attempts"], int) or comp["attempts"] < 1):
        errors.append(f"{label}: attempts must be a positive integer")
    if comp.get("backoff", "fixed") not in schema.BACKOFFS:
        errors.append(f"{label}: backoff must be one of {', '.join(schema.BACKOFFS)}")
    return errors


def _check_references(el: dict, resolver) -> list[str]:
    if resolver is None:
        return []
    errors = []
    refs = []
    if el.get("type") == "SUB_PIPELINE":
        refs.append(((el.get("config") or {}).get("pipeline"), "sub-pipeline"))
    comp = el.get("compensation") or {}
    if isinstance(comp, dict) and comp.get("action") == "START_PIPELINE":
        refs.append((comp.get("pipeline"), "compensation pipeline"))
    for ref, what in refs:
        if ref and resolver(ref) is None:
            errors.append(f"Element {el['name']!r}: {what} {ref!r} does not exist")
    return errors


def _find_cycle(graph: dict[str, set[str]]) -> list[str]:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str]:
        state[node] = 1
        stack.append(node)
        for dep in sorted(graph.get(node, ())):
            if state.get(dep) == 1:
                return stack[stack.index(dep):] + [dep]
            if dep not in state:
                found = visit(dep)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return []

    for node in sorted(graph):
        if node not in state:
            found = visit(node)
            if found:
                return found
    return []


def topological_order(elements: list[dict]) -> list[dict]:
    """Stable topological order: sequence order wins wherever dependencies allow."""
    by_name = {e["name"]: e for e in elements}
    placed: list[dict] = []
    done: set[str] = set()
    pending = list(elements)
    while pending:
        for el in pending:
            if all(d in done or d not in by_name for d in el.get("depends_on") or []):
                placed.append(el)
                done.add(el["name"])
                pending.remove(el)
                break
        else:
            raise ValueError("Dependency cycle")
    return placed
