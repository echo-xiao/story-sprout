"""Durable image storage — the SINGLE place image bytes are read/written.

Backend is chosen by config.GCS_BUCKET:
  - set   -> Google Cloud Storage (durable; survives Cloud Run instance recycling
             and redeploys, which a local file does NOT).
  - unset -> local files under GENERATED_DIR, served via the app's /static mount
             (local dev, or before the bucket is provisioned). Same behaviour the
             app had before this module existed, so nothing breaks pre-setup.

Every caller (scene / page / character-sheet / special-page generation + their
version history) goes through here, so swapping GCS for something else later is a
one-file change — that's the whole point of routing image I/O through one door.

`key` is a bucket-relative path like "the_great_gatsby/scenes/buchanan_mansion_v3.png".
The same key is reused as the local path (relative to GENERATED_DIR) in fallback
mode, so the two backends stay interchangeable.
"""

from __future__ import annotations

import logging
import threading

from src.config import GCS_BUCKET, GENERATED_DIR

logger = logging.getLogger(__name__)

_client = None
_client_lock = threading.Lock()


def _bucket():
    """Return the GCS bucket handle, or None when GCS is not configured."""
    global _client
    if not GCS_BUCKET:
        return None
    with _client_lock:
        if _client is None:
            from google.cloud import storage
            _client = storage.Client()
    return _client.bucket(GCS_BUCKET)


def put_image(key: str, data: bytes, content_type: str = "image/png") -> str:
    """Store `data` under `key`; return a URL usable directly in <img src>.

    GCS mode returns the public object URL (the bucket must be public-read).
    Fallback mode writes under GENERATED_DIR and returns a /static path.
    """
    b = _bucket()
    if b is not None:
        blob = b.blob(key)
        blob.upload_from_string(data, content_type=content_type)
        return f"https://storage.googleapis.com/{GCS_BUCKET}/{key}"

    path = GENERATED_DIR / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"/static/{key}"


def get_image(key: str) -> bytes | None:
    """Fetch image bytes for `key`, or None if absent."""
    b = _bucket()
    if b is not None:
        try:
            return b.blob(key).download_as_bytes()
        except Exception as e:  # NotFound or transient — caller treats as missing
            logger.debug("GCS get_image miss for %s: %s", key, e)
            return None

    path = GENERATED_DIR / key
    return path.read_bytes() if path.exists() else None


def image_url(key: str) -> str:
    """The URL a stored `key` resolves to, without touching the bytes (for reads)."""
    if GCS_BUCKET:
        return f"https://storage.googleapis.com/{GCS_BUCKET}/{key}"
    return f"/static/{key}"


def exists(key: str) -> bool:
    b = _bucket()
    if b is not None:
        try:
            return b.blob(key).exists()
        except Exception:
            return False
    return (GENERATED_DIR / key).exists()


def record_image_version(book_id: str, asset_type: str, asset_key: str,
                         data: bytes, content_type: str = "image/png") -> str:
    """Persist a freshly generated image AND register it as a selectable version.

    The ONE call every regeneration path uses so a new image automatically (a)
    lands in durable storage and (b) becomes a pickable version. Content-hashed
    key -> identical bytes reuse the same object and dedupe in the version list.
    Returns the stored URL.
    """
    import hashlib
    import re as _re
    from src.core.db import add_asset_version

    digest = hashlib.sha256(data).hexdigest()
    ext = "png" if "png" in content_type else "jpg"
    safe_key = _re.sub(r"[^\w.-]+", "_", asset_key).strip("_")[:60] or "asset"
    key = f"{book_id}/{asset_type}s/{safe_key}_{digest[:12]}.{ext}"
    url = put_image(key, data, content_type)
    add_asset_version(book_id, asset_type, asset_key, url, image_hash=digest)
    return url


def delete_prefix(prefix: str) -> None:
    """Delete every stored image whose key starts with `prefix` (book deletion)."""
    b = _bucket()
    if b is not None:
        for blob in b.list_blobs(prefix=prefix):
            try:
                blob.delete()
            except Exception as e:
                logger.warning("GCS delete failed for %s: %s", blob.name, e)
        return
    # Local fallback: GENERATED_DIR cleanup is handled by the existing rmtree in
    # delete_book, so nothing extra to do here.
