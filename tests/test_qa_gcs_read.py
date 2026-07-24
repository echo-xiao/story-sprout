"""Step 3 tests: _load_quality store-first reads (src/routes/editor.py).

Verifies that:
  - _load_quality returns store data when present, even when the local file is absent.
  - _load_quality falls back to the local file when the store has nothing.
  - _load_quality returns None when neither source has it.
  - The segment-history endpoint attaches quality from the store (cold-instance case).

Tests seed and assert via the PUBLIC store API (store.put_json / store.get_json)
so they run on BOTH the GCS and Firestore backends without modification.
"""

from __future__ import annotations

import json

import pytest

import src.core.store as _store
from src.routes.editor import _load_quality


# ---------------------------------------------------------------------------
# Unit tests for _load_quality helper
# ---------------------------------------------------------------------------


def test_load_quality_returns_gcs_data_when_local_missing(tmp_path, monkeypatch):
    """Store has the file; local /tmp is empty (cold instance). Must return store data."""
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)

    rel_key = "book1/chapters/ch00/quality/page_001_quality.json"
    # Seed via the public store API — works on both GCS and Firestore backends.
    _store.put_json(rel_key, {"overall_score": 88})

    result = _load_quality(rel_key)
    assert result is not None
    assert result["overall_score"] == 88


def test_load_quality_falls_back_to_local_file(tmp_path, monkeypatch):
    """Store has nothing; local file present. Must return local data."""
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)

    rel_key = "book1/chapters/ch00/quality/page_001_quality.json"
    local = tmp_path / rel_key
    local.parent.mkdir(parents=True)
    local.write_text(json.dumps({"overall_score": 55}), encoding="utf-8")
    # Store is empty (autouse _fake_store_bucket / _fake_fs_collection give fresh state)

    result = _load_quality(rel_key)
    assert result is not None
    assert result["overall_score"] == 55


def test_load_quality_returns_none_when_neither_source_has_it(tmp_path, monkeypatch):
    """Neither store nor local file — must return None."""
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)

    result = _load_quality("book1/chapters/ch00/quality/missing.json")
    assert result is None


def test_load_quality_gcs_preferred_over_local(tmp_path, monkeypatch):
    """Both store and local exist — store wins (most recent durable state)."""
    rel_key = "book1/chapters/ch00/quality/page_001_quality.json"
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)

    # Seed store with score 99 via public API
    _store.put_json(rel_key, {"overall_score": 99})

    # Also write a local file with a different score
    local = tmp_path / rel_key
    local.parent.mkdir(parents=True)
    local.write_text(json.dumps({"overall_score": 42}), encoding="utf-8")

    result = _load_quality(rel_key)
    assert result["overall_score"] == 99  # store wins


# ---------------------------------------------------------------------------
# Integration test: segment-history endpoint reads quality from store
# ---------------------------------------------------------------------------


@pytest.fixture()
def seg_history_book(monkeypatch, tmp_path, client):
    """A book with a current page in image storage and a quality verdict in the
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

    # Quality is in the JSON store but NOT on disk (simulates cold instance).
    # Seeded via the public store API — works on both GCS and Firestore backends.
    rel_key = "coldbook/chapters/ch00/quality/page_001_quality.json"
    _store.put_json(rel_key, {"overall_score": 77, "page": 1})

    return client


def test_segment_history_reads_quality_from_gcs(seg_history_book):
    """GET /segment/{id}/history must attach quality from the store even when
    the local quality file does not exist (cold serverless instance)."""
    resp = seg_history_book.get("/api/book/coldbook/segment/1/history")
    assert resp.status_code == 200
    images = resp.json()["images"]
    assert images, "expected at least the current image"
    current = next((img for img in images if img["version"] == "current"), None)
    assert current is not None
    assert "quality" in current, "quality must be attached from store"
    assert current["quality"]["overall_score"] == 77


# ---------------------------------------------------------------------------
# Fix 3: _load_quality must not propagate store errors to the carousel
# ---------------------------------------------------------------------------

def test_load_quality_gcs_error_falls_through_to_local_file(tmp_path, monkeypatch):
    """When store.get_json raises (transient blip), _load_quality must NOT
    propagate the exception — it must fall through to the local file fallback
    and return the local data."""
    import src.core.store as _s

    # Make store.get_json raise unconditionally (simulates store down)
    monkeypatch.setattr(_s, "get_json", lambda key: (_ for _ in ()).throw(RuntimeError("store timeout")))
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

    monkeypatch.setattr(_s, "get_json", lambda key: (_ for _ in ()).throw(RuntimeError("store down")))
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)

    # No local file
    result = _load_quality("book1/chapters/ch00/quality/missing.json")
    assert result is None
