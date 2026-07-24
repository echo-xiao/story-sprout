"""GCS-JSON store — the single data layer (replaces MongoDB src/core/db.py).

Every piece of book state (metadata, characters, segments, chapters, asset
version pointers) is a JSON object in the GCS bucket, under the book_id prefix.
Read = download+parse one object; write = overwrite one object. No database.

Auth: GCS_SA_JSON (service-account JSON string) -> from_service_account_info
(Vercel has no ambient GCP identity); empty -> ADC (local dev). In tests,
monkeypatch `_bucket` to an in-memory fake.

Backend switch: STORE_BACKEND="gcs" (default) keeps existing behaviour.
STORE_BACKEND="firestore" routes all four primitives to Firestore instead.
Monkeypatch `_fs_collection` in tests to avoid hitting real Firestore.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

_client = None
_lock = threading.Lock()

# Firestore module-level singleton (separate from GCS _client).
_fs_client = None
_fs_lock = threading.Lock()

# Holds the `firestore.transactional` decorator so tests can monkeypatch it.
# Populated lazily inside _fs_mutate_json; tests override via monkeypatch.
_firestore_transactional = None


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


# ── Firestore seam ──────────────────────────────────────────────────────────

def _doc_id(key: str) -> str:
    """Encode a store key as a Firestore document ID.

    Firestore document IDs cannot contain '/', but our keys use '/' as a
    path separator (e.g. 'the_happy_prince/preprocess/analysis.json').
    '|' never appears in our keys (which are slug/slug/name patterns) and
    is legal in Firestore doc IDs, so we swap '/' -> '|'.
    """
    return key.replace("/", "|")


def _fs_collection():
    """Return the Firestore 'json_store' CollectionReference.

    Lazily builds a module-singleton Firestore client from the same
    GCS_SA_JSON service-account JSON that the GCS client uses.
    Monkeypatch THIS function in tests to inject an in-memory fake:
        monkeypatch.setattr(store, "_fs_collection", lambda: fake_col)
    """
    global _fs_client
    from src.config import FIRESTORE_DATABASE, GCS_SA_JSON

    with _fs_lock:
        if _fs_client is None:
            from google.cloud import firestore
            if GCS_SA_JSON:
                from google.oauth2 import service_account
                info = json.loads(GCS_SA_JSON)
                creds = service_account.Credentials.from_service_account_info(info)
                _fs_client = firestore.Client(
                    project=info["project_id"],
                    credentials=creds,
                    database=FIRESTORE_DATABASE,
                )
            else:
                _fs_client = firestore.Client(database=FIRESTORE_DATABASE)
    return _fs_client.collection("json_store")


# ── GCS primitive implementations (renamed from the originals) ───────────────

def _gcs_get_json(key: str) -> Optional[Any]:
    blob = _bucket().blob(key)
    if not blob.exists():
        return None
    return json.loads(blob.download_as_text())


def _gcs_put_json(key: str, data: Any) -> None:
    _bucket().blob(key).upload_from_string(
        json.dumps(data, ensure_ascii=False),
        content_type="application/json",
    )


def _gcs_mutate_json(key: str, mutator, retries: int = 8):
    """GCS-backed atomic read-modify-write via optimistic concurrency."""
    from google.api_core.exceptions import PreconditionFailed

    b = _bucket()
    get_blob = getattr(b, "get_blob", None)
    if get_blob is None:  # test fake without generation support
        obj = _gcs_get_json(key) or {}
        result = mutator(obj)
        _gcs_put_json(key, obj)
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


def _gcs_list_keys(suffix: str = "") -> list[str]:
    return [b.name for b in _bucket().list_blobs() if b.name.endswith(suffix)]


# ── Firestore primitive implementations ─────────────────────────────────────

def _fs_get_json(key: str) -> Optional[Any]:
    col = _fs_collection()
    doc = col.document(_doc_id(key)).get()
    if not doc.exists:
        return None
    return doc.to_dict().get("data")


def _fs_put_json(key: str, data: Any) -> None:
    col = _fs_collection()
    col.document(_doc_id(key)).set({"key": key, "data": data})


def _fs_mutate_json(key: str, mutator, retries: int = 8):
    """Firestore-backed atomic read-modify-write via a Firestore transaction.

    Firestore transactions auto-retry on contention, so the manual
    if_generation_match retry loop is NOT needed here. The `retries` param
    is kept for signature compatibility but is unused on this backend.

    The `_firestore_transactional` module attribute holds the decorator so
    tests can monkeypatch it with an in-memory fake.
    """
    import src.core.store as _self  # self-reference to pick up monkeypatches

    col = _fs_collection()
    ref = col.document(_doc_id(key))

    # Resolve the transactional decorator: use the monkeypatched version if
    # set (tests), otherwise import from the real Firestore library.
    txn_decorator = _self._firestore_transactional
    if txn_decorator is None:
        from google.cloud import firestore as _firestore
        txn_decorator = _firestore.transactional

    result_holder: list = []

    @txn_decorator
    def _txn(txn, ref):
        snap = ref.get(transaction=txn)
        obj = (snap.to_dict() or {}).get("data") or {}
        result = mutator(obj)
        txn.set(ref, {"key": key, "data": obj})
        result_holder.append(result)
        return result

    _txn(ref)
    return result_holder[-1] if result_holder else None


def _fs_list_keys(suffix: str = "") -> list[str]:
    col = _fs_collection()
    out = []
    for snap in col.stream():
        body = snap.to_dict()
        k = body.get("key", "")
        if k.endswith(suffix):
            out.append(k)
    return out


# ── Public dispatchers (read STORE_BACKEND fresh each call) ─────────────────

def get_json(key: str) -> Optional[Any]:
    from src.config import STORE_BACKEND
    if STORE_BACKEND == "firestore":
        return _fs_get_json(key)
    return _gcs_get_json(key)


def put_json(key: str, data: Any) -> None:
    from src.config import STORE_BACKEND
    if STORE_BACKEND == "firestore":
        _fs_put_json(key, data)
        return
    _gcs_put_json(key, data)


def _mutate_json(key: str, mutator, retries: int = 8):
    """Atomically read-modify-write a JSON object under optimistic concurrency.

    On GCS: reads with generation, applies mutator, writes with
    if_generation_match; retries on PreconditionFailed. Returns whatever
    `mutator` returns.

    On Firestore: uses a Firestore transaction (auto-retried by Firestore on
    contention). The `retries` param is kept for signature compatibility but
    is unused on the Firestore backend.
    """
    from src.config import STORE_BACKEND
    if STORE_BACKEND == "firestore":
        return _fs_mutate_json(key, mutator, retries)
    return _gcs_mutate_json(key, mutator, retries)


def _list_keys(suffix: str = "") -> list[str]:
    from src.config import STORE_BACKEND
    if STORE_BACKEND == "firestore":
        return _fs_list_keys(suffix)
    return _gcs_list_keys(suffix)


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


def mutate_preprocess_file(book_id: str, filename: str, mutator):
    """Atomically read-modify-write a preprocess JSON under GCS optimistic
    concurrency (same protection as assets.json). Use this — NOT a plain
    load+save — whenever a preprocess file is an OVERLAY updated in place
    (e.g. special_pages.json edits), so concurrent writers (multiple serverless
    instances, a stale browser re-saving, Save-then-Regen) never clobber each
    other's edits. `mutator(obj)` edits the dict in place ({} when absent) and
    its return value is returned. Raises on a durable-write failure — callers
    MUST surface it (a silently swallowed failure is a false "saved")."""
    return _mutate_json(f"{book_id}/preprocess/{filename}", mutator)


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


def set_version_quality(book_id: str, asset_type: str, asset_key: str,
                        version_id: str, quality: dict) -> bool:
    """Store `quality` on the specific version identified by `version_id`.

    Mirrors `set_selected_version`'s `_mutate_json` shape.
    Returns True if the version was found and updated, False otherwise.
    Never raises — callers must not break generation on a QA-persist failure.
    """
    k = _rec_key(asset_type, asset_key)
    out: dict[str, bool] = {"ok": False}

    def _mut(assets: dict) -> None:
        rec = assets.get(k)
        if not rec:
            return
        for v in rec["versions"]:
            if v["id"] == version_id:
                v["quality"] = quality
                assets[k] = rec
                out["ok"] = True
                return

    _mutate_json(_assets_key(book_id), _mut)
    return out["ok"]


def delete_asset_versions(book_id: str) -> None:
    put_json(_assets_key(book_id), {})
