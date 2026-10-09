import os

import pytest

from app.wikis.scanner import SCANNER
from app.wikis.search import ENGINE

# 1x1 transparent PNG.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a4"
    "0000000049454e44ae426082"
)

PAGES = {
    "index.md": "# Project docs\n\nStart with [the architecture](ARCHITECTURE.md#layers) and the "
                "[setup guide](guides/setup.md).\n\n![diagram](img/diagram.png)\n",
    "ARCHITECTURE.md": "# Architecture\n\n## Layers\n\nThe scheduler talks to the queue.\n\n"
                       "| Layer | Owner |\n|---|---|\n| queue | ops |\n\n```python\ndef run():\n    return 1\n```\n\n"
                       "<script>alert('x')</script>\n",
    "guides/setup.md": "---\ntitle: Setting up\n---\n\nInstall the scheduler, then read index.md.\n",
    "ADRs/001-use-jwt.md": "---\nstatus: Accepted\ndate: 2026-01-10\nauthor: Ryan\nstakeholders: [Alice, Bob]\n---\n"
                           "# Use JWT for sessions\n\n## Context\n\nTokens need secure storage.\n\n## Decision\n\nUse JWT.\n\n"
                           "## Consequences\n\nKey rotation needed.\n\n## Alternatives Considered\n\n- Server sessions\n",
    "ADRs/002-queue.md": "# Queue backend\n\n**Status:** Proposed\n**Date:** 2026-03-02\n\n## Problem\n\nNeed a queue.\n\n"
                         "## Decision\n\nUse SQLite.\n",
}


@pytest.fixture(autouse=True)
def _fresh_caches():
    SCANNER.invalidate()
    ENGINE.invalidate()
    yield
    SCANNER.invalidate()
    ENGINE.invalidate()


def write_docs(base: str, pages: dict | None = None) -> str:
    docs = os.path.join(base, "docs")
    for rel, text in (pages or PAGES).items():
        path = os.path.join(docs, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    os.makedirs(os.path.join(docs, "img"), exist_ok=True)
    with open(os.path.join(docs, "img", "diagram.png"), "wb") as fh:
        fh.write(PNG)
    os.makedirs(os.path.join(docs, ".hidden"), exist_ok=True)
    with open(os.path.join(docs, ".hidden", "secret.md"), "w") as fh:
        fh.write("# hidden")
    return docs
