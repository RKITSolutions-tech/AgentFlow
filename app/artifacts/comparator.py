"""Artifact comparison: unified text diffs for text kinds, and a byte/size
comparison for images (a pixel diff needs an imaging library, which is not a
dependency)."""
from __future__ import annotations

import difflib

from app.artifacts import collector, models

MAX_DIFF_LINES = 2000
MAX_TEXT_BYTES = 512 * 1024


class ComparisonError(ValueError):
    """The two artifacts cannot be compared."""


def compare(root: str, a: models.Artifact, b: models.Artifact) -> tuple[str, dict]:
    if a.id == b.id:
        raise ComparisonError("Choose two different artifacts")
    if a.kind in models.KINDS and a.kind == b.kind and a.kind in collector.TEXT_KINDS:
        return "text_diff", _text(root, a, b)
    if a.kind == b.kind == "screenshot":
        return "image_compare", _image(root, a, b)
    raise ComparisonError(f"Cannot compare a {a.kind} with a {b.kind}")


def _read(root: str, artifact: models.Artifact, limit: int) -> bytes:
    with open(collector.file_path(root, artifact), "rb") as fh:
        return fh.read(limit + 1)


def _text(root: str, a, b) -> dict:
    raw_a, raw_b = _read(root, a, MAX_TEXT_BYTES), _read(root, b, MAX_TEXT_BYTES)
    truncated = len(raw_a) > MAX_TEXT_BYTES or len(raw_b) > MAX_TEXT_BYTES
    lines_a = raw_a[:MAX_TEXT_BYTES].decode("utf-8", "replace").splitlines()
    lines_b = raw_b[:MAX_TEXT_BYTES].decode("utf-8", "replace").splitlines()
    diff = list(difflib.unified_diff(lines_a, lines_b, f"#{a.id} {a.name}", f"#{b.id} {b.name}", lineterm=""))
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    return {
        "identical": not diff, "added": added, "removed": removed, "truncated": truncated,
        "diff": diff[:MAX_DIFF_LINES], "diff_truncated": len(diff) > MAX_DIFF_LINES,
    }


def _image(root: str, a, b) -> dict:
    data_a, data_b = _read(root, a, collector.MAX_ARTIFACT_BYTES), _read(root, b, collector.MAX_ARTIFACT_BYTES)
    size_a, size_b = collector.image_size(data_a), collector.image_size(data_b)
    differing = sum(1 for x, y in zip(data_a, data_b) if x != y) + abs(len(data_a) - len(data_b))
    return {
        "identical": data_a == data_b,
        "size_a": list(size_a) if size_a else None,
        "size_b": list(size_b) if size_b else None,
        "same_dimensions": size_a == size_b and size_a is not None,
        "bytes_a": len(data_a), "bytes_b": len(data_b), "differing_bytes": differing,
        "note": "Byte-level comparison; pixel diffing needs an imaging library.",
    }
