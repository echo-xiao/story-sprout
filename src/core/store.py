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


def _mutate_json(key: str, mutator, retries: int = 8):
    """Atomically read-modify-write a JSON object under GCS optimistic
    concurrency, so parallel mutations of a SHARED blob never lose updates.

    Reads the object with its GCS generation, lets `mutator(obj)` edit it in
    place (obj is {} when the object is absent), then writes with
    `if_generation_match`. A concurrent write (PreconditionFailed / 412) means
    our snapshot is stale — re-read and re-apply. Without this, two requests that
    both read `assets.json` and write it back would clobber each other (the bug
    where a freshly-recorded version vanished when the editor's page-load fired
    many /versions writes at once). Returns whatever `mutator` returns.

    Falls back to a plain read-modify-write for stores whose bucket has no
    `get_blob` (some unit-test fakes) — those tests don't exercise concurrency.
    """
    from google.api_core.exceptions import PreconditionFailed

    b = _bucket()
    get_blob = getattr(b, "get_blob", None)
    if get_blob is None:  # test fake without generation support
        obj = get_json(key) or {}
        result = mutator(obj)
        put_json(key, obj)
        return result

    for _ in range(retries):
        blob = get_blob(key)
        if blob is None:
            obj, generation = {}, 0  # if_generation_match=0 => create-only
        else:
            obj, generation = json.loads(blob.download_as_text()), blob.generation
        result = mutator(obj)
        try:
            b.blob(key).upload_from_string(
                json.dumps(obj, ensure_ascii=False),
                content_type="application/json",
                if_generation_match=generation,
            )
            return result
        except PreconditionFailed:
            continue  # someone else wrote between our read and write — retry
    raise RuntimeError(f"assets write contention on {key} after {retries} tries")


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


# ── Asset versions (image version pointers; bytes live in storage.py/GCS) ───
import uuid
from datetime import datetime, timezone

_MAX_ASSET_VERSIONS = 12


def _assets_key(book_id: str) -> str:
    return f"{book_id}/assets.json"


def _load_assets(book_id: str) -> dict:
    return get_json(_assets_key(book_id)) or {}


def _rec_key(asset_type: str, asset_key: str) -> str:
    return f"{asset_type}:{asset_key}"


def add_asset_version(book_id: str, asset_type: str, asset_key: str, url: str,
                      image_hash: str | None = None,
                      storage_key: str | None = None) -> str:
    k = _rec_key(asset_type, asset_key)
    out: dict[str, str] = {}

    def _mut(assets: dict) -> None:
        rec = assets.get(k) or {"versions": [], "selected_version_id": None}
        versions = rec["versions"]
        if image_hash:
            for v in versions:
                if v.get("hash") == image_hash:
                    if not rec.get("user_selected"):
                        rec["selected_version_id"] = v["id"]
                    assets[k] = rec
                    out["id"] = v["id"]
                    return
        vid = uuid.uuid4().hex[:12]
        versions.append({"id": vid, "url": url, "hash": image_hash,
                         "storage_key": storage_key,
                         "created_at": datetime.now(timezone.utc).isoformat()})
        if len(versions) > _MAX_ASSET_VERSIONS:
            rec["versions"] = versions[-_MAX_ASSET_VERSIONS:]
        if not rec.get("user_selected"):
            rec["selected_version_id"] = vid
        assets[k] = rec
        out["id"] = vid

    _mutate_json(_assets_key(book_id), _mut)
    return out["id"]


def set_selected_version(book_id: str, asset_type: str, asset_key: str,
                         version_id: str) -> bool:
    k = _rec_key(asset_type, asset_key)
    out: dict[str, bool] = {"ok": False}

    def _mut(assets: dict) -> None:
        rec = assets.get(k)
        if not rec or not any(v["id"] == version_id for v in rec["versions"]):
            return
        rec["selected_version_id"] = version_id
        rec["user_selected"] = True
        assets[k] = rec
        out["ok"] = True

    _mutate_json(_assets_key(book_id), _mut)
    return out["ok"]


def get_selected_version(book_id: str, asset_type: str, asset_key: str) -> Optional[dict]:
    rec = _load_assets(book_id).get(_rec_key(asset_type, asset_key))
    if not rec:
        return None
    sel = rec.get("selected_version_id")
    versions = rec.get("versions", [])
    return next((v for v in versions if v["id"] == sel), versions[-1] if versions else None)


def list_asset_versions(book_id: str, asset_type: str, asset_key: str) -> dict:
    rec = _load_assets(book_id).get(_rec_key(asset_type, asset_key))
    if not rec:
        return {"versions": [], "selected_version_id": None}
    return {"versions": rec.get("versions", []),
            "selected_version_id": rec.get("selected_version_id")}


def delete_asset_versions(book_id: str) -> None:
    put_json(_assets_key(book_id), {})
