"""Per-book shared state store.

Tools auto-save their outputs here. Subsequent tools auto-read
from previous steps, so Gemini doesn't need to shuttle large JSON.
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# In-memory store keyed by book_id
_stores: dict[str, dict[str, Any]] = {}


def get_store(book_id: str) -> dict[str, Any]:
    if book_id not in _stores:
        _stores[book_id] = {}
    return _stores[book_id]


def save(book_id: str, key: str, value: Any) -> None:
    store = get_store(book_id)
    store[key] = value
    logger.debug("State saved: %s.%s (%s)", book_id, key, type(value).__name__)


def load(book_id: str, key: str, default: Any = None) -> Any:
    store = get_store(book_id)
    return store.get(key, default)


def clear(book_id: str) -> None:
    _stores.pop(book_id, None)
