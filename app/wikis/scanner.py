"""Wiki file scanner and index (docs/WIKI_INTEGRATION_AND_PRESENTATION.md §4.1, §9).

Walks a wiki root for markdown pages and keeps, per file, its parsed metadata
(title, type, frontmatter, links, headings) and term counts for search. The
cache is keyed by absolute path and re-parses a file only when its mtime or
size changed, so repeated scans cost an `os.walk` plus a `stat` per file; a
whole root is rescanned at most every `search_index_interval` seconds unless
`invalidate()` is called (the browser's "Rescan" button).

Safety: hidden files/folders are skipped, symlinks are only followed when they
resolve inside the root, and every path is re-checked with
`app.workspace.files.resolve_path` before it is opened.
"""
from __future__ import annotations

import os
import posixpath
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass

from app.wikis.models import PAGE_TYPES, WikiPageMeta, WikiRoot
from app.workspace import files

PAGE_EXTENSIONS = (".md", ".markdown")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")

_H1 = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.M)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.M)
_FENCE = re.compile(r"^(```|~~~).*?^\1\s*$", re.M | re.S)
_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_]{1,}")

# Path segment (lower-cased) -> page type (§4.5 and task 73's conventions).
_DIR_TYPES = {
    "adr": "adr", "adrs": "adr", "decisions": "decision", "decision": "decision",
    "decision-log": "decision", "api": "api", "apis": "api", "reference": "api",
    "architecture": "architecture", "design": "architecture",
    "guides": "guide", "guide": "guide", "how-to": "guide", "howto": "guide",
}


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a leading `---` block of `key: value` lines from the body.

    Values may be scalars, `[a, b]` lists or `- item` continuation lines --
    enough for ADR metadata without a YAML dependency. Unparseable blocks are
    left in the body."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if lines[0].strip() != "---":
        return {}, text
    data: dict = {}
    key = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() in ("---", "..."):
            return data, "\n".join(lines[index + 1:]).lstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and key is not None:
            if not isinstance(data.get(key), list):
                data[key] = [] if data.get(key) in ("", None) else [data[key]]
            data[key].append(_scalar(stripped[2:]))
            continue
        name, sep, value = line.partition(":")
        if not sep or not name.strip() or name.startswith(" "):
            return {}, text
        key = name.strip().lower()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            data[key] = [_scalar(v) for v in value[1:-1].split(",") if v.strip()]
        else:
            data[key] = _scalar(value)
    return {}, text  # no closing fence


def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def page_type(path: str, frontmatter: dict) -> str:
    declared = str(frontmatter.get("type", "")).strip().lower()
    if declared in PAGE_TYPES:
        return declared
    segments = [s.lower() for s in path.split("/")[:-1]]
    for segment in reversed(segments):
        if segment in _DIR_TYPES:
            return _DIR_TYPES[segment]
    stem = path.rsplit("/", 1)[-1].lower()
    if stem.startswith(("adr-", "adr_")):
        return "adr"
    if "architecture" in stem or "design" in stem:
        return "architecture"
    return "page"


def _title(path: str, frontmatter: dict, body: str) -> str:
    if frontmatter.get("title"):
        return str(frontmatter["title"])
    match = _H1.search(_FENCE.sub("", body))
    if match:
        return re.sub(r"[*_`]", "", match.group(1)).strip()
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return stem.replace("_", " ").replace("-", " ").strip() or stem


def resolve_link(page_path: str, target: str) -> str | None:
    """A link target in `page_path` as a root-relative path, or None when it is
    external, an in-page anchor, or escapes the root."""
    target = target.split("#", 1)[0].split("?", 1)[0]
    if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("//"):
        return None
    base = "" if target.startswith("/") else posixpath.dirname(page_path)
    resolved = posixpath.normpath(posixpath.join(base, target.lstrip("/")))
    if resolved == "." or resolved.startswith("../") or resolved == "..":
        return None
    return resolved


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass
class _Entry:
    mtime: float
    size: int
    meta: WikiPageMeta
    text: str
    terms: Counter


