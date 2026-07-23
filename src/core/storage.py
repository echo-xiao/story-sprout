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
from pathlib import Path

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
            from src.config import GCS_SA_JSON
            if GCS_SA_JSON:
                import json
                from google.oauth2 import service_account
                info = json.loads(GCS_SA_JSON)
                creds = service_account.Credentials.from_service_account_info(info)
                _client = storage.Client(project=info.get("project_id"), credentials=creds)
            else:
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


def mirror_to_gcs(local_path) -> None:
    """Upload a just-written LOCAL file to GCS at its GENERATED_DIR-relative key,
    so generated images survive Cloud Run instance recycling. No-op without GCS.

    Called from the one low-level image saver (save_inline_image), so EVERY
    generation path (page / character sheet / special page) persists durably —
    not just the per-asset regen endpoints."""
    if not GCS_BUCKET:
        return
    p = local_path if isinstance(local_path, Path) else Path(local_path)
    try:
        key = str(p.relative_to(GENERATED_DIR))
    except ValueError:
        return
    try:
        ct = "image/png" if p.suffix == ".png" else "image/jpeg"
        put_image(key, p.read_bytes(), ct)
        # Drop the other-extension object so a stale .png can't shadow a new .jpg
        # (or vice-versa) — the serving layer would otherwise return the old one.
        other = key[: -len(p.suffix)] + (".jpg" if p.suffix == ".png" else ".png")
        if other != key:
            delete_key(other)
    except Exception as e:
        logger.warning("mirror_to_gcs failed for %s: %s", local_path, e)


def localize(key: str) -> str | None:
    """Ensure a LOCAL file copy of `key` exists and return its path, downloading
    from GCS if needed. Generators/the PDF renderer read local paths, so a GCS
    object has to be materialized before they can use it."""
    local = GENERATED_DIR / key
    if local.exists():
        return str(local)
    data = get_image(key)
    if data is None:
        return None
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(data)
    return str(local)


def delete_key(key: str) -> None:
    """Delete a single stored image (GCS + any local copy)."""
    b = _bucket()
    if b is not None:
        try:
            b.blob(key).delete()
        except Exception:
            pass
    p = GENERATED_DIR / key
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass


def exists(key: str) -> bool:
    b = _bucket()
    if b is not None:
        try:
            return b.blob(key).exists()
        except Exception:
            return False
    return (GENERATED_DIR / key).exists()


def list_keys(prefix: str) -> list[str]:
    """List durable object keys under `prefix` RECURSIVELY, with the SAME
    semantics in GCS and local mode. Unlike list_prefix (which treats the last
    path component as a filename stem in local mode), this returns every
    key/file whose path starts with `prefix` — the right primitive for asking
    "which page/sheet/cover images exist under this directory" on serverless,
    where the local /tmp is empty but the durable copy lives in GCS."""
    b = _bucket()
    if b is not None:
        try:
            return [blob.name for blob in b.list_blobs(prefix=prefix)]
        except Exception as e:
            logger.warning("GCS list_keys failed for %s: %s", prefix, e)
            return []
    root = GENERATED_DIR / prefix
    if not root.exists():
        return []
    return [str(p.relative_to(GENERATED_DIR)) for p in root.rglob("*") if p.is_file()]


def list_prefix(prefix: str) -> list[str]:
    """List stored image keys under `prefix`. Reads GCS when configured (so it
    works after a Cloud Run redeploy wiped the local disk — the durable copy is
    in GCS), else the local filesystem."""
    b = _bucket()
    if b is not None:
        try:
            return [blob.name for blob in b.list_blobs(prefix=prefix)]
        except Exception as e:
            logger.warning("GCS list_prefix failed for %s: %s", prefix, e)
            return []
    root = GENERATED_DIR / prefix
    parent = root.parent
    if not parent.exists():
        return []
    stem = root.name
    return [
        str(p.relative_to(GENERATED_DIR))
        for p in parent.glob(f"{stem}*") if p.is_file()
    ]


def record_image_version(book_id: str, asset_type: str, asset_key: str,
                         data: bytes, content_type: str = "image/png") -> str:
    """Persist a freshly generated image AND register it as a selectable version.

    The ONE call every regeneration path uses so a new image automatically (a)
    lands in durable storage and (b) becomes a pickable version. Content-hashed
    key -> identical bytes reuse the same object and dedupe in the version list.
    Returns the new version id (callers that need to attach QA capture this;
    callers that only need durable storage may ignore it).
    """
    import hashlib
    import re as _re
    from src.core.store import add_asset_version

    digest = hashlib.sha256(data).hexdigest()
    ext = "png" if "png" in content_type else "jpg"
    safe_key = _re.sub(r"[^\w.-]+", "_", asset_key).strip("_")[:60] or "asset"
    key = f"{book_id}/{asset_type}s/{safe_key}_{digest[:12]}.{ext}"
    url = put_image(key, data, content_type)
    vid = add_asset_version(book_id, asset_type, asset_key, url,
                            image_hash=digest, storage_key=key)
    return vid


def selected_version_image(book_id: str, asset_type: str, asset_key: str) -> str | None:
    """Localized path of the SELECTED version's immutable, content-addressed
    image, or None. The hash key never changes content, so localize's cache is
    safe and every serverless instance resolves the identical bytes — the anchor
    for cross-page consistency."""
    from src.core.store import get_selected_version
    sel = get_selected_version(book_id, asset_type, asset_key)
    if not sel or not sel.get("storage_key"):
        return None
    return localize(sel["storage_key"])


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
