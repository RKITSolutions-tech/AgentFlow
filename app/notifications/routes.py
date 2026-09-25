from __future__ import annotations

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from app.db import get_db
from app.notifications import models

bp = Blueprint("notifications", __name__, url_prefix="/notifications")


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _as_dict(n: models.Notification) -> dict:
    return {
        "id": n.id,
        "kind": n.kind,
        "message": n.message,
        "url": url_for("sessions.view_session", session_id=n.session_id),
    }


@bp.get("")
def index():
    db = get_db()
    return render_template(
        "notifications/index.html",
        notifications=models.list_notifications(db),
        preferences=models.get_preferences(db),
        kinds=models.KINDS,
        channels=models.CHANNELS,
    )


@bp.get("/poll")
def poll():
    """Unread count, plus browser notifications to show if ``deliver=1``.

    Pages only pass ``deliver=1`` when the browser has granted permission, so
    a denied or undecided browser leaves them undelivered.
    """
    db = get_db()
    shown = models.take_undelivered_browser(db) if request.args.get("deliver") == "1" else []
    return jsonify({"unread": models.unread_count(db), "browser": [_as_dict(n) for n in shown]})


@bp.post("/<int:notification_id>/read")
def read(notification_id: int):
    db = get_db()
    if models.get_notification(db, notification_id) is None:
        return jsonify({"error": "Notification not found"}), 404
    models.mark_read(db, notification_id)
    return jsonify({"status": "read", "unread": models.unread_count(db)})


@bp.post("/read-all")
def read_all():
    db = get_db()
    models.mark_all_read(db)
    if _wants_json():
        return jsonify({"status": "read", "unread": 0})
    return redirect(url_for("notifications.index"))


@bp.post("/preferences")
def save_preferences():
    """Save the checkbox grid: a checked ``<kind>:<channel>`` box is enabled."""
    db = get_db()
    checked = set(request.form.getlist("enabled"))
    for kind in models.KINDS:
        for channel in models.CHANNELS:
            models.set_preference(db, kind, channel, f"{kind}:{channel}" in checked)
    flash("Notification preferences saved.", "info")
    return redirect(url_for("notifications.index"))
