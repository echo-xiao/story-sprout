"""Shared utility functions for route handlers."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from fastapi import Header, HTTPException

from src.config import GENERATED_DIR

logger = logging.getLogger(__name__)

# Assets with a regeneration in flight, keyed (book_id, kind, ident) — e.g.
# ("b", "segment", 3) or ("b", "character", "Jay Gatsby"). The regen endpoints
# claim before spawning their background task and release in its finally;
# without this, two clicks ran two Gemini generations racing on the same
# files, and restore-version could interleave with a running regen.
# In-memory is fine: the service runs as a single instance.
_active_regens: set[tuple[str, str, Any]] = set()


def _require_user_key(x_gemini_key: str | None = Header(default=None)) -> str | None:
    """BYOK gate (only enforced when REQUIRE_USER_KEY=true).

    Enforced: generating/regenerating needs the caller's own Gemini key (403
    otherwise) so public users can't bill the project. Not enforced: the key is
    IGNORED and everything runs on the project backend (Vertex). Honoring an
    optional key here used to silently route image generation to free-tier AI
    Studio keys, which have ZERO quota for the image model — every regen 429'd
    while the project backend would have worked fine.
    """
    from src.config import REQUIRE_USER_KEY
    if not REQUIRE_USER_KEY:
        return None
    if not x_gemini_key:
        raise HTTPException(
            status_code=403,
            detail="A Gemini API key is required to generate. Add yours on the Create page.",
        )
    return x_gemini_key

def write_json_atomic(path, data: Any) -> None:
    """Write JSON via temp file + rename so a concurrent reader (e.g. a status
    poll) never sees a torn, half-written file."""
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, default=str, ensure_ascii=False))
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_file_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _get_lock(key: str) -> threading.Lock:
    with _locks_lock:
        if key not in _file_locks:
            _file_locks[key] = threading.Lock()
        return _file_locks[key]


def _load_json(book_id: str, filename: str) -> dict | list | None:
    """Load preprocess data from MongoDB first, fall back to local JSON file."""
    try:
        from src.core.db import load_preprocess_file
        data = load_preprocess_file(book_id, filename)
        if data is not None:
            return data
    except Exception as e:
        logger.debug("MongoDB load failed for %s/%s: %s", book_id, filename, e)

    # Fallback to local file
    path = GENERATED_DIR / book_id / "preprocess" / filename
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_characters(book_id: str) -> list[dict]:
    """Character profiles from the canonical `characters` collection.

    The `characters` collection (the consistency hub, kept in sync via the
    MongoDB MCP integration) is the single source of truth. The
    preprocess_files / llm_characters.json store can be left blank by a failed
    re-preprocess, so it is only a last-resort fallback — never the primary read.
    """
    try:
        from src.core.db import get_characters as _get_chars_db
        chars = _get_chars_db(book_id)
        if chars:
            return chars
    except Exception as e:
        logger.debug("get_characters failed for %s: %s", book_id, e)
    data = _load_json(book_id, "llm_characters.json")
    return data.get("characters", []) if isinstance(data, dict) else []


def segment_page_num(segments: list[dict], ch_idx: int, seg_id: int) -> int:
    """Page number of a segment: 1-based position within its chapter's
    segments sorted by id. Falls back to 1 when the segment isn't found —
    the same semantics every route previously open-coded."""
    ch_segments = sorted(
        (s for s in segments if s.get("chapter_idx") == ch_idx),
        key=lambda s: s.get("id", 0),
    )
    return next((i + 1 for i, s in enumerate(ch_segments) if s.get("id") == seg_id), 1)


def _save_json(book_id: str, filename: str, data: Any) -> None:
    """Save preprocess data to MongoDB and local disk."""
    # Save to MongoDB
    try:
        from src.core.db import save_preprocess_file
        save_preprocess_file(book_id, filename, data)
    except Exception as e:
        logger.warning("MongoDB save failed for %s/%s: %s", book_id, filename, e)

    # Also save to local disk as backup
    path = GENERATED_DIR / book_id / "preprocess" / filename
    lock = _get_lock(f"{book_id}/{filename}")
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
