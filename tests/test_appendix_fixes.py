"""Appendix point-fixes from the review.

#2 quality endpoints take the regen mutex (don't score an image being replaced).
#3 character rename drops stale chapter consistency caches.
Final-fixes: manual-QA quality endpoints dual-write to the store.
"""

from __future__ import annotations

import json

import pytest

import src.core.store as _store
import src.routes.editor as editor
import src.routes.generation as _gen
from src.routes.helpers import _active_regens


def test_segment_quality_409_during_regen(client, monkeypatch):
    monkeypatch.setattr(
        "src.routes.generation._load_json",
        lambda bid, fn: {"segments": [{"id": 0, "chapter_idx": 0}]} if fn == "analysis.json" else {},
    )
    _active_regens.add(("b", "segment", 0))
    try:
        assert client.post("/api/book/b/segment/0/quality").status_code == 409
    finally:
        _active_regens.discard(("b", "segment", 0))


def test_special_quality_409_during_regen(client):
    _active_regens.add(("b", "special", "book_cover:0"))
    try:
        assert client.post("/api/book/b/special/book_cover/quality").status_code == 409
    finally:
        _active_regens.discard(("b", "special", "book_cover:0"))


def test_rename_drops_chapter_consistency(monkeypatch, tmp_path):
    monkeypatch.setattr(editor, "GENERATED_DIR", tmp_path)
    monkeypatch.setattr(editor, "_load_json", lambda bid, fn: {})
    monkeypatch.setattr(editor, "_save_json", lambda *a, **k: None)
    ch = tmp_path / "b" / "chapters" / "ch00"
    ch.mkdir(parents=True)
    cons = ch / "consistency.json"
    cons.write_text("{}")

    editor._cascade_character_rename("b", "Alice", "Alicia")

    assert not cons.exists(), "rename must invalidate the chapter consistency cache"


# ---------------------------------------------------------------------------
# Fix 1: check_segment_quality dual-writes quality JSON to the store
# ---------------------------------------------------------------------------

def test_segment_quality_dual_writes_to_gcs(monkeypatch, tmp_path, client):
    """check_segment_quality must persist the quality result to the store
    so _load_quality can find it on a cold serverless instance.
    Asserts via store.get_json — works on both GCS and Firestore backends."""
    import src.config as _cfg
    monkeypatch.setattr(_cfg, "GENERATED_DIR", tmp_path)
    monkeypatch.setattr(_gen, "GENERATED_DIR", tmp_path)

    # Create the image on local disk (required for the quality check endpoint)
    ch_pages = tmp_path / "qbook" / "chapters" / "ch00" / "pages"
    ch_pages.mkdir(parents=True)
    (ch_pages / "page_001.png").write_bytes(b"IMG")

    # Stub _load_json to return a segment
    monkeypatch.setattr(_gen, "_load_json", lambda bid, fn: (
        {"segments": [{"id": 1, "chapter_idx": 0, "text": "hello world " * 5,
                       "characters_in_scene": [], "character_actions": [],
                       "scene_background": "", "scene_summary": "s",
                       "sentiment": "neutral"}]}
        if fn == "analysis.json" else {}
    ))

    # Stub the actual QA call to avoid Gemini
    import src.generation.gemini_consistency_check as gcc
    monkeypatch.setattr(gcc, "check_page_quality",
                        lambda *a, **kw: {"overall_score": 85, "issues": []})

    resp = client.post("/api/book/qbook/segment/1/quality")
    assert resp.status_code == 200, resp.text

    # Assert via the PUBLIC store API — backend-agnostic (GCS or Firestore)
    expected_key = "qbook/chapters/ch00/quality/page_001_quality.json"
    stored = _store.get_json(expected_key)
    assert stored is not None, (
        f"quality JSON must be persisted to the store at '{expected_key}'"
    )
    assert stored["overall_score"] == 85


# ---------------------------------------------------------------------------
# Fix 1: check_special_page_quality dual-writes quality JSON to the store
# ---------------------------------------------------------------------------

def test_special_page_quality_dual_writes_to_gcs(monkeypatch, tmp_path, client):
    """check_special_page_quality must persist the quality result to the store
    so _load_quality can find it on a cold serverless instance.
    Asserts via store.get_json — works on both GCS and Firestore backends."""
    import src.config as _cfg
    import src.routes.editor as _ed
    import src.generation.gemini_consistency_check as gcc

    monkeypatch.setattr(_cfg, "GENERATED_DIR", tmp_path)
    monkeypatch.setattr(_gen, "GENERATED_DIR", tmp_path)

    # Create the special cover image
    special_dir = tmp_path / "spbook" / "special"
    special_dir.mkdir(parents=True)
    (special_dir / "book_cover.png").write_bytes(b"COVER")

    # Stub helpers used inside the endpoint (inline imports)
    monkeypatch.setattr(_ed, "load_special_records", lambda bid: {})
    monkeypatch.setattr(_gen, "load_characters", lambda bid: [], raising=False)
    monkeypatch.setattr("src.routes.helpers.load_characters", lambda bid: [])

    # Stub the actual QA call to avoid Gemini
    monkeypatch.setattr(gcc, "check_page_quality",
                        lambda *a, **kw: {"overall_score": 72, "issues": []})

    resp = client.post("/api/book/spbook/special/book_cover/quality")
    assert resp.status_code == 200, resp.text

    # Assert via the PUBLIC store API — backend-agnostic (GCS or Firestore)
    expected_key = "spbook/special/quality/book_cover_quality.json"
    stored = _store.get_json(expected_key)
    assert stored is not None, (
        f"special quality JSON must be persisted to the store at '{expected_key}'"
    )
    assert stored["overall_score"] == 72
