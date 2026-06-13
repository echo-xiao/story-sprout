"""MongoDB data layer for Picture Book Generator.

Collections:
  books          — book metadata (title, book_id, status, created_at)
  characters     — character profiles (canonical_name, aliases, gender, appearance, sheet_path)
  preprocess_files — the per-book preprocess JSONs (analysis.json etc.), Mongo-first read
  generation_log — LLM call logs (input, output, tokens, duration)

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
