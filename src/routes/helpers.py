"""Shared utility functions for route handlers."""

from __future__ import annotations

import json
import threading
from typing import Any

from src.config import GENERATED_DIR

_file_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _get_lock(key: str) -> threading.Lock:
    with _locks_lock:
        if key not in _file_locks:
            _file_locks[key] = threading.Lock()
        return _file_locks[key]


def _load_json(book_id: str, filename: str) -> dict | list | None:
    path = GENERATED_DIR / book_id / "preprocess" / filename
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_json(book_id: str, filename: str, data: Any) -> None:
    path = GENERATED_DIR / book_id / "preprocess" / filename
    lock = _get_lock(f"{book_id}/{filename}")
    with lock:
        path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
