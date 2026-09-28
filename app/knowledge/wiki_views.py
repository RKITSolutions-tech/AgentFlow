"""Wiki UI routes (task 50.4): slug-based browsing and search over the same
`knowledge_entries` rows the /knowledge admin UI (app/knowledge/views.py)
manages -- see app/db.py's SCHEMA comment for why this is a second view onto
one table rather than a second store."""
from __future__ import annotations

from flask import Blueprint, abort, redirect, render_template, request, url_for

from app.db import get_db
from app.knowledge import maintenance, models, search

bp = Blueprint("wiki", __name__, url_prefix="/wiki")


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


@bp.get("")
def index():
    db = get_db()
    query = request.args.get("q", "").strip()
    filters = {
        "kind": request.args.get("kind") or None,
        "language": request.args.get("language") or None,
        "library": request.args.get("library") or None,
        "version": request.args.get("version") or None,
        "confidence": request.args.get("confidence") or "reviewed",
    }
    searching = bool(query) or any(v for k, v in filters.items() if k != "confidence")
    results = None
    page = max(1, request.args.get("page", 1, type=int))
    page_size = 20
    has_more = False
    if searching:
        searcher = search.KnowledgeSearch()
        results = searcher.search(db, query, limit=page_size + 1, offset=(page - 1) * page_size, **filters)
        has_more = len(results) > page_size
        results = results[:page_size]
    tree = models.list_topics(db)
    recent = [e for e in models.list_entries(db, confidence="reviewed", limit=20) if e.slug][:8]
    pending_review = len(models.list_review_queue(db, status="pending"))
    return render_template(
        "knowledge/wiki_index.html", tree=tree, results=results, query=query,
        filters=filters, recent=recent, pending_review=pending_review, page=page, has_more=has_more,
    )


@bp.get("/review-queue")
def review_queue():
    db = get_db()
    items = models.list_review_queue(db, status="pending")
    report = maintenance.generate_report(db)
    return render_template("knowledge/needs_review_queue.html", items=items, report=report)


@bp.post("/review-queue/<int:item_id>/approve")
def approve_review_item(item_id: int):
    db = get_db()
    item = models.get_review_item(db, item_id)
    if item is None or item.status != "pending":
        if _wants_json():
            return {"error": "Not found or already resolved"}, 404
        abort(404)
    try:
        _apply_review_item(db, item)
    except ValueError as exc:
        if _wants_json():
            return {"error": str(exc)}, 400
        raise
    models.resolve_review_item(db, item_id, "approved", "user")
    if _wants_json():
        return {"status": "success", "message": "Change applied"}
    return redirect(url_for("wiki.review_queue"))


@bp.post("/review-queue/<int:item_id>/reject")
def reject_review_item(item_id: int):
    db = get_db()
    item = models.get_review_item(db, item_id)
    if item is None or item.status != "pending":
        if _wants_json():
            return {"error": "Not found or already resolved"}, 404
        abort(404)
    models.resolve_review_item(db, item_id, "rejected", "user")
    if _wants_json():
        return {"status": "success"}
    return redirect(url_for("wiki.review_queue"))


def _apply_review_item(db, item: models.ReviewItem) -> None:
    if item.change_type == "create":
        models.create_entry(
            db, "note", item.title or item.slug or "Untitled", item.content or "",
            slug=item.slug, confidence="unverified",
        )
    elif item.change_type == "update" and item.entry_id:
        models.update_entry(db, item.entry_id, title=item.title, content=item.content)
    elif item.change_type == "delete" and item.entry_id:
        models.delete_entry(db, item.entry_id)


@bp.get("/<slug>/preview")
def preview(slug: str):
    entry = models.get_entry_by_slug(get_db(), slug)
    if entry is None:
        return {"error": "not found"}, 404
    return {"slug": entry.slug, "title": entry.title, "snippet": (entry.content or "")[:200]}


@bp.post("/<slug>/propose")
def propose(slug: str):
    db = get_db()
    entry = models.get_entry_by_slug(db, slug)
    title = request.form.get("title", "")
    content = request.form.get("content", "")
    reason = request.form.get("reason", "")
    models.propose_change(
        db, "update" if entry else "create", slug=slug,
        entry_id=entry.id if entry else None, title=title, content=content, reason=reason,
    )
    if _wants_json():
        return {"status": "success", "message": "Proposal submitted for review"}
    return redirect(url_for("wiki.view_entry", slug=slug))


@bp.get("/<slug>")
def view_entry(slug: str):
    db = get_db()
    entry = models.get_entry_by_slug(db, slug)
    if entry is None:
        abort(404)
    models.log_read(db, entry.id, context="wiki_ui")
    entry = models.get_entry(db, entry.id)  # re-fetch: use_count/last_read_at just changed
    backlinks = models.list_backlinks(db, slug)
    forward = [f for f in (models.get_entry_by_slug(db, s) for s in models.list_forward_links(db, entry.id)) if f]
    return render_template("knowledge/wiki_entry.html", entry=entry, backlinks=backlinks, forward=forward)
