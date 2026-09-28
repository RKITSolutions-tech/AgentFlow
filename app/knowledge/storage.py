"""Disk storage for knowledge entries with redaction and atomic writes."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path

from app.knowledge import models
from app.runs import security


class KnowledgeStore:
    """Disk-backed storage for knowledge entries."""

    MAX_ENTRY_BYTES = 1_000_000
    MAX_DIR_BYTES = 100_000_000

    def __init__(self, root_path: str | None = None, patterns: tuple[str, ...] = ()):
        self._root = Path(root_path or "knowledge")
        self._patterns = patterns
        self._root.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, entry_id: int, title: str) -> Path:
        """Generate a safe filename (no directory traversal)."""
        title_hash = hashlib.sha256(title.encode()).hexdigest()[:12]
        safe_name = f"{title_hash}_{entry_id}.md"
        return self._root / safe_name

    def write_entry(self, db: sqlite3.Connection, entry_id: int) -> None:
        """Write entry to disk, applying redaction and atomic write."""
        entry = models.get_entry(db, entry_id)
        if entry is None:
            raise LookupError(f"Entry {entry_id} not found")
        path = self._safe_path(entry_id, entry.title)
        clean_content, _ = security.redact(entry.content, self._patterns)
        clean_title, _ = security.redact(entry.title, self._patterns)
        text = f"# {clean_title}\n\nTags: {', '.join(entry.tags or []) or 'none'}\n\n{clean_content}\n"
        if len(text.encode()) > self.MAX_ENTRY_BYTES:
            raise ValueError(f"Entry too large: {len(text)} bytes")
        with tempfile.NamedTemporaryFile(mode="w", dir=self._root, delete=False, suffix=".md") as f:
            temp_path = Path(f.name)
            f.write(text)
        try:
            temp_path.replace(path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def read_entry(self, entry_id: int, title: str) -> str | None:
        """Read entry content from disk."""
        path = self._safe_path(entry_id, title)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def delete_entry(self, entry_id: int, title: str) -> None:
        """Delete entry file from disk."""
        path = self._safe_path(entry_id, title)
        path.unlink(missing_ok=True)

    def get_total_size(self) -> int:
        """Get total size of all files in knowledge directory."""
        return sum(f.stat().st_size for f in self._root.iterdir() if f.is_file())

    def cleanup_lru(self) -> None:
        """Remove oldest entries if total size exceeds limit."""
        total = self.get_total_size()
        if total <= self.MAX_DIR_BYTES:
            return
        files = sorted(
            self._root.iterdir(),
            key=lambda f: f.stat().st_mtime) if self._root.exists() else []
        for f in files:
            if f.is_file():
                f.unlink()
                total -= f.stat().st_size
                if total <= self.MAX_DIR_BYTES:
                    break
