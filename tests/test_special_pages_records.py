"""Special pages as first-class editable records (same framework as story pages):
deterministic derivation, legacy-book fallback, PUT roundtrip, naming authority,
and the billing-key error classifier."""

from __future__ import annotations

import json

import pytest

from src.gemini_backend import friendly_gen_error
from src.generation.special_page_data import (
    derive_special_pages, special_file_base, special_key,
)


def _segments():
    return [
        {"id": 1, "chapter_idx": 0, "characters_in_scene": ["Nick", "Gatsby"],
         "scene_background": "West Egg mansion lawn", "scene_summary": "Nick arrives."},
        {"id": 2, "chapter_idx": 0, "characters_in_scene": ["Gatsby"],
         "scene_background": "", "scene_summary": "Gatsby waves."},
        {"id": 3, "chapter_idx": 1, "characters_in_scene": ["Daisy", "Gatsby", "Nick"],
         "scene_background": "Buchanan drawing room", "scene_summary": "Tea at Daisy's."},
    ]


def _ch_map():
    return {
        "0": {"chapter_title": "The Party", "num_segments": 2},
        "1": {"chapter_title": "The Reunion", "num_segments": 1},
    }


def test_derivation_is_deterministic_and_complete():
    a = derive_special_pages("Gatsby", _segments(), _ch_map(), [])
    b = derive_special_pages("Gatsby", _segments(), _ch_map(), [])
    assert a == b  # no randomness, no LLM — legacy fallback must equal preprocess output
    # Book structure by design: ONE front cover, ONE cover per chapter, ONE
    # back cover — and nothing else (chapter endings were cut).
    assert set(a) == {"book_cover", "back_cover", "chapter_cover:0", "chapter_cover:1"}
    assert a["book_cover"]["title_text"] == "Gatsby"
    # characters ranked by mention count
    assert a["book_cover"]["characters_in_scene"][0] == "Gatsby"
    assert a["chapter_cover:0"]["scene_background"] == "West Egg mansion lawn"
    assert a["chapter_cover:1"]["characters_in_scene"][0] in ("Gatsby", "Daisy", "Nick")


def test_file_base_is_single_naming_authority():
    # 1-based file names for 0-based chapters — the regen endpoint, history
    # and restore all resolve through this one function.
    assert special_file_base("chapter_cover", 0) == "chapter_01_cover"
    assert special_file_base("chapter_cover", 2) == "chapter_03_cover"
    assert special_file_base("book_cover") == "book_cover"
    assert special_file_base("nope") is None
    assert special_file_base("chapter_ending", 0) is None  # cut by design
    assert special_key("chapter_cover", 1) == "chapter_cover:1"
    assert special_key("back_cover") == "back_cover"


