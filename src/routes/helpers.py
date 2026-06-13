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

# Chapters currently generating (book_id, ch_idx). Prevents a second subprocess
# from being spawned for a chapter that's already running (which would double-hit
# Gemini and race on progress.json / agent_log.json). Lives here (not in
# generation.py) so editor.py can consult it without a circular import — rename /
# restore endpoints must refuse to touch assets a chapter run is using.
_active_generations: set[tuple[str, int]] = set()


def book_generation_active(book_id: str, ch_idx: int | None = None) -> bool:
    """True when a chapter-generation subprocess is running for this book
    (or for one specific chapter when ch_idx is given)."""
    if ch_idx is not None:
        return (book_id, ch_idx) in _active_generations
    return any(claim[0] == book_id for claim in _active_generations)


def book_regen_active(book_id: str) -> bool:
    """True when any per-asset regeneration is in flight for this book."""
    return any(claim[0] == book_id for claim in _active_regens)


# Last failure message per regen claim, set when a regen task ends without
# producing a file and cleared when a new regen claims the asset. Served by
# GET /regen-active so the frontend can tell the user WHY (e.g. "free-tier key
# has zero image quota — use a billing-enabled key") instead of timing out
# silently. In-memory like the claims themselves (single-instance service).
_last_regen_errors: dict[tuple[str, str, Any], str] = {}


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
            detail="A Gemini API key with BILLING ENABLED (paid tier) is required to "
                   "generate — free keys have zero image quota. Add yours on the Create page.",
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
    """Load preprocess data from MongoDB first, fall back to local JSON file.

    Freshness guard: _save_json writes BOTH stores best-effort, so a Mongo
    blip during a save leaves the file updated but the doc stale — and
    Mongo-first reads then shadowed the fresh file forever. When the local
    file is clearly newer than the doc, the file wins and the doc is healed
    from it (best-effort), closing the divergence instead of perpetuating it.
    """
    path = GENERATED_DIR / book_id / "preprocess" / filename

    mongo_data = None
    mongo_updated: str | None = None
    try:
        from src.core.db import load_preprocess_file_with_meta
        result = load_preprocess_file_with_meta(book_id, filename)
        if result is not None:
            mongo_data, mongo_updated = result
    except Exception as e:
        logger.debug("MongoDB load failed for %s/%s: %s", book_id, filename, e)

    if mongo_data is not None:
        try:
            if mongo_updated and path.exists():
                from datetime import datetime
                doc_ts = datetime.fromisoformat(mongo_updated).timestamp()
                # 2s epsilon: a normal dual write lands in both stores within
                # moments — only a clearly newer file indicates divergence.
                if path.stat().st_mtime > doc_ts + 2:
                    file_data = json.loads(path.read_text(encoding="utf-8"))
                    try:
                        from src.core.db import save_preprocess_file
                        save_preprocess_file(book_id, filename, file_data)
                        logger.info("Healed stale Mongo doc %s/%s from newer local file", book_id, filename)
                    except Exception:
                        pass  # heal is best-effort; the fresh file still wins below
                    return file_data
        except Exception as e:
            logger.debug("Freshness check failed for %s/%s: %s", book_id, filename, e)
        return mongo_data

    # Fallback to local file
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


def load_character_profiles(book_id: str) -> list[dict]:
    """Generation-shaped character profiles — the SINGLE source of character
    appearance for every generation path (web + the chapter subprocess).

    Resolves through load_characters (the `characters` collection, file
    fallback), so an editor edit is honoured everywhere. This replaces
    analysis.json['character_profiles'], a copy written once at preprocess
    that no edit ever updated — whole-chapter generation read it and drew
    renamed/re-described characters with their stale look.
    """
    return [
        {
            "name": c.get("canonical_name", ""),
            "role": c.get("role", "supporting"),
            "gender": c.get("gender", "unknown"),
            "aliases": c.get("aliases", []),
            "personality_traits": [],
            "appearance_description": [c.get("appearance", ""), c.get("description", "")],
            "visual_details": c.get("visual_details", {}),
        }
        for c in load_characters(book_id)
    ]


def segment_page_num(segments: list[dict], ch_idx: int, seg_id: int) -> int:
    """Page number of a segment: 1-based position within its chapter's
    segments sorted by id. Falls back to 1 when the segment isn't found —
    the same semantics every route previously open-coded."""
    ch_segments = sorted(
        (s for s in segments if s.get("chapter_idx") == ch_idx),
        key=lambda s: s.get("id", 0),
    )
    return next((i + 1 for i, s in enumerate(ch_segments) if s.get("id") == seg_id), 1)


def update_chapter_data_page(book_id: str, ch_idx: int, page_num: int,
                             image_path: str | None = None, text: str | None = None) -> None:
    """Keep chapter_data.json's entry for one page in step after a
    single-page change (regen / restore-version / text edit).

    chapter_data.json is what the combined book.pdf build reads. Without this,
    a regen that switched image extensions (.png → .jpg) left a dead absolute
    path — a silently blank page in the next PDF — and edited or restored
    text never reached it at all.

    UPSERTS: pages that have no entry yet (segment was <10 words at pipeline
    time, or the chapter was never pipeline-generated at all) are inserted,
    bootstrapping the file if needed — previously such pages showed in the
    editor but were silently absent from every PDF.
    """
    import re as _re

    path = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "chapter_data.json"
    lock = _get_lock(f"{book_id}/ch{ch_idx:02d}/chapter_data.json")
    with lock:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("chapter_data.json unreadable for %s ch%d — page %d update dropped",
                               book_id, ch_idx, page_num)
                return
        else:
            # Chapter built page-by-page via segment regen before any full
            # pipeline run — bootstrap so the PDF can include it.
            data = {"chapter_idx": ch_idx, "pages": []}
        pages = data.setdefault("pages", [])
        for p in pages:  # legacy entries lack page_number — derive from filename
            if "page_number" not in p:
                m = _re.search(r"page_(\d+)", p.get("image_path", "") or "")
                if m:
                    p["page_number"] = int(m.group(1))
        entry = next((p for p in pages if p.get("page_number") == page_num), None)
        if entry is None:
            entry = {"page_number": page_num, "image_path": "", "text": ""}
            pages.append(entry)
            pages.sort(key=lambda p: p.get("page_number") or 0)
        if image_path:
            entry["image_path"] = image_path
        if text is not None:
            entry["text"] = text
        write_json_atomic(path, data)


def invalidate_chapter_consistency(book_id: str, ch_idx: int) -> None:
    """Drop the chapter-level consistency.json cache.

    It aggregates per-page scores computed against specific images and text;
    any page image swap (regen, restore-version) or text edit makes it stale.
    GET /chapter/{ch}/consistency serves the file verbatim, so leaving it
    reports verdicts about content that no longer exists.
    """
    p = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "consistency.json"
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass


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
