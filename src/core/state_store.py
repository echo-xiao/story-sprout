"""Per-book shared state store.

Tools auto-save their outputs here. Subsequent tools auto-read
from previous steps, so Gemini doesn't need to shuttle large JSON.
"""

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# In-memory store keyed by book_id, protected by a lock
_stores: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def get_store(book_id: str) -> dict[str, Any]:
    with _lock:
        if book_id not in _stores:
            _stores[book_id] = {}
        return _stores[book_id]


def save(book_id: str, key: str, value: Any) -> None:
    with _lock:
        if book_id not in _stores:
            _stores[book_id] = {}
        _stores[book_id][key] = value
    logger.debug("State saved: %s.%s (%s)", book_id, key, type(value).__name__)


def load(book_id: str, key: str, default: Any = None) -> Any:
    with _lock:
        store = _stores.get(book_id, {})
        return store.get(key, default)


def clear(book_id: str) -> None:
    with _lock:
        _stores.pop(book_id, None)
