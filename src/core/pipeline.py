"""Book deletion helper for the FastAPI app, backed by the GCS-JSON store.

(The old MongoDB helpers were removed with the Mongo layer; the live generation
pipeline runs via src/agents/ — Analyzer→Writer→Artist→QA.)
"""

from __future__ import annotations

import logging

from src.config import GENERATED_DIR

logger = logging.getLogger(__name__)


async def delete_book(book_id: str) -> bool:
    """Delete a book's durable data — every GCS object under its `{book_id}/`
    prefix (meta, characters, chapters, assets, images, preprocess).

    Returns True if the book existed (a store meta doc or a local generated
    dir), so the route can 404 an unknown id instead of reporting a phantom
    delete. The route still rmtree's the local dir separately.
    """
    from src.core import storage, store

    existed = (GENERATED_DIR / book_id).exists()
    try:
        existed = existed or store.get_book(book_id) is not None
    except Exception:
        pass  # store unconfigured (no GCS) — rely on the local dir check
    try:
        storage.delete_prefix(f"{book_id}/")
    except Exception as e:
        logger.warning("delete_prefix failed for %s: %s", book_id, e)
    return existed
