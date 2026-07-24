"""Task 5: QA is stored per-version so it survives regens and version-switches.

TDD: tests run RED before implementation, GREEN after.

(a) Store primitive: set_version_quality writes QA onto the exact version;
    different versions keep their own QA data independently.
(b) Page-regen integration: stubbing Gemini QA to return overall_score=88
    results in the recorded page version carrying quality.overall_score == 88.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import src.core.store as store
import src.core.storage as storage


# ---------------------------------------------------------------------------
# (a) Store primitive
# ---------------------------------------------------------------------------

def test_quality_stored_and_read_per_version():
    b = "qav"
    v1 = store.add_asset_version(b, "page", "ch00:p001", "u1", image_hash="h1", storage_key="k1")
    v2 = store.add_asset_version(b, "page", "ch00:p001", "u2", image_hash="h2", storage_key="k2")
    assert store.set_version_quality(b, "page", "ch00:p001", v1, {"overall_score": 80}) is True
    assert store.set_version_quality(b, "page", "ch00:p001", v2, {"overall_score": 95}) is True
    versions = store.list_asset_versions(b, "page", "ch00:p001")["versions"]
    by_id = {v["id"]: v for v in versions}
    assert by_id[v1]["quality"]["overall_score"] == 80
    assert by_id[v2]["quality"]["overall_score"] == 95  # each version keeps ITS OWN QA


def test_set_version_quality_missing_version_returns_false():
    """Returns False when the version id doesn't exist (doesn't raise)."""
    b = "qav_miss"
    store.add_asset_version(b, "page", "ch00:p002", "u1", image_hash="hx", storage_key="kx")
    ok = store.set_version_quality(b, "page", "ch00:p002", "nonexistent_id", {"overall_score": 50})
    assert ok is False


def test_set_version_quality_missing_asset_returns_false():
    """Returns False when the asset key itself doesn't exist yet."""
    ok = store.set_version_quality("qav_no_asset", "page", "ch00:p999", "any_id", {"overall_score": 50})
    assert ok is False


# ---------------------------------------------------------------------------
# (b) Page-regen integration: after regen, the recorded version carries QA
# ---------------------------------------------------------------------------

BOOK_ID = "testbook_qa_ver"
SEG_ID = 1
CH_IDX = 0
PAGE_NUM = 1


@pytest.fixture()
def page_regen_env(monkeypatch, tmp_path):
    """Stub out GCS, Gemini, and file-system so the regen endpoint runs fast."""
    # Point GENERATED_DIR at tmp_path.
    monkeypatch.setattr("src.routes.generation.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GCS_BUCKET", "")

    # Seed analysis.json — _load_json reads from the durable store first (Task 2B:
    # the store is the single source of truth; local files are only consulted when
    # the store raises).  We must seed the store AND the local file so that both
    # the store-read path and any fast-path local consumers (e.g. generators that
    # read the freshly written local mirror) see correct data.
    analysis = {
        "segments": [
            {
                "id": SEG_ID,
                "chapter_idx": CH_IDX,
                "text": "Once upon a time there was a brave knight.",
                "simplified_text": "A brave knight lived long ago.",
                "characters_in_scene": [],
                "character_actions": [],
                "scene_background": "",
                "scene_summary": "A brave knight.",
                "sentiment": "neutral",
            }
        ]
    }
    book_dir = tmp_path / BOOK_ID
    preprocess_dir = book_dir / "preprocess"
    preprocess_dir.mkdir(parents=True)
    (preprocess_dir / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
    (book_dir / "characters.json").write_text("[]", encoding="utf-8")

    # Seed the store so _load_json returns the fresh data (not local-only).
    store.save_preprocess_file(BOOK_ID, "analysis.json", analysis)

    # Also patch helpers.GENERATED_DIR
    monkeypatch.setattr("src.routes.helpers.GENERATED_DIR", tmp_path)

    # Create the chapter pages directory and a fake page image.
    pages_dir = tmp_path / BOOK_ID / "chapters" / f"ch{CH_IDX:02d}" / "pages"
    pages_dir.mkdir(parents=True)
    page_img = pages_dir / f"page_{PAGE_NUM:03d}.png"
    page_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)

    # Stub generate_illustrations to write a fresh page image (mimicking real gen).
    def _fake_generate_illustrations(page_prompts, character_sheets, book_id,
                                     style_ref=None, pages_dir=None,
                                     correction_feedback=None, **kw):
        if pages_dir:
            import pathlib
            p = pathlib.Path(pages_dir) / f"page_{PAGE_NUM:03d}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)

    monkeypatch.setattr(
        "src.generation.illustration.generate_illustrations",
        _fake_generate_illustrations,
    )

    # Stub qa_and_self_correct to return a known QA result.
    def _fake_qa_and_self_correct(**kwargs):
        return {"overall_score": 88, "details": "stubbed"}

    monkeypatch.setattr(
        "src.generation.page_service.qa_and_self_correct",
        _fake_qa_and_self_correct,
    )

    # Stub load_characters.
    monkeypatch.setattr(
        "src.routes.helpers.load_characters",
        lambda book_id: [],
    )

    # Stub update_chapter_data_page (no-op).
    monkeypatch.setattr(
        "src.routes.helpers.update_chapter_data_page",
        lambda *a, **kw: None,
    )

    # Stub gemini_backend helpers used by the outer _regen wrapper.
    import src.gemini_backend as gb
    monkeypatch.setattr(gb, "set_user_api_key", lambda key: None)
    monkeypatch.setattr(gb, "reset_user_api_key", lambda token: None)
    monkeypatch.setattr(gb, "set_gen_error_box", lambda box: None)
    monkeypatch.setattr(gb, "reset_gen_error_box", lambda token: None)
    monkeypatch.setattr(gb, "friendly_gen_error", lambda box: "")

    return tmp_path


def test_page_regen_attaches_qa_to_recorded_version(monkeypatch, page_regen_env):
    """After page regen with a stubbed QA score of 88, the store version
    must carry quality.overall_score == 88.
    """
    import src.routes.generation as gen_mod
    from src.routes.helpers import _active_regens
    from fastapi import BackgroundTasks

    _active_regens.discard((BOOK_ID, "segment", SEG_ID))

    bt = BackgroundTasks()
    asyncio.run(
        gen_mod.regenerate_segment_illustration(
            book_id=BOOK_ID,
            seg_id=SEG_ID,
            background_tasks=bt,
            user_key="",
        )
    )
    asyncio.run(bt())

    # The version in the store must carry quality.
    versions = store.list_asset_versions(BOOK_ID, "page", f"ch{CH_IDX:02d}:p{PAGE_NUM:03d}")["versions"]
    assert len(versions) >= 1, "Expected at least one version recorded after regen"
    latest = versions[-1]
    assert "quality" in latest, (
        f"Expected 'quality' key in recorded version; got keys: {list(latest.keys())}"
    )
    assert latest["quality"]["overall_score"] == 88, (
        f"Expected overall_score=88; got {latest['quality']}"
    )
