from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from app.security import PathNotAllowedError, validate_repository_path
import os

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.get("/directories")
def list_directories():
    path = request.args.get("path", "").strip()
    if not path:
        return jsonify({"error": "No path provided"}), 400

    allowed_roots = current_app.config["ALLOWED_PROJECT_ROOTS"]

    try:
        validate_repository_path(path, allowed_roots)
    except PathNotAllowedError as exc:
        return jsonify({"error": str(exc)}), 403

    try:
        if not os.path.isdir(path):
            return jsonify({"error": "Path is not a directory"}), 400

        entries = os.listdir(path)
        directories = []

        for entry in sorted(entries):
            full_path = os.path.join(path, entry)
            if os.path.isdir(full_path) and not entry.startswith("."):
                directories.append({"name": entry, "path": full_path})

        return jsonify({"directories": directories})
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403
    except OSError as exc:
        return jsonify({"error": str(exc)}), 400
