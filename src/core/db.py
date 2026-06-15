"""MongoDB data layer for Picture Book Generator.

Collections:
  books          — book metadata (title, book_id, status, created_at)
  characters     — character profiles (canonical_name, aliases, gender, appearance, sheet_path)
  preprocess_files — the per-book preprocess JSONs (analysis.json etc.), Mongo-first read
  book_chapters  — generated chapter pages for the reader/PDF
  statuses / feedback — generation status + user feedback

All operations are synchronous (pymongo) for compatibility with scripts.
Falls back gracefully if MongoDB is unavailable.
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

import pymongo

from src.config import MONGODB_URI, MONGODB_DB

logger = logging.getLogger(__name__)

_client: Optional[pymongo.MongoClient] = None
_available: Optional[bool] = None
_last_fail: float = 0.0
_RETRY_AFTER = 30.0  # seconds to back off before retrying a failed connection
# First connection can race between threadpool workers — without a lock each
# loser builds (and leaks) its own MongoClient with live monitor threads.
_client_lock = threading.Lock()


def _get_db() -> Optional[pymongo.database.Database]:
    """Get MongoDB database, or None if unavailable.

    On failure, back off for _RETRY_AFTER seconds instead of disabling MongoDB
    permanently — a transient Atlas blip should not kill the layer for the
    whole process lifetime.
    """
    global _client, _available, _last_fail
    if _available is False and (time.time() - _last_fail) < _RETRY_AFTER:
        return None
    with _client_lock:
        try:
            if _client is None:
                _client = pymongo.MongoClient(MONGODB_URI, serverSelectionTimeoutMS=3000)
                _client.admin.command("ping")
                _available = True
                logger.info("MongoDB connected: %s", MONGODB_URI)
            return _client[MONGODB_DB]
        except Exception as e:
            _available = False
            _last_fail = time.time()
            if _client is not None:
                try:
                    _client.close()  # release topology threads before dropping the ref
                except Exception:
                    pass
            _client = None  # reset so a later call can reconnect
            logger.warning("MongoDB unavailable (%s); backing off %.0fs", e, _RETRY_AFTER)
            return None


def is_available() -> bool:
    """Check if MongoDB is available."""
    _get_db()
    return _available is True


# ═══════════════════════════════════════════════════════════════
# Books collection
# ═══════════════════════════════════════════════════════════════

def save_book(book_id: str, title: str, num_chapters: int, **extra) -> bool:
    """Upsert the single book-level doc. Chapter data lives in the separate
    book_chapters collection (one doc per chapter), so books stays one doc
    per book — no $exists guards, no schema mixing."""
    db = _get_db()
    if db is None:
        return False
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "book_id": book_id,
        "title": title,
        "num_chapters": num_chapters,
        "updated_at": now,
        **extra,
    }
    db.books.update_one(
        {"book_id": book_id},
        {"$set": doc, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return True


def save_book_chapter(book_id: str, chapter_idx: int, chapter_doc: dict) -> bool:
    """Upsert one generated chapter (title, pages, ...) keyed (book_id, chapter)."""
    db = _get_db()
    if db is None:
        return False
    db.book_chapters.update_one(
        {"book_id": book_id, "chapter": chapter_idx},
        {"$set": {"book_id": book_id, "chapter": chapter_idx,
                  "updated_at": datetime.now(timezone.utc).isoformat(),
                  **chapter_doc}},
        upsert=True,
    )
    return True


def save_feedback(message: str, email: str | None = None, context: str | None = None) -> bool:
    """Store one user feedback entry. Returns False if MongoDB is unavailable
    (the route then falls back to a local file so nothing is lost)."""
    db = _get_db()
    if db is None:
        return False
    db.feedback.insert_one({
        "message": message,
        "email": email,
        "context": context,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return True


def usage_since(cutoff_iso: str) -> dict:
    """Activity since `cutoff_iso` for the owner's usage digest: books created
    and feedback received in the window, plus the all-time book total. created_at
    is stored as an ISO string, so a lexicographic $gte is a chronological one."""
    db = _get_db()
    if db is None:
        return {"available": False, "new_books": [], "feedback": [], "total_books": 0}
    new_books = list(db.books.find(
        {"created_at": {"$gte": cutoff_iso}},
        {"_id": 0, "title": 1, "book_id": 1, "created_at": 1},
    ).sort("created_at", -1))
    feedback = list(db.feedback.find(
        {"created_at": {"$gte": cutoff_iso}},
        {"_id": 0, "message": 1, "email": 1, "context": 1, "created_at": 1},
    ).sort("created_at", -1))
    return {
        "available": True,
        "new_books": new_books,
        "feedback": feedback,
        "total_books": db.books.count_documents({}),
    }


# ═══════════════════════════════════════════════════════════════
# Characters collection
# ═══════════════════════════════════════════════════════════════

def save_characters(book_id: str, characters: list[dict]) -> bool:
    db = _get_db()
    if db is None:
        return False
    # Delete old characters for this book, insert new
    db.characters.delete_many({"book_id": book_id})
    if characters:
        docs = [{"book_id": book_id, **c} for c in characters]
        db.characters.insert_many(docs)
    return True


def get_characters(book_id: str) -> list[dict]:
    db = _get_db()
    if db is None:
        return []
    return list(db.characters.find({"book_id": book_id}, {"_id": 0}))


def update_character(book_id: str, canonical_name: str, updates: dict) -> bool:
    db = _get_db()
    if db is None:
        return False
    result = db.characters.update_one(
        {"book_id": book_id, "canonical_name": canonical_name},
        {"$set": updates},
    )
    # matched_count, not blind True: the editor's Mongo-fallback path uses this
    # to decide between "updated" and 404 for characters that don't exist.
    return result.matched_count > 0


# ═══════════════════════════════════════════════════════════════
# Bulk save from preprocess (all layers at once)
# ═══════════════════════════════════════════════════════════════

def save_preprocess(book_id: str, title: str, characters: list[dict],
                    segments: list[dict], alias_map: dict, gender_map: dict) -> bool:
    """Save all preprocess data to MongoDB in one go."""
    db = _get_db()
    if db is None:
        return False

    save_book(book_id, title, len(set(s.get("chapter_idx", 0) for s in segments)),
              alias_map=alias_map, gender_map=gender_map)
    save_characters(book_id, characters)
    logger.info("Saved preprocess to MongoDB: %d characters", len(characters))
    return True


# ═══════════════════════════════════════════════════════════════
# Preprocess files collection (generic JSON document store)
# ═══════════════════════════════════════════════════════════════

def save_preprocess_file(book_id: str, filename: str, data: Any) -> bool:
    """Save a preprocess JSON file to MongoDB."""
    db = _get_db()
    if db is None:
        return False
    db.preprocess_files.update_one(
        {"book_id": book_id, "filename": filename},
        {"$set": {"book_id": book_id, "filename": filename, "data": data,
                  "updated_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    return True


def load_preprocess_file(book_id: str, filename: str) -> Optional[Any]:
    """Load a preprocess JSON file from MongoDB."""
    db = _get_db()
    if db is None:
        return None
    doc = db.preprocess_files.find_one(
        {"book_id": book_id, "filename": filename},
        {"_id": 0, "data": 1},
    )
    if doc is None:
        return None
    return doc.get("data")


def load_preprocess_file_with_meta(book_id: str, filename: str) -> Optional[tuple[Any, Optional[str]]]:
    """Like load_preprocess_file, but also returns the doc's updated_at ISO
    timestamp so callers can detect a doc gone stale behind a newer local
    file (a Mongo blip during save writes the file but not the doc)."""
    db = _get_db()
    if db is None:
        return None
    doc = db.preprocess_files.find_one(
        {"book_id": book_id, "filename": filename},
        {"_id": 0, "data": 1, "updated_at": 1},
    )
    if doc is None:
        return None
    return doc.get("data"), doc.get("updated_at")


def list_preprocess_books() -> list[dict]:
    """List all books that have preprocess data in MongoDB."""
    db = _get_db()
    if db is None:
        return []
    pipeline = [
        {"$match": {"filename": "meta.json"}},
        {"$project": {"_id": 0, "book_id": 1, "data": 1}},
        {"$sort": {"book_id": 1}},
    ]
    results = []
    # A connection that drops AFTER the initial ping isn't caught by _get_db's
    # backoff — without this guard the aggregate raised straight through and
    # the library endpoint 500'd instead of falling back to the disk scan.
    try:
        for doc in db.preprocess_files.aggregate(pipeline):
            meta = doc.get("data", {})
            results.append({
                "book_id": doc["book_id"],
                "title": meta.get("title", doc["book_id"]),
                "num_chapters": meta.get("num_chapters", 0),
            })
    except Exception as e:
        logger.warning("list_preprocess_books failed (%s); falling back to disk", e)
        return []
    return results


# ═══════════════════════════════════════════════════════════════
# Asset versions — ONE place that records "which versions of an image exist
# and which one is selected", for pages / scenes / characters alike.
#
#   asset_type: "page" | "scene" | "character"
#   asset_key : page -> "ch00:seg12"; scene -> location name; character -> canonical_name
#
# Image BYTES live in GCS (src.core.storage); only the pointer + version list
# live here. Selecting is a pure pointer write (no image is generated); only a
# regenerate appends a version — so clicking a thumbnail can never spawn a new
# version. Versions dedupe by content hash and are capped so the list can't grow
# without bound.
# ═══════════════════════════════════════════════════════════════

_MAX_ASSET_VERSIONS = 12


def add_asset_version(book_id: str, asset_type: str, asset_key: str, url: str,
                      image_hash: str | None = None,
                      storage_key: str | None = None) -> Optional[str]:
    """Append a freshly generated version and make it the selected one.

    Dedupe: if a version with the same content hash already exists, no new
    version is added — that existing version is re-selected and its id returned
    (a regen that produced a byte-identical image must not bloat the list).
    Returns the selected version id, or None if MongoDB is unavailable.
    """
    import uuid
    db = _get_db()
    if db is None:
        return None
    key = {"book_id": book_id, "asset_type": asset_type, "asset_key": asset_key}
    doc = db.asset_versions.find_one(key) or {"versions": []}
    versions = doc.get("versions", [])

    if image_hash:
        for v in versions:
            if v.get("hash") == image_hash:
                # Identical image already on record — re-select it, add nothing.
                set_selected_version(book_id, asset_type, asset_key, v["id"])
                return v["id"]

    vid = uuid.uuid4().hex[:12]
    versions.append({
        "id": vid,
        "url": url,
        "hash": image_hash,
        "storage_key": storage_key,  # the storage.py key — used to fetch bytes on select
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # Cap to the most recent N, but never drop the one we're about to select.
    if len(versions) > _MAX_ASSET_VERSIONS:
        versions = versions[-_MAX_ASSET_VERSIONS:]
    db.asset_versions.update_one(
        key,
        {"$set": {**key, "versions": versions, "selected_version_id": vid,
                  "updated_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    return vid


def set_selected_version(book_id: str, asset_type: str, asset_key: str,
                         version_id: str) -> bool:
    """Pick an EXISTING version as the selected one. Pure pointer write — no
    image is generated. No-op-safe if the version id isn't known."""
    db = _get_db()
    if db is None:
        return False
    key = {"book_id": book_id, "asset_type": asset_type, "asset_key": asset_key}
    res = db.asset_versions.update_one(
        {**key, "versions.id": version_id},
        {"$set": {"selected_version_id": version_id,
                  "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    return res.matched_count > 0


def get_selected_version(book_id: str, asset_type: str, asset_key: str) -> Optional[dict]:
    """The selected version dict {id, url, hash, created_at}, or None.

    The SINGLE read entry every consumer (Pages view, PDF export, page-gen
    references) uses to resolve 'the image to use for this asset'."""
    db = _get_db()
    if db is None:
        return None
    doc = db.asset_versions.find_one(
        {"book_id": book_id, "asset_type": asset_type, "asset_key": asset_key},
        {"_id": 0, "versions": 1, "selected_version_id": 1},
    )
    if not doc:
        return None
    sel = doc.get("selected_version_id")
    versions = doc.get("versions", [])
    chosen = next((v for v in versions if v.get("id") == sel), None)
    # Fall back to the newest version if the pointer is missing/stale.
    return chosen or (versions[-1] if versions else None)


def list_asset_versions(book_id: str, asset_type: str, asset_key: str) -> dict:
    """All versions + the selected id for one asset (newest last)."""
    db = _get_db()
    if db is None:
        return {"versions": [], "selected_version_id": None}
    doc = db.asset_versions.find_one(
        {"book_id": book_id, "asset_type": asset_type, "asset_key": asset_key},
        {"_id": 0, "versions": 1, "selected_version_id": 1},
    )
    if not doc:
        return {"versions": [], "selected_version_id": None}
    return {"versions": doc.get("versions", []),
            "selected_version_id": doc.get("selected_version_id")}


def delete_asset_versions(book_id: str) -> None:
    """Drop all version records for a book (called from delete_book)."""
    db = _get_db()
    if db is None:
        return
    try:
        db.asset_versions.delete_many({"book_id": book_id})
    except Exception:
        pass