@pytest.fixture()
def book(monkeypatch, tmp_path):
    """A legacy book on disk: analysis + meta, but NO special_pages.json.

    Task 2B: _load_json now treats the durable store as the single source of
    truth — a successful store read (even returning None) is authoritative and
    local files are NOT consulted.  Data must be seeded into the store so that
    routes reading via _load_json find it.  The local files are also written as
    a best-effort cache for any paths that read directly from disk.
    """
    import src.core.store as store
    monkeypatch.setattr("src.routes.helpers.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    pre = tmp_path / "b1" / "preprocess"
    pre.mkdir(parents=True)
    analysis = {"segments": _segments()}
    meta = {"title": "Gatsby"}
    ch_map = _ch_map()
    (pre / "analysis.json").write_text(json.dumps(analysis))
    (pre / "meta.json").write_text(json.dumps(meta))
    (pre / "chapter_segments.json").write_text(json.dumps(ch_map))
    # Also seed the durable store so _load_json serves them authoritatively.
    store.save_preprocess_file("b1", "analysis.json", analysis)
    store.save_preprocess_file("b1", "meta.json", meta)
    store.save_preprocess_file("b1", "chapter_segments.json", ch_map)
    return tmp_path


def test_get_merges_records_and_put_persists(book, client):
    # GET derives records for a legacy book (no re-preprocess needed)
    r = client.get("/api/book/b1/special-pages")
    assert r.status_code == 200
    pages = {p.get("key"): p for p in r.json()["pages"]}
    assert pages["book_cover"]["characters_in_scene"][0] == "Gatsby"
    assert pages["chapter_cover:1"]["scene_background"] == "Buchanan drawing room"
    # exactly one cover entry per chapter, nothing else per-chapter
    assert "chapter_cover:0" in pages and "chapter_ending:0" not in pages

    # PUT edits one record; first write bootstraps the file with ALL records
    r = client.put("/api/book/b1/special/book_cover",
                   json={"scene_background": "Green light across the bay",
                         "characters_in_scene": ["Gatsby"]})
    assert r.status_code == 200
    saved = json.loads((book / "b1" / "preprocess" / "special_pages.json").read_text())
    assert saved["pages"]["book_cover"]["scene_background"] == "Green light across the bay"
    assert saved["pages"]["book_cover"]["characters_in_scene"] == ["Gatsby"]
    assert "chapter_cover:0" not in saved["pages"]  # only the edited record is stored, not derived siblings

    # GET now serves the edited record, others untouched
    pages = {p.get("key"): p for p in client.get("/api/book/b1/special-pages").json()["pages"]}
    assert pages["book_cover"]["scene_background"] == "Green light across the bay"
    assert pages["chapter_cover:1"]["title_text"] == "The Reunion"

    # unknown type rejected (was a silent no-op claim before)
    assert client.put("/api/book/b1/special/banana", json={}).status_code == 400


def test_history_uses_version_store(book, client):
    """History carousel is built from the version store, not the history/ directory.

    Seeding two versions + selecting v1 must produce two entries (newest-first),
    with the selected one mapped to version="current" and each entry carrying
    its own quality — the same contract as the segment carousel (Task 1/Task 2).
    """
    import src.core.store as store
    asset_key = "book_cover:0"
    v1 = store.add_asset_version(
        "b1", "special", asset_key, "http://example.com/bc_v1.png",
        image_hash="h1", storage_key="sk_v1.png",
    )
    v2 = store.add_asset_version(
        "b1", "special", asset_key, "http://example.com/bc_v2.png",
        image_hash="h2", storage_key="sk_v2.png",
    )
    store.set_version_quality("b1", "special", asset_key, v1, {"overall_score": 70})
    store.set_version_quality("b1", "special", asset_key, v2, {"overall_score": 85})
    store.set_selected_version("b1", "special", asset_key, v1)

    r = client.get("/api/book/b1/special/book_cover/history?chapter=0")
    assert r.status_code == 200
    images = r.json()["images"]
    assert len(images) == 2, f"Expected 2 entries from store; got {images}"
    # Selected version (v1) maps to "current".
    current = [img for img in images if img["version"] == "current"]
    assert len(current) == 1 and current[0]["url"] == "http://example.com/bc_v1.png"
    # Per-version quality is present on both entries.
    by_url = {img["url"]: img.get("quality", {}).get("overall_score") for img in images}
    assert by_url["http://example.com/bc_v1.png"] == 70
    assert by_url["http://example.com/bc_v2.png"] == 85


def test_friendly_error_names_billing_for_free_tier():
    msg = friendly_gen_error([
        "429 RESOURCE_EXHAUSTED ... generate_content_free_tier_requests, limit: 0"])
    assert "BILLING" in msg
    assert friendly_gen_error(["429 rate limit"]).startswith("Gemini rate limit")
    assert friendly_gen_error([]) is None


def test_alias_map_never_rewrites_another_canonical_name():
    # LLM listed "Madame Defarge" (a real character) as Monsieur's alias —
    # mapping it would rewrite her into him across the whole book text.
    from src.preprocessing.pipeline import _build_alias_map
    chars = [
        {"canonical_name": "Monsieur Defarge",
         "aliases": ["Madame Defarge", "the wine-shop keeper"]},
        {"canonical_name": "Madame Defarge", "aliases": ["The Vengeance Knitter"]},
    ]
    amap = _build_alias_map(chars)
    assert "madame defarge" not in amap
    assert amap["the wine-shop keeper"] == "Monsieur Defarge"
    assert amap["the vengeance knitter"] == "Madame Defarge"
