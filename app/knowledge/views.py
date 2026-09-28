"""Knowledge base UI routes (task 48)."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for

from app.db import get_db
from app.knowledge import models, search
from app.projects import models as project_models

bp = Blueprint("knowledge", __name__, url_prefix="/knowledge")


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


@bp.get("")
def list_entries():
    """List knowledge entries with filtering."""
    db = get_db()
    kind = request.args.get("kind")
    confidence = request.args.get("confidence", "reviewed")
    query = request.args.get("q", "")
    searcher = search.KnowledgeSearch()
    results = searcher.search(
        db, query, kind=kind, confidence=confidence, limit=50,
    )
    if _wants_json():
        return {
            "entries": [
                {
                    "id": r.entry_id,
                    "title": r.title,
                    "confidence": r.confidence,
                    "use_count": r.use_count,
                }
                for r in results
            ]
        }
    return render_template("knowledge/list.html", results=results, query=query, kind=kind)


@bp.get("/<int:entry_id>")
def view_entry(entry_id: int):
    """View a knowledge entry."""
    db = get_db()
    entry = models.get_entry(db, entry_id)
    if entry is None:
        abort(404)
    provenance = models.list_provenance(db, entry_id)
    return render_template("knowledge/detail.html", entry=entry, provenance=provenance)


@bp.post("/<int:entry_id>/pin")
def pin_entry(entry_id: int):
    """Pin or unpin an entry."""
    db = get_db()
    entry = models.get_entry(db, entry_id)
    if entry is None:
        abort(404)
    models.pin_entry(db, entry_id, not entry.pinned)
    if _wants_json():
        return {"status": "success", "pinned": not entry.pinned}
    return redirect(url_for("knowledge.view_entry", entry_id=entry_id))


@bp.post("/<int:entry_id>/promote")
def promote_entry(entry_id: int):
    """Promote entry from unverified to reviewed."""
    db = get_db()
    entry = models.get_entry(db, entry_id)
    if entry is None:
        abort(404)
    if entry.confidence == "reviewed":
        if _wants_json():
            return {"error": "Already reviewed"}, 409
        return redirect(url_for("knowledge.view_entry", entry_id=entry_id))
    models.promote_entry(db, entry_id, "system")
    if _wants_json():
        return {"status": "success", "confidence": "reviewed"}
    return redirect(url_for("knowledge.view_entry", entry_id=entry_id))


@bp.post("/<int:entry_id>/delete")
def delete_entry(entry_id: int):
    """Delete an entry."""
    db = get_db()
    entry = models.get_entry(db, entry_id)
    if entry is None:
        abort(404)
    models.delete_entry(db, entry_id)
    if _wants_json():
        return {"status": "success"}
    return redirect(url_for("knowledge.list_entries"))
