"""GCS-JSON store — the single data layer (replaces MongoDB src/core/db.py).

Every piece of book state (metadata, characters, segments, chapters, asset
version pointers) is a JSON object in the GCS bucket, under the book_id prefix.
Read = download+parse one object; write = overwrite one object. No database.

Auth: GCS_SA_JSON (service-account JSON string) -> from_service_account_info
(Vercel has no ambient GCP identity); empty -> ADC (local dev). In tests,
monkeypatch `_bucket` to an in-memory fake.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

_client = None
_lock = threading.Lock()


def _bucket():
    """Return the GCS bucket handle. Raises if GCS_BUCKET is unset (the store
    has no local fallback — GCS is the single source of truth)."""
    global _client
    from src.config import GCS_BUCKET, GCS_SA_JSON

    if not GCS_BUCKET:
        raise RuntimeError("GCS_BUCKET is not set — the JSON store requires it.")
    with _lock:
        if _client is None:
            from google.cloud import storage
            if GCS_SA_JSON:
                from google.oauth2 import service_account
                info = json.loads(GCS_SA_JSON)
                creds = service_account.Credentials.from_service_account_info(info)
                _client = storage.Client(project=info.get("project_id"), credentials=creds)
            else:
                _client = storage.Client()
    return _client.bucket(GCS_BUCKET)


def get_json(key: str) -> Optional[Any]:
    blob = _bucket().blob(key)
    if not blob.exists():
        return None
    return json.loads(blob.download_as_text())


def put_json(key: str, data: Any) -> None:
    _bucket().blob(key).upload_from_string(
        json.dumps(data, ensure_ascii=False),
        content_type="application/json",
    )


# ── list helper (overridable in tests) ─────────────────────────────────────
def _list_keys(suffix: str = "") -> list[str]:
    return [b.name for b in _bucket().list_blobs() if b.name.endswith(suffix)]


# ── Books ──────────────────────────────────────────────────────────────────
def save_book(book_id: str, title: str, num_chapters: int, **extra) -> None:
    put_json(f"{book_id}/meta.json",
             {"book_id": book_id, "title": title, "num_chapters": num_chapters, **extra})


def get_book(book_id: str) -> Optional[dict]:
    return get_json(f"{book_id}/meta.json")


def list_books() -> list[dict]:
    out = []
    for key in _list_keys("/meta.json"):
        doc = get_json(key)
        if doc:
            out.append({"book_id": doc.get("book_id", key.split("/")[0]),
                        "title": doc.get("title", ""),
                        "num_chapters": doc.get("num_chapters", 0)})
    return out


# ── Characters ─────────────────────────────────────────────────────────────
def save_characters(book_id: str, characters: list[dict]) -> None:
    put_json(f"{book_id}/characters.json", characters)


def get_characters(book_id: str) -> list[dict]:
    return get_json(f"{book_id}/characters.json") or []


def update_character(book_id: str, canonical_name: str, updates: dict) -> bool:
    chars = get_characters(book_id)
    for c in chars:
        if c.get("canonical_name") == canonical_name:
            c.update(updates)
            save_characters(book_id, chars)
            return True
    return False


# ── Chapters ───────────────────────────────────────────────────────────────
def save_chapter(book_id: str, chapter_idx: int, chapter_doc: dict) -> None:
    put_json(f"{book_id}/chapters/{chapter_idx}.json", {"chapter": chapter_idx, **chapter_doc})


def get_chapter(book_id: str, chapter_idx: int) -> Optional[dict]:
    return get_json(f"{book_id}/chapters/{chapter_idx}.json")


# ── Preprocess JSON files (analysis.json, meta.json, ...) ───────────────────
def save_preprocess_file(book_id: str, filename: str, data: Any) -> None:
    put_json(f"{book_id}/preprocess/{filename}", data)


def load_preprocess_file(book_id: str, filename: str) -> Optional[Any]:
    return get_json(f"{book_id}/preprocess/{filename}")
