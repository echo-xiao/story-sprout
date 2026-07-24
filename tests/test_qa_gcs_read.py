"""Step 3 tests: _load_quality GCS-first reads (src/routes/editor.py).

Verifies that:
  - _load_quality returns GCS data when present, even when the local file is absent.
  - _load_quality falls back to the local file when GCS has nothing.
  - _load_quality returns None when neither source has the key.
  - The segment-history endpoint attaches quality from GCS (cold-instance case).
"""

from __future__ import annotations

import json

import pytest

import src.config as _cfg
import src.core.store as _store
from src.routes.editor import _load_quality

_SKIP_ON_FIRESTORE = pytest.mark.skipif(
    _cfg.STORE_BACKEND == "firestore",
    reason=(
        "GCS-specific: seeds data via _install_bucket (GCS backing dict). "
        "On the Firestore backend store.get_json reads from the Firestore fake, "
        "not the GCS bucket, so these fixtures cannot populate the read path. "
        "Firestore-backend QA-store coverage lives in test_qa_per_version.py."
    ),
)


# ---------------------------------------------------------------------------
# Unit tests for _load_quality helper
# ---------------------------------------------------------------------------


def _install_bucket(monkeypatch, backing: dict):
    class _Blob:
        def __init__(self, key):
            self._key = key

        def exists(self):
            return self._key in backing

        def download_as_text(self):
            return backing[self._key]

        def upload_from_string(self, data, content_type="application/json"):
            backing[self._key] = data

    class _Bucket:
        def blob(self, key):
            return _Blob(key)

    monkeypatch.setattr(_store, "_bucket", lambda: _Bucket())


@_SKIP_ON_FIRESTORE
def test_load_quality_returns_gcs_data_when_local_missing(tmp_path, monkeypatch):
    """GCS has the file; local /tmp is empty (cold instance). Must return GCS data."""
    backing = {}
    _install_bucket(monkeypatch, backing)
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)

    rel_key = "book1/chapters/ch00/quality/page_001_quality.json"
    backing[rel_key] = json.dumps({"overall_score": 88})

    result = _load_quality(rel_key)
    assert result is not None
    assert result["overall_score"] == 88


def test_load_quality_falls_back_to_local_file(tmp_path, monkeypatch):
    """GCS has nothing; local file present. Must return local data."""
    _install_bucket(monkeypatch, {})  # empty GCS
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)

    rel_key = "book1/chapters/ch00/quality/page_001_quality.json"
    local = tmp_path / rel_key
    local.parent.mkdir(parents=True)
    local.write_text(json.dumps({"overall_score": 55}), encoding="utf-8")

    result = _load_quality(rel_key)
    assert result is not None
    assert result["overall_score"] == 55


def test_load_quality_returns_none_when_neither_source_has_it(tmp_path, monkeypatch):
    """Neither GCS nor local file — must return None."""
    _install_bucket(monkeypatch, {})
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)

    result = _load_quality("book1/chapters/ch00/quality/missing.json")
    assert result is None


@_SKIP_ON_FIRESTORE
def test_load_quality_gcs_preferred_over_local(tmp_path, monkeypatch):
    """Both GCS and local exist — GCS wins (most recent durable state)."""
    rel_key = "book1/chapters/ch00/quality/page_001_quality.json"
    backing = {rel_key: json.dumps({"overall_score": 99})}
    _install_bucket(monkeypatch, backing)
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)

    local = tmp_path / rel_key
    local.parent.mkdir(parents=True)
    local.write_text(json.dumps({"overall_score": 42}), encoding="utf-8")

    result = _load_quality(rel_key)
    assert result["overall_score"] == 99  # GCS wins


# ---------------------------------------------------------------------------
# Integration test: segment-history endpoint reads quality from GCS
# ---------------------------------------------------------------------------


@pytest.fixture()
def seg_history_book(monkeypatch, tmp_path, client):
    """A book with a current page in GCS storage and a quality verdict in the
    JSON store (but NOT the local /tmp directory — mimics a cold serverless
    instance where /tmp was wiped)."""
    from tests.conftest import make_segment
    import src.core.storage as _storage

    analysis = {"segments": [make_segment(1, ch_idx=0)]}
    monkeypatch.setattr("src.routes.editor._load_json",
                        lambda book_id, filename: analysis if filename == "analysis.json" else {})

    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.routes.helpers.GENERATED_DIR", tmp_path)
    # storage.exists() and storage.image_url() read from GENERATED_DIR in local mode
    monkeypatch.setattr(_storage, "GENERATED_DIR", tmp_path)
    monkeypatch.setattr(_storage, "GCS_BUCKET", "", raising=False)

    # Create local page so storage.exists() returns True in local fallback mode
    page_dir = tmp_path / "coldbook" / "chapters" / "ch00" / "pages"
    page_dir.mkdir(parents=True)
    (page_dir / "page_001.png").write_bytes(b"IMG")

    # Quality is in the JSON store (GCS) but NOT on disk (simulates cold instance)
    rel_key = "coldbook/chapters/ch00/quality/page_001_quality.json"
    backing = {rel_key: json.dumps({"overall_score": 77, "page": 1})}
    _install_bucket(monkeypatch, backing)

    return client


@_SKIP_ON_FIRESTORE
def test_segment_history_reads_quality_from_gcs(seg_history_book):
    """GET /segment/{id}/history must attach quality from the GCS store even when
    the local quality file does not exist (cold serverless instance)."""
    resp = seg_history_book.get("/api/book/coldbook/segment/1/history")
    assert resp.status_code == 200
    images = resp.json()["images"]
    assert images, "expected at least the current image"
    current = next((img for img in images if img["version"] == "current"), None)
    assert current is not None
    assert "quality" in current, "quality must be attached from GCS store"
    assert current["quality"]["overall_score"] == 77


# ---------------------------------------------------------------------------
# Fix 3: _load_quality must not propagate GCS errors to the carousel
# ---------------------------------------------------------------------------

def test_load_quality_gcs_error_falls_through_to_local_file(tmp_path, monkeypatch):
    """When store.get_json raises (transient GCS blip), _load_quality must NOT
    propagate the exception — it must fall through to the local file fallback
    and return the local data."""
    import src.core.store as _s

    # Make store.get_json raise unconditionally (simulates GCS down)
    monkeypatch.setattr(_s, "get_json", lambda key: (_ for _ in ()).throw(RuntimeError("GCS timeout")))
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)

    rel_key = "book1/chapters/ch00/quality/page_001_quality.json"
    local = tmp_path / rel_key
    local.parent.mkdir(parents=True)
    local.write_text(json.dumps({"overall_score": 61}), encoding="utf-8")

    # Must NOT raise; must return local file data
    result = _load_quality(rel_key)
    assert result is not None
    assert result["overall_score"] == 61


def test_load_quality_gcs_error_returns_none_when_no_local(tmp_path, monkeypatch):
    """When store.get_json raises AND there is no local file, _load_quality must
    return None — never propagate the exception."""
    import src.core.store as _s

    monkeypatch.setattr(_s, "get_json", lambda key: (_ for _ in ()).throw(RuntimeError("GCS down")))
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)

    # No local file
    result = _load_quality("book1/chapters/ch00/quality/missing.json")
    assert result is None
