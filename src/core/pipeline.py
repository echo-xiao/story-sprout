"""Main pipeline: delegates to the Gemini Agent orchestrator.

Also provides MongoDB helpers for the FastAPI app to use.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import motor.motor_asyncio

from src.config import GENERATED_DIR, MONGODB_DB, MONGODB_URI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MongoDB helpers (used by FastAPI for the book library and deletion)
# ---------------------------------------------------------------------------

_mongo_client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None


def _get_db() -> motor.motor_asyncio.AsyncIOMotorDatabase:
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = motor.motor_asyncio.AsyncIOMotorClient(
            MONGODB_URI, serverSelectionTimeoutMS=5000
        )
    return _mongo_client[MONGODB_DB]


async def delete_book(book_id: str) -> bool:
    db = _get_db()
    # One book-level doc in books + per-chapter docs in book_chapters; clear
    # the consistency collections too (characters/segments) instead of
    # orphaning them.
    res = await db.books.delete_many({"book_id": book_id})
    await db.statuses.delete_one({"book_id": book_id})
    for coll in ("book_chapters", "characters", "segments", "preprocess_files", "illustrations"):
        try:
            await db[coll].delete_many({"book_id": book_id})
        except Exception:
            pass
    # A preprocess-only book has a generated dir but no chapter docs yet — treat
    # that as deletable too, so the endpoint proceeds to rmtree instead of 404.
    has_dir = (GENERATED_DIR / book_id).exists()
    return res.deleted_count > 0 or has_dir


# ---------------------------------------------------------------------------
# NOTE: The Gemini function-calling orchestrator path (generate_picture_book →
# run_agent) was removed. The live pipeline runs via scripts/generate_chapter.py
# + src/agents/ (Analyzer→Writer→Artist→QA). This module now only provides the
# MongoDB status/book helpers above.
# ---------------------------------------------------------------------------
