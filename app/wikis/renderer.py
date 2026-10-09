"""Markdown rendering for the project wiki browser
(docs/WIKI_INTEGRATION_AND_PRESENTATION.md §4.1.2, §7.3).

mistune 3 with `escape=True`: raw HTML in a page is shown as text, never
executed, and mistune already neutralises `javascript:`-style links. On top of
that the renderer:

- gives every heading a stable `id` and a `#` anchor link (the browser's
  collapsible-section toggles and smooth scrolling hang off these),
- highlights fenced code with Pygments when it is installed (plain, escaped
  `<pre><code>` otherwise) -- `app.js` adds the copy-to-clipboard button,
- rewrites relative links to other pages / images to wiki browser URLs,
- lazy-loads images and wraps tables in a horizontally scrollable box.

Rendered HTML is cached per (root, path, mtime, size).
"""
from __future__ import annotations

import html
import re
import threading
from typing import Callable

import mistune
from mistune.renderers.html import HTMLRenderer

from app.wikis import scanner

try:  # optional: server-side syntax highlighting
    from pygments import highlight as _pyg_highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import get_lexer_by_name
    from pygments.util import ClassNotFound
except ImportError:  # pragma: no cover - exercised only without Pygments
    _pyg_highlight = None

_PLUGINS = ["table", "strikethrough", "footnotes", "task_lists", "url", "mark"]
_TAGS = re.compile(r"<[^>]+>")


def slugify(text: str) -> str:
    text = html.unescape(_TAGS.sub("", text)).lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[\s_-]+", "-", text).strip("-") or "section"


class WikiHTMLRenderer(HTMLRenderer):
    """HTML renderer for one page. `link_for(target)` maps a root-relative page
    or image path to its browser URL (None leaves the link untouched)."""

    def __init__(self, page_path: str = "", link_for: Callable[[str], str | None] | None = None):
        super().__init__(escape=True)
        self.page_path = page_path
        self.link_for = link_for
        self._ids: dict[str, int] = {}

    def _unique(self, slug: str) -> str:
        count = self._ids.get(slug, 0)
        self._ids[slug] = count + 1
        return slug if count == 0 else f"{slug}-{count}"

    def heading(self, text: str, level: int, **attrs) -> str:
        anchor = self._unique(slugify(text))
        return (
            f'<h{level} id="{anchor}" class="wiki-heading">{text}'
            f'<a class="wiki-anchor" href="#{anchor}" aria-label="Link to this section">#</a></h{level}>\n'
        )

    def block_code(self, code: str, info: str | None = None) -> str:
        language = (info or "").strip().split(None, 1)[0] if info else ""
        if _pyg_highlight is not None and language:
            try:
                lexer = get_lexer_by_name(language, stripall=False)
            except ClassNotFound:
                lexer = None
            if lexer is not None:
                body = _pyg_highlight(code, lexer, HtmlFormatter(nowrap=True))
                return (
                    f'<pre class="wiki-code highlight" data-language="{html.escape(language)}">'
                    f'<code>{body}</code></pre>\n'
                )
        lang_attr = f' data-language="{html.escape(language)}"' if language else ""
        return f'<pre class="wiki-code"{lang_attr}><code>{html.escape(code)}</code></pre>\n'

    def _rewrite(self, url: str) -> str:
        if self.link_for is None:
            return url
        anchor = ""
        if "#" in url:
            url, anchor = url.split("#", 1)
            anchor = "#" + anchor
        if not url:
            return anchor
        target = scanner.resolve_link(self.page_path, html.unescape(url))
        if target is None:
            return url + anchor
        mapped = self.link_for(target)
        return (mapped + anchor) if mapped else url + anchor

    def link(self, text: str, url: str, title: str | None = None) -> str:
        return super().link(text, self._rewrite(url), title)

    def image(self, text: str, url: str, title: str | None = None) -> str:
        rendered = super().image(text, self._rewrite(url), title)
        return rendered.replace("<img ", '<img loading="lazy" ', 1)

    def table(self, text: str) -> str:  # registered by the table plugin's renderer hook below
        return '<div class="wiki-table-wrap"><table>\n' + text + "</table></div>\n"


def _render_table(renderer, text: str) -> str:
    return renderer.table(text)


def render_markdown(text: str, page_path: str = "", link_for: Callable[[str], str | None] | None = None) -> str:
    renderer = WikiHTMLRenderer(page_path, link_for)
    markdown = mistune.create_markdown(escape=True, renderer=renderer, plugins=_PLUGINS)
    renderer.register("table", _render_table)
    return markdown(text or "")


_cache: dict[tuple, str] = {}
_cache_lock = threading.Lock()
_CACHE_MAX = 256


def render_page(cache_key: tuple, text: str, page_path: str, link_for) -> str:
    """`render_markdown` memoised on `cache_key` (root, path, mtime, size)."""
    with _cache_lock:
        cached = _cache.get(cache_key)
    if cached is not None:
        return cached
    rendered = render_markdown(text, page_path, link_for)
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        _cache[cache_key] = rendered
    return rendered