class WikiScanner:
    """Thread-safe, mtime-invalidated cache of parsed wiki pages."""

    def __init__(self, interval: int = 3600, max_pages: int = 2000, max_page_bytes: int = 2_000_000):
        self.interval = interval
        self.max_pages = max_pages
        self.max_page_bytes = max_page_bytes
        self._lock = threading.Lock()
        self._files: dict[str, _Entry] = {}
        # root id -> (scanned at, pages, folder-tree signature)
        self._roots: dict[str, tuple[float, dict[str, WikiPageMeta], tuple]] = {}

    def configure(self, interval: int, max_pages: int, max_page_bytes: int) -> None:
        self.interval, self.max_pages, self.max_page_bytes = interval, max_pages, max_page_bytes

    # -- discovery --------------------------------------------------------------

    def _walk(self, root_path: str) -> list[str]:
        """Root-relative paths of markdown pages, each folder's own pages before
        its sub-folders', capped at max_pages."""
        real_root = os.path.realpath(root_path)
        found: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
            kept = []
            for d in sorted(dirnames):
                if d.startswith("."):
                    continue
                full = os.path.join(dirpath, d)
                if os.path.islink(full):
                    target = os.path.realpath(full)
                    if not (target == real_root or target.startswith(real_root + os.sep)):
                        continue
                kept.append(d)
            dirnames[:] = kept
            for name in sorted(filenames):
                if name.startswith(".") or not name.lower().endswith(PAGE_EXTENSIONS):
                    continue
                full = os.path.join(dirpath, name)
                if os.path.islink(full):
                    target = os.path.realpath(full)
                    if not target.startswith(real_root + os.sep):
                        continue
                found.append(os.path.relpath(full, root_path).replace(os.sep, "/"))
                if len(found) >= self.max_pages:
                    return found
        return found

    def _parse(self, root: WikiRoot, rel: str, absolute: str, stat: os.stat_result) -> _Entry:
        too_large = stat.st_size > self.max_page_bytes
        text = ""
        if not too_large:
            with open(absolute, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        frontmatter, body = parse_frontmatter(text)
        no_code = _FENCE.sub("", body)
        links = []
        for target in _LINK.findall(no_code):
            resolved = resolve_link(rel, target)
            if resolved and resolved.lower().endswith(PAGE_EXTENSIONS) and resolved != rel and resolved not in links:
                links.append(resolved)
        headings = tuple(
            (len(m.group(1)), re.sub(r"[*_`]", "", m.group(2)).strip()) for m in _HEADING.finditer(no_code)
        )
        meta = WikiPageMeta(
            root_key=root.key, path=rel, title=_title(rel, frontmatter, body), size=stat.st_size,
            modified=stat.st_mtime, wiki_type=page_type(rel, frontmatter), frontmatter=frontmatter,
            links=tuple(links), headings=headings, too_large=too_large,
        )
        terms = Counter(tokenize(body))
        terms.update({t: 3 for t in tokenize(meta.title)})  # title words weigh more
        return _Entry(stat.st_mtime, stat.st_size, meta, text, terms)

    def scan_root(self, root: WikiRoot, force: bool = False) -> dict[str, WikiPageMeta]:
        """{relative path: metadata} for every page under the root. Unchanged
        files come from the cache; a missing root scans as empty."""
        with self._lock:
            cached = self._roots.get(root.key + "|" + root.path)
        if cached and not force and time.monotonic() - cached[0] < self.interval and self._unchanged(root, cached):
            return cached[1]
        if not os.path.isdir(root.path):
            return {}
        signature = self._dir_signature(root.path)
        pages: dict[str, WikiPageMeta] = {}
        for rel in self._walk(root.path):
            try:
                absolute = files.resolve_path(root.path, rel)
                stat = os.stat(absolute)
            except (ValueError, OSError):
                continue
            with self._lock:
                entry = self._files.get(absolute)
            if force or entry is None or entry.mtime != stat.st_mtime or entry.size != stat.st_size or entry.meta.root_key != root.key:
                try:
                    entry = self._parse(root, rel, absolute, stat)
                except OSError:
                    continue
                with self._lock:
                    self._files[absolute] = entry
            pages[rel] = entry.meta
        with self._lock:
            self._roots[root.key + "|" + root.path] = (time.monotonic(), pages, signature)
        return pages

    def _unchanged(self, root: WikiRoot, cached: tuple) -> bool:
        """Cheap freshness check for a cached root: the folder tree's mtimes
        (adds/removes/renames) and every known file's mtime/size."""
        _, pages, signature = cached
        for rel, meta in pages.items():
            try:
                stat = os.stat(os.path.join(root.path, rel))
            except OSError:
                return False
            if stat.st_mtime != meta.modified or stat.st_size != meta.size:
                return False
        return self._dir_signature(root.path) == signature

    @staticmethod
    def _dir_signature(root_path: str) -> tuple:
        sig = []
        for dirpath, dirnames, _ in os.walk(root_path):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            try:
                sig.append((dirpath, os.stat(dirpath).st_mtime))
            except OSError:
                continue
        return tuple(sig)

    def scan_project_docs(self, project_path: str, docs_dir: str = "docs") -> dict[str, WikiPageMeta]:
        """Convenience for one repository's docs folder (task 73's entry point)."""
        root = WikiRoot(key="repo-adhoc", kind="repo", label=docs_dir, path=os.path.join(project_path, docs_dir),
                        project_id=0, ref_id=0, repo_path=project_path)
        return self.scan_root(root)

    # -- cached content ---------------------------------------------------------

    def text(self, root: WikiRoot, rel: str) -> str | None:
        """Raw file content of a scanned page (None if unknown or too large)."""
        try:
            absolute = files.resolve_path(root.path, rel)
        except ValueError:
            return None
        self.scan_root(root)
        with self._lock:
            entry = self._files.get(absolute)
        if entry is None or entry.meta.too_large:
            return None
        return entry.text

    def terms(self, root: WikiRoot, rel: str) -> Counter:
        with self._lock:
            entry = self._files.get(os.path.join(root.path, rel))
        return entry.terms if entry else Counter()

    def invalidate(self, roots: list[WikiRoot] | None = None) -> None:
        """Forget cached roots (all, or the given ones) so the next scan
        re-walks them; per-file parse results are still reused if unchanged."""
        with self._lock:
            if roots is None:
                self._roots.clear()
                self._files.clear()
                return
            for root in roots:
                self._roots.pop(root.key + "|" + root.path, None)


SCANNER = WikiScanner()
