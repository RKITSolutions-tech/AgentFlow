from __future__ import annotations

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from app.db import get_db
from app.settings import models as settings_models

bp = Blueprint("settings", __name__, url_prefix="/settings")


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


@bp.get("")
def index():
    db = get_db()
    models_by_provider = {
        provider: settings_models.list_models(db, provider)
        for provider in settings_models.PROVIDERS
    }
    return render_template("settings/index.html", models_by_provider=models_by_provider)


@bp.post("/models/add")
def add_model():
    db = get_db()
    provider = request.form.get("provider", "").strip().lower()
    model_id = request.form.get("model_id", "").strip()

    if provider not in settings_models.PROVIDERS:
        flash("Unknown provider.", "error")
    elif not model_id:
        flash("Model id is required.", "error")
    else:
        try:
            settings_models.add_model(db, provider, model_id)
            flash(f"Added {model_id}.", "info")
        except Exception:
            flash(f"{model_id} is already in the catalog.", "error")

    return redirect(url_for("settings.index"))


@bp.post("/models/<int:catalog_id>/toggle")
def toggle_model(catalog_id: int):
    db = get_db()
    entry = settings_models.get_model(db, catalog_id)
    if entry is None:
        if _wants_json():
            return jsonify({"error": "Model not found"}), 404
        return redirect(url_for("settings.index"))

    settings_models.set_model_enabled(db, catalog_id, not entry.enabled)

    if _wants_json():
        return jsonify({"status": "ok", "enabled": not entry.enabled}), 200
    return redirect(url_for("settings.index"))


@bp.post("/models/<int:catalog_id>/delete")
def delete_model(catalog_id: int):
    db = get_db()
    entry = settings_models.get_model(db, catalog_id)
    if entry is None:
        if _wants_json():
            return jsonify({"error": "Model not found"}), 404
        return redirect(url_for("settings.index"))

    settings_models.delete_model(db, catalog_id)

    if _wants_json():
        return jsonify({"status": "deleted", "message": f"Removed {entry.model_id}."}), 200

    flash(f"Removed {entry.model_id}.", "info")
    return redirect(url_for("settings.index"))
