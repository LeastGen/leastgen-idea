"""Single shared slugify helper for run-id prefixes.

Canonical behavior (previously `backend/routers/pipeline.py::_slugify`):
regex `[^a-z0-9]+` -> `-`, 60-char cap, `"research-run"` fallback for
empty results. Adds NFKD unicode normalization so accented input maps to
its ASCII base ("Cafe\u0301" -> "cafe") instead of being dropped, and a
non-str guard. `backend/routers/phases.py` previously kept `/` (path
traversal, P0-7) — both routers must import from here.
"""

from __future__ import annotations

import re
import unicodedata

MAX_LEN = 60
FALLBACK = "research-run"


def slugify(text: str, *, max_len: int = MAX_LEN) -> str:
    """Slugify free-text for use as a run-id prefix (safe charset `[a-z0-9-]`)."""
    if not isinstance(text, str):
        text = str(text)
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return (slug[:max_len] if slug else FALLBACK)
