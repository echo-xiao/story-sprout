"""Main pipeline: delegates to the Gemini Agent orchestrator.

Also provides MongoDB helpers for the FastAPI app to use.
"""

from __future__ import annotations

import logging
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional

import motor.motor_asyncio

from src.config import GENERATED_DIR, MONGODB_DB, MONGODB_URI
from src.models import (
    GenerationConfig,
    GenerationStatus,
    PictureBook,
    StatusEnum,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MongoDB helpers (used by FastAPI for status polling and book retrieval)
# ---------------------------------------------------------------------------

_mongo_client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None


def _get_db() -> motor.motor_asyncio.AsyncIOMotorDatabase:
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
    return _mongo_client[MONGODB_DB]


async def save_status(status: GenerationStatus) -> None:
    db = _get_db()
    await db.statuses.update_one(
        {"book_id": status.book_id},
        {"$set": status.model_dump()},
        upsert=True,
    )


async def save_book(book: PictureBook) -> None:
    db = _get_db()
    doc = book.model_dump()
    doc["created_at"] = book.created_at.isoformat()
    await db.books.update_one(
        {"book_id": book.book_id},
        {"$set": doc},
        upsert=True,
    )


async def get_status(book_id: str) -> Optional[dict[str, Any]]:
    db = _get_db()
    return await db.statuses.find_one({"book_id": book_id}, {"_id": 0})


async def get_book(book_id: str) -> Optional[dict[str, Any]]:
    db = _get_db()
    return await db.books.find_one({"book_id": book_id}, {"_id": 0})


async def list_books() -> list[dict[str, Any]]:
    db = _get_db()
    cursor = db.books.find(
        {},
        {"_id": 0, "book_id": 1, "title": 1, "created_at": 1, "config": 1},
    ).sort("created_at", -1)
    return await cursor.to_list(length=200)


async def delete_book(book_id: str) -> bool:
    db = _get_db()
    res = await db.books.delete_one({"book_id": book_id})
    await db.statuses.delete_one({"book_id": book_id})
    return res.deleted_count > 0


# ---------------------------------------------------------------------------
# Main entry point: delegates to the Gemini Agent
# ---------------------------------------------------------------------------

StatusCallback = Optional[Callable[[GenerationStatus], Coroutine[Any, Any, None]]]


async def generate_picture_book(
    source: str,
    config: GenerationConfig,
    book_id: str | None = None,
    status_callback: StatusCallback = None,
) -> PictureBook:
    """Generate a picture book using the Gemini Agent orchestrator.

    The agent uses function calling to invoke pipeline tools (MCP tools),
    deciding the execution order and handling retries autonomously.
    """
    if book_id is None:
        book_id = uuid.uuid4().hex[:12]

    # Save initial status
    status = GenerationStatus(
        book_id=book_id,
        status=StatusEnum.QUEUED,
        progress=0,
        current_step="Starting agent",
    )
    await save_status(status)

    # Status callback that also persists to MongoDB
    async def _status_cb(s: GenerationStatus) -> None:
        await save_status(s)
        if status_callback:
            await status_callback(s)

    try:
        from src.agent_orchestrator import run_agent

        book = await run_agent(
            source=source,
            config=config,
            book_id=book_id,
            status_callback=_status_cb,
        )

        # Save final book to MongoDB
        await save_book(book)

        # Update status to complete
        status.status = StatusEnum.COMPLETE
        status.progress = 100
        status.current_step = "Complete"
        await save_status(status)

        return book

    except Exception as exc:
        logger.exception("Agent failed for book_id=%s", book_id)
        status.status = StatusEnum.FAILED
        status.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        await save_status(status)
        raise
