"""Shared utility functions for route handlers."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from src.config import GENERATED_DIR

logger = logging.getLogger(__name__)

_file_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _get_lock(key: str) -> threading.Lock:
    with _locks_lock:
        if key not in _file_locks:
            _file_locks[key] = threading.Lock()
        return _file_locks[key]


def _load_json(book_id: str, filename: str) -> dict | list | None:
    """Load preprocess data from MongoDB first, fall back to local JSON file."""
    try:
        from src.core.db import load_preprocess_file
        data = load_preprocess_file(book_id, filename)
        if data is not None:
            return data
    except Exception as e:
        logger.debug("MongoDB load failed for %s/%s: %s", book_id, filename, e)

    # Fallback to local file
    path = GENERATED_DIR / book_id / "preprocess" / filename
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


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
