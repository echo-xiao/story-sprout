"""Shared utility functions for route handlers."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from fastapi import Header, HTTPException

from src.config import GENERATED_DIR
from src.core import storage

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


def book_owner_email(book_id: str) -> str:
    """The email that created this book (from user.json), lowercased, or ''.

    The single source of book ownership — read by the ownership middleware
    (app.py) so EVERY write to a book is gated in one place, not per-endpoint.
    """
    path = GENERATED_DIR / book_id / "preprocess" / "user.json"
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        return (info.get("email") or "").strip().lower() if isinstance(info, dict) else ""
    except (OSError, ValueError):
        return ""


def is_admin_token(token: str | None) -> bool:
    """Constant-time check that `token` matches ADMIN_TOKEN.

    A valid admin token bypasses the BYOK gate AND book-ownership, and runs
    generation on the PROJECT backend (Vertex) — no user key is injected. Lets
    the operator regenerate sample books without flipping the global
    REQUIRE_USER_KEY switch (which would open generation to everyone). The SINGLE
    admin predicate, reused by both middlewares and the route dependency. Unset
    ADMIN_TOKEN → always False (no backdoor by default)."""
    import secrets
    from src.config import ADMIN_TOKEN
    return bool(ADMIN_TOKEN and token and secrets.compare_digest(token, ADMIN_TOKEN))


def _require_user_key(
    x_gemini_key: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None),
) -> str | None:
    """No-op gate. The app has NO auth — the shared-passcode gate was removed,
    so generation is open to anyone with the URL (all generation runs on the
    project backend / its API keys). Kept as a FastAPI dependency purely so the
    ~17 endpoints wiring Depends(_require_user_key) don't each need editing; it
    never blocks and never injects a per-user key."""
    return None

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


def _load_json(book_id: str, filename: str, prefetched=None) -> dict | list | None:
    """THE single accessor for a preprocess file.

    The durable store (Firestore or GCS, per STORE_BACKEND) is the SINGLE source
    of truth.  A successful store read — even one that returns ``None`` (the
    document is genuinely absent) — is AUTHORITATIVE and is returned immediately
    without consulting the local file.  The local file under GENERATED_DIR is
    ONLY used when ALL store read attempts raise (store unconfigured / unreachable),
    which keeps local-dev (no backend) and a total-outage last-resort working.

    ``prefetched`` is accepted for backward compatibility (the old MongoDB-MCP
    batch-read path) and IGNORED — MCP is gone.
    """
    from src.core import store
    # Retry the store read on transient failures.  On serverless the /tmp copy
    # is per-instance and cross-request STALE — an earlier write on this instance
    # left an old special_pages.json, so serving the stale local copy after a
    # save caused the edit to "vanish" (and Save-&-Regen read the pre-edit
    # summary).  The store is strongly consistent; distinguish a SUCCESSFUL read
    # (data or None) from a RAISED read (store unavailable) so we never let a
    # stale local copy shadow a fresh or genuinely-absent store value.
    store_ok = False
    data = None
    for attempt in range(3):
        try:
            data = store.load_preprocess_file(book_id, filename)
            store_ok = True
            break  # store read succeeded (data may be None if the object is absent)
        except Exception as e:  # store unconfigured (local dev) or transient error
            logger.debug("store load failed (attempt %d) for %s/%s: %s",
                         attempt + 1, book_id, filename, e)
            if attempt < 2:
                import time as _t
                _t.sleep(0.2 * (attempt + 1))
    if store_ok:
        # Store read SUCCEEDED — its answer is authoritative (data or None).
        # Never fall through to the local file; that copy is per-instance stale.
        return data
    # ALL attempts raised — store is unreachable (local dev / total outage).
    # Fall back to the local file as a last resort.
    path = GENERATED_DIR / book_id / "preprocess" / filename
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_characters(book_id: str) -> list[dict]:
    """Character profiles — the GCS-JSON store's characters.json is the single
    source of truth; the preprocess llm_characters.json is a last-resort
    fallback (a failed re-preprocess can leave it blank, so never primary)."""
    from src.core import store
    try:
        chars = store.get_characters(book_id)
        if chars:
            return chars
    except Exception as e:
        logger.debug("store.get_characters failed for %s: %s", book_id, e)
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


def _normalize_character_name(name: str) -> str:
    """Loose key for matching a scene/segment character name to a canonical
    record: lowercased, a leading article ("the "/"a "/"an ") dropped,
    whitespace collapsed.

    Lets "Remarkable Rocket" match canonical "the Remarkable Rocket" WITHOUT
    hardcoding either. This is the root cause of the cover's missing-character
    bug: segments name a character by a short form, but the canonical record
    stores it WITH a leading "the" and its aliases don't list the short form —
    so an exact/case-insensitive match fails and the sheet is never found.
    """
    import re
    s = (name or "").strip().lower()
    s = re.sub(r"^(the|a|an)\s+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def make_character_name_resolver(characters: list[dict]):
    """Build once, resolve many: returns ``f(name) -> canonical_name`` using
    canonical → alias → normalized(canonical/alias) matching, falling back to
    the input name unchanged when nothing matches.

    The SINGLE name-resolution rule shared by every place that maps a
    ``characters_in_scene`` entry to a character record / sheet / version, so
    the editor panel and all generation paths resolve a scene name to the same
    character. Canonical names are registered before aliases, so a character's
    own canonical form always beats another character's alias for the same key.
    """
    exact: dict[str, str] = {}
    norm: dict[str, str] = {}
    aliases: list[tuple[str, str]] = []
    # Pass 1: every canonical name claims its key (highest priority).
    for c in characters:
        cn = (c.get("canonical_name") or "").strip()
        if not cn:
            continue
        exact.setdefault(cn.lower(), cn)
        norm.setdefault(_normalize_character_name(cn), cn)
        for a in c.get("aliases") or []:
            a = (a or "").strip()
            if a:
                aliases.append((a, cn))
    # Pass 2: aliases fill only keys no canonical already claimed.
    for a, cn in aliases:
        exact.setdefault(a.lower(), cn)
        norm.setdefault(_normalize_character_name(a), cn)

    def resolve(name: str) -> str:
        if not name:
            return name
        return (
            exact.get(name.strip().lower())
            or norm.get(_normalize_character_name(name))
            or name
        )

    return resolve


def resolve_canonical_name(book_id: str, name: str) -> str:
    """Resolve one scene character name to its canonical form (loads the book's
    characters). For many names, build a resolver once via
    ``make_character_name_resolver(load_characters(book_id))`` instead."""
    return make_character_name_resolver(load_characters(book_id))(name)


def versioned_static_url(rel_path: str, fs_path) -> str:
    """A storage URL with a cache-busting ``?v=<mtime>`` derived from the file.

    Page images are written in place at a STABLE path (page_001.png), so a
    redraw never changes the URL — the browser kept serving the cached bytes
    and "Gen chapter" looked like a no-op. Tying the version to the file mtime
    means the URL changes whenever the file does, so every consumer (editor,
    page list, PDF preview, thumbnails) re-fetches without per-component
    ``?v=counter`` hacks. Falls back to no version when the file is missing.

    Returns a GCS public URL when ``GCS_BUCKET`` is set, or a ``/static/``
    path for local development (storage.image_url handles the switch).
    """
    try:
        v = int(fs_path.stat().st_mtime)
    except OSError:
        return storage.image_url(rel_path)
    return f"{storage.image_url(rel_path)}?v={v}"


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
                             image_path: str | None = None, text: str | None = None,
                             refs: dict | None = None) -> None:
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
    from src.core import store

    store_key = f"{book_id}/chapters/ch{ch_idx:02d}/chapter_data.json"
    path = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "chapter_data.json"
    lock = _get_lock(f"{book_id}/ch{ch_idx:02d}/chapter_data.json")

    # Capture the final state after the store mutate so we can mirror locally.
    _final_data: list = []

    def _mutator(data: dict) -> None:
        # Bootstrap only when the store has no doc yet (genuinely absent).
        if not data:
            data["chapter_idx"] = ch_idx
            data["pages"] = []
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
        if refs is not None:
            entry["refs"] = refs
        _final_data.append(data)

    # AUTHORITATIVE sync point for chapter_data — store._mutate_json is
    # atomic (Firestore transaction or GCS optimistic-concurrency retry) so
    # concurrent page updates to the same chapter never clobber each other.
    # The base of the read-modify-write is the STORE, not the local file,
    # so a cold serverless instance with an empty /tmp cannot lose pages.
    try:
        store._mutate_json(store_key, _mutator)
    except Exception as e:
        logger.warning("chapter_data store mutate failed for %s ch%d: %s", book_id, ch_idx, e)
        return

    # Local mirror: best-effort so same-invocation PDF/generator fast path
    # still has the file on disk; never used as the read authority.
    with lock:
        if _final_data:
            write_json_atomic(path, _final_data[0])
        else:
            logger.warning("chapter_data mutator did not capture final state for %s ch%d — local mirror skipped",
                           book_id, ch_idx)


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


def write_local_preprocess(book_id: str, filename: str, data: Any) -> None:
    """Write ONLY the local GENERATED_DIR copy of a preprocess file.

    This is a best-effort same-invocation cache for the PDF/generator fast path.
    It is NEVER a read authority: _load_json only consults the local file when
    ALL durable-store read attempts raise (store unreachable).  Callers that need
    durability write to the store separately — via _save_json for a full overwrite,
    or store.mutate_preprocess_file for an atomic overlay update."""
    path = GENERATED_DIR / book_id / "preprocess" / filename
    lock = _get_lock(f"{book_id}/{filename}")
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8")


def _save_json(book_id: str, filename: str, data: Any) -> None:
    """Persist a preprocess file to the durable store (authority) AND a local
    GENERATED_DIR copy (best-effort same-invocation cache for generators / PDF).

    The durable store (Firestore or GCS) is written first and is the SINGLE
    source of truth; the local copy is a cache that _load_json uses ONLY when
    the store is unreachable.  This is a whole-file OVERWRITE and swallows the
    store error (best-effort).  For an OVERLAY updated in place (read-modify-write,
    e.g. special_pages.json edits) use store.mutate_preprocess_file so concurrent
    writers don't clobber each other and a real write failure is surfaced."""
    from src.core import store
    try:
        store.save_preprocess_file(book_id, filename, data)
    except Exception as e:
        logger.warning("store save failed for %s/%s: %s", book_id, filename, e)

    write_local_preprocess(book_id, filename, data)
