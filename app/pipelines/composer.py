"""Sub-pipeline composition (docs/PIPELINE_ENGINE.md §4).

A SUB_PIPELINE element references another definition by name. `flatten`
expands references into one element list so the executor only ever sees
plain elements; the reference itself is never copied into storage.
"""
from __future__ import annotations

import copy
from typing import Callable

from app.pipelines import schema
from app.pipelines.validator import topological_order, validate

Resolver = Callable[[str], "dict | None"]


class CompositionError(ValueError):
    """A definition cannot be composed (missing, recursive or too deep)."""


def with_implicit_dependencies(elements: list[dict]) -> list[dict]:
    """Elements with no `depends_on` key follow the previous element of the same
    phase, so a plain list reads as a linear pipeline (§2)."""
    out, previous = [], {}
    for el in copy.deepcopy(elements):
        phase = el.get("phase", "MAIN")
        if "depends_on" not in el:
            el["depends_on"] = [previous[phase]] if phase in previous else []
        previous[phase] = el["name"]
        out.append(el)
    return out


def flatten(definition: dict, resolver: Resolver, _stack: tuple[str, ...] = ()) -> list[dict]:
    """Return the definition's elements with sub-pipelines expanded in place.

    Child names become `<parent>.<child>`. Children take the parent's phase; a
    child with no dependencies of its own waits for the parent's dependencies,
    and anything depending on the parent waits for every child. Disabled
    sub-pipeline elements expand to disabled children.
    """
    name = definition.get("name", "")
    if name in _stack:
        raise CompositionError("Recursive sub-pipeline: " + " -> ".join((*_stack, name)))
    if len(_stack) >= schema.MAX_COMPOSE_DEPTH:
        raise CompositionError(f"Sub-pipelines nest deeper than {schema.MAX_COMPOSE_DEPTH} levels")

    flat: list[dict] = []
    expansion: dict[str, list[str]] = {}
    for el in with_implicit_dependencies(definition["elements"]):
        if el["type"] != "SUB_PIPELINE":
            flat.append(el)
            continue
        ref = el["config"]["pipeline"]
        child = resolver(ref)
        if child is None:
            raise CompositionError(f"Sub-pipeline {ref!r} does not exist")
        children = flatten(child, resolver, (*_stack, name))
        local = {c["name"] for c in children}
        prefix = f"{el['name']}."
        for c in children:
            c["name"] = prefix + c["name"]
            c["depends_on"] = [prefix + d for d in c["depends_on"] if d in local]
            comp = c.get("compensation")
            if isinstance(comp, dict) and comp.get("step") in local:
                comp["step"] = prefix + comp["step"]
            if not c["depends_on"]:
                c["depends_on"] = list(el["depends_on"])
            c["phase"] = el.get("phase", "MAIN")
            if el.get("enabled", "ENABLED") != "ENABLED":
                c["enabled"] = el["enabled"]
        expansion[el["name"]] = [c["name"] for c in children]
        flat.extend(children)

    # Anything that depended on a sub-pipeline now depends on all its children.
    for el in flat:
        deps = []
        for d in el["depends_on"]:
            deps.extend(expansion.get(d, [d]))
        el["depends_on"] = deps
    return flat


def compose(definition: dict, resolver: Resolver) -> list[dict]:
    """Validate, flatten and order a definition ready for execution."""
    errors = validate(definition, resolver)
    if errors:
        raise CompositionError("; ".join(errors))
    return topological_order(flatten(definition, resolver))
