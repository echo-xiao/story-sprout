"""MongoDB data layer for Picture Book Generator.

Collections:
  books          — book metadata (title, book_id, status, created_at)
  characters     — character profiles (canonical_name, aliases, gender, appearance, sheet_path)
  segments       — all segments (text, characters_in_scene, actions, background, sentiment)
  illustrations  — generation records (segment_id, prompt, image_path, version)
  generation_log — LLM call logs (input, output, tokens, duration)

All operations are synchronous (pymongo) for compatibility with scripts.
Falls back gracefully if MongoDB is unavailable.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import pymongo
from pymongo.errors import ServerSelectionTimeoutError

from src.config import MONGODB_URI, MONGODB_DB

logger = logging.getLogger(__name__)

_client: Optional[pymongo.MongoClient] = None
_available: Optional[bool] = None


def _get_db() -> Optional[pymongo.database.Database]:
    """Get MongoDB database, or None if unavailable."""
    global _client, _available
    if _available is False:
        return None
    try:
        if _client is None:
            _client = pymongo.MongoClient(MONGODB_URI, serverSelectionTimeoutMS=3000)
            _client.admin.command("ping")
            _available = True
            logger.info("MongoDB connected: %s", MONGODB_URI)
        return _client[MONGODB_DB]
    except ServerSelectionTimeoutError:
        _available = False
        logger.warning("MongoDB unavailable at %s", MONGODB_URI)
        return None
    except Exception as e:
        _available = False
        logger.warning("MongoDB error: %s", e)
        return None


def is_available() -> bool:
    """Check if MongoDB is available."""
    _get_db()
    return _available is True


# ═══════════════════════════════════════════════════════════════
# Books collection
# ═══════════════════════════════════════════════════════════════

def save_book(book_id: str, title: str, num_chapters: int, **extra) -> bool:
    db = _get_db()
    if db is None:
        return False
    doc = {
        "book_id": book_id,
        "title": title,
        "num_chapters": num_chapters,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    db.books.update_one({"book_id": book_id}, {"$set": doc}, upsert=True)
    return True


def get_book(book_id: str) -> Optional[dict]:
    db = _get_db()
    if db is None:
        return None
    return db.books.find_one({"book_id": book_id}, {"_id": 0})


def list_books() -> list[dict]:
    db = _get_db()
    if db is None:
        return []
    return list(db.books.find({}, {"_id": 0}).sort("updated_at", -1))


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
    db.characters.update_one(
        {"book_id": book_id, "canonical_name": canonical_name},
        {"$set": updates},
    )
    return True


# ═══════════════════════════════════════════════════════════════
# Segments collection
# ═══════════════════════════════════════════════════════════════

def save_segments(book_id: str, segments: list[dict]) -> bool:
    db = _get_db()
    if db is None:
        return False
    db.segments.delete_many({"book_id": book_id})
    if segments:
        docs = [{"book_id": book_id, **s} for s in segments]
        db.segments.insert_many(docs)
    return True


def get_segments(book_id: str, chapter_idx: Optional[int] = None) -> list[dict]:
    db = _get_db()
    if db is None:
        return []
    query: dict = {"book_id": book_id}
    if chapter_idx is not None:
        query["chapter_idx"] = chapter_idx
    return list(db.segments.find(query, {"_id": 0}).sort("id", 1))


def update_segment(book_id: str, segment_id: int, updates: dict) -> bool:
    db = _get_db()
    if db is None:
        return False
    db.segments.update_one(
        {"book_id": book_id, "id": segment_id},
        {"$set": updates},
    )
    return True


# ═══════════════════════════════════════════════════════════════
# Illustrations collection
# ═══════════════════════════════════════════════════════════════

def save_illustration(book_id: str, segment_id: int, prompt: str,
                      image_path: str, version: int = 1) -> bool:
    db = _get_db()
    if db is None:
        return False
    doc = {
        "book_id": book_id,
        "segment_id": segment_id,
        "prompt": prompt,
        "image_path": image_path,
        "version": version,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.illustrations.insert_one(doc)
    return True


def get_illustrations(book_id: str, segment_id: Optional[int] = None) -> list[dict]:
    db = _get_db()
    if db is None:
        return []
    query: dict = {"book_id": book_id}
    if segment_id is not None:
        query["segment_id"] = segment_id
    return list(db.illustrations.find(query, {"_id": 0}).sort("created_at", -1))


# ═══════════════════════════════════════════════════════════════
# Generation log
# ═══════════════════════════════════════════════════════════════

def log_llm_call(book_id: str, step: str, model: str,
                 input_text: str, output_text: str,
                 tokens: int = 0, duration_s: float = 0) -> bool:
    db = _get_db()
    if db is None:
        return False
    doc = {
        "book_id": book_id,
        "step": step,
        "model": model,
        "input_preview": input_text[:500],
        "output_preview": output_text[:500],
        "tokens": tokens,
        "duration_s": round(duration_s, 2),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.generation_log.insert_one(doc)
    return True


def get_generation_log(book_id: str) -> list[dict]:
    db = _get_db()
    if db is None:
        return []
    return list(db.generation_log.find({"book_id": book_id}, {"_id": 0}).sort("created_at", 1))


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
    save_segments(book_id, segments)
    logger.info("Saved preprocess to MongoDB: %d characters, %d segments", len(characters), len(segments))
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
    for doc in db.preprocess_files.aggregate(pipeline):
        meta = doc.get("data", {})
        results.append({
            "book_id": doc["book_id"],
            "title": meta.get("title", doc["book_id"]),
            "num_chapters": meta.get("num_chapters", 0),
        })
    return results
