"""编辑器全环节联动一致: selection + reference + QA + stale all agree, per asset type.

Tests the end-to-end interlocking of:
  1. Character coherence — selection/reference/_sheets_for/stale flip together
  2. Scene coherence — mirror of #1 via _find_scene_sheet + get_stale_pages
  3. QA travels with the version — per-version QA survives independently
  4. History endpoint per-version QA — url-key lookup verification
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import src.core.store as store
import src.core.storage as storage
import src.routes.generation as gen
import src.generation.illustration as illus


def _img(tmp: Path, key: str, data: bytes) -> str:
    """Write a fake image file under tmp and return its relative key."""
    p = tmp / key
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return key


# ---------------------------------------------------------------------------
# Test 1: Character coherence across regen + re-select
# ---------------------------------------------------------------------------

def test_character_ref_stable_across_regen_and_stale_agrees(monkeypatch, tmp_path):
    """Select V1 → regen appends V2 (sticky keeps V1) → _sheets_for returns V1
    for two calls (identical); page with provenance V1 is NOT stale.
    Then set_selected_version(V2) → _sheets_for returns V2 AND page becomes stale.
    Proves: reference + stale flip together on selection change.
    """
    monkeypatch.setattr("src.routes.generation.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    b = "coh_char"

    # Seed V1 image and register as version
    k1 = _img(tmp_path, f"{b}/characters/Swallow_1111111111111111111111111111111111111111111111111111111111111111.png", b"V1")
    v1 = store.add_asset_version(b, "character", "Swallow", "u1",
                                 image_hash="1111111111111111111111111111111111111111111111111111111111111111",
                                 storage_key=k1)
    store.set_selected_version(b, "character", "Swallow", v1)

    # Simulate a later regen that appends V2 — selection is sticky on V1
    k2 = _img(tmp_path, f"{b}/characters/Swallow_2222222222222222222222222222222222222222222222222222222222222222.png", b"V2")
    v2 = store.add_asset_version(b, "character", "Swallow", "u2",
                                 image_hash="2222222222222222222222222222222222222222222222222222222222222222",
                                 storage_key=k2)

    # REFERENCE: two calls return the same V1 path
    a = gen._sheets_for(b, ["Swallow"])
    c = gen._sheets_for(b, ["Swallow"])
    assert len(a) == 1, "Expected one sheet entry"
    assert Path(a[0]["sheet_path"]).read_bytes() == b"V1", "Reference must use V1 (selected)"
    assert a[0]["sheet_path"] == c[0]["sheet_path"], "Two reference calls must return identical path"

    # STALE: page with provenance V1 is NOT stale (selection is still V1)
    monkeypatch.setattr(
        "src.routes.generation._load_json",
        lambda book_id, fn: {
            "segments": [{
                "id": 1,
                "chapter_idx": 0,
                "characters_in_scene": ["Swallow"],
                "scene_background": "",
            }]
        } if fn == "analysis.json" else {},
    )
    store.put_json(
        f"{b}/chapters/ch00/chapter_data.json",
        {"pages": [{"page_number": 1, "refs": {"characters": {"Swallow": v1}, "scenes": {}}}]},
    )
    stale_result = asyncio.run(gen.get_stale_pages(b, 0))
    assert stale_result["stale"] == [], (
        f"Page with V1 provenance and V1 selected must NOT be stale; got {stale_result['stale']}"
    )

    # Re-select V2: reference AND stale BOTH flip together
    store.set_selected_version(b, "character", "Swallow", v2)

    ref_after = gen._sheets_for(b, ["Swallow"])
    assert len(ref_after) == 1
    assert Path(ref_after[0]["sheet_path"]).read_bytes() == b"V2", (
        "After selecting V2, _sheets_for must return V2 image"
    )

    stale_after = asyncio.run(gen.get_stale_pages(b, 0))
    assert len(stale_after["stale"]) == 1, (
        f"After selecting V2, page with V1 provenance must be stale; got {stale_after['stale']}"
    )
    assert stale_after["stale"][0]["page"] == 1


# ---------------------------------------------------------------------------
# Test 2: Scene coherence across regen + re-select
# ---------------------------------------------------------------------------

def test_scene_ref_stable_across_regen_and_stale_agrees(monkeypatch, tmp_path):
    """Mirror of test 1 for scene: select V1 → regen appends V2 (sticky keeps V1)
    → _find_scene_sheet returns V1; page with V1 provenance is NOT stale.
    Then set_selected_version(V2) → _find_scene_sheet returns V2 AND page stale.
    Proves: scene reference + stale flip together on selection change.
    """
    monkeypatch.setattr("src.generation.illustration.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.routes.generation.GENERATED_DIR", tmp_path)
    b = "coh_scene"
    scene_name = "The Garden"

    # Seed llm_locations.json in the store so _find_scene_sheet can find the location
    store.save_preprocess_file(b, "llm_locations.json", {
        "locations": [{"name": scene_name, "aliases": []}]
    })

    # Seed V1 scene image and register as version
    k1 = _img(tmp_path, f"{b}/scenes/The_Garden_1111111111111111111111111111111111111111111111111111111111111111.png", b"SCENE-V1")
    v1 = store.add_asset_version(b, "scene", scene_name, "su1",
                                 image_hash="1111111111111111111111111111111111111111111111111111111111111111",
                                 storage_key=k1)
    store.set_selected_version(b, "scene", scene_name, v1)

    # Simulate a later regen that appends V2 — sticky selection keeps V1
    k2 = _img(tmp_path, f"{b}/scenes/The_Garden_2222222222222222222222222222222222222222222222222222222222222222.png", b"SCENE-V2")
    v2 = store.add_asset_version(b, "scene", scene_name, "su2",
                                 image_hash="2222222222222222222222222222222222222222222222222222222222222222",
                                 storage_key=k2)

    # REFERENCE: two calls return the same V1 scene path
    scene_bg = f"a page set in {scene_name} at dawn"
    s1 = illus._find_scene_sheet(b, scene_bg)
    s2 = illus._find_scene_sheet(b, scene_bg)
    assert s1 is not None, "_find_scene_sheet must return V1 path"
    assert Path(s1).read_bytes() == b"SCENE-V1", "Scene reference must use V1 (selected)"
    assert s1 == s2, "Two scene reference calls must return identical path"

    # STALE: page with V1 scene provenance is NOT stale (selection is still V1)
    monkeypatch.setattr(
        "src.routes.generation._load_json",
        lambda book_id, fn: {
            "segments": [{
                "id": 1,
                "chapter_idx": 0,
                "characters_in_scene": [],
                "scene_background": scene_bg,
            }]
        } if fn == "analysis.json" else {},
    )
    store.put_json(
        f"{b}/chapters/ch00/chapter_data.json",
        {"pages": [{"page_number": 1, "refs": {"characters": {}, "scenes": {scene_name: v1}}}]},
    )
    stale_result = asyncio.run(gen.get_stale_pages(b, 0))
    assert stale_result["stale"] == [], (
        f"Page with scene V1 provenance and V1 selected must NOT be stale; got {stale_result['stale']}"
    )

    # Re-select V2: scene reference AND stale BOTH flip together
    store.set_selected_version(b, "scene", scene_name, v2)

    ref_after = illus._find_scene_sheet(b, scene_bg)
    assert ref_after is not None
    assert Path(ref_after).read_bytes() == b"SCENE-V2", (
        "After selecting V2, _find_scene_sheet must return V2 image"
    )

    stale_after = asyncio.run(gen.get_stale_pages(b, 0))
    assert len(stale_after["stale"]) == 1, (
        f"After selecting V2, page with V1 scene provenance must be stale; got {stale_after['stale']}"
    )
    assert stale_after["stale"][0]["page"] == 1


# ---------------------------------------------------------------------------
# Test 3: QA travels with the version (store primitive)
# ---------------------------------------------------------------------------

def test_qa_travels_with_the_version():
    """Two page versions each carry their own QA independently.
    V1 gets score 77, V2 gets score 93.
    list_asset_versions returns each version's own quality — they never bleed across.
    Proves: QA is permanently bound to the version that earned it.
    """
    b = "coh_qa"
    v1 = store.add_asset_version(b, "page", "ch00:p001", "url1",
                                 image_hash="hqa1", storage_key="kqa1")
    store.set_version_quality(b, "page", "ch00:p001", v1, {"overall_score": 77})

    v2 = store.add_asset_version(b, "page", "ch00:p001", "url2",
                                 image_hash="hqa2", storage_key="kqa2")
    store.set_version_quality(b, "page", "ch00:p001", v2, {"overall_score": 93})

    vs = {v["id"]: v for v in store.list_asset_versions(b, "page", "ch00:p001")["versions"]}

    assert vs[v1]["quality"]["overall_score"] == 77, (
        f"V1 must carry score 77; got {vs[v1].get('quality')}"
    )
    assert vs[v2]["quality"]["overall_score"] == 93, (
        f"V2 must carry score 93; got {vs[v2].get('quality')}"
    )
    # Ensure QA didn't bleed
    assert vs[v1]["quality"]["overall_score"] != vs[v2]["quality"]["overall_score"]


# ---------------------------------------------------------------------------
# Test 4: History endpoint per-version QA — url-key lookup verification
# ---------------------------------------------------------------------------

def test_history_endpoint_per_version_qa_attaches(monkeypatch, tmp_path):
    """Verify that the history endpoint (get_segment_illustration_history) attaches
    per-version QA to carousel entries via the url-key lookup.

    Strategy: register two page versions with URLs that exactly match what
    storage.image_url() returns for the page's file key, set per-version quality
    on each, then call the real endpoint and assert quality is present.

    The url-key lookup (_quality_by_url) maps v["url"] -> v["quality"].
    The carousel entry url = storage.image_url(pdir + "/page_001.png").
    For the lookup to succeed, the version URL must equal storage.image_url(storage_key).
    Since GCS_BUCKET is "" (local mode), image_url(key) = f"/static/{key}".
    We seed the version with url = /static/{ck} matching the current page key.
    """
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.routes.helpers.GENERATED_DIR", tmp_path)

    b = "coh_hist"
    ch_idx = 0
    page_num = 1
    asset_key = f"ch{ch_idx:02d}:p{page_num:03d}"

    # Seed analysis.json so the endpoint can find seg_id=1 -> page 1
    store.save_preprocess_file(b, "analysis.json", {
        "segments": [{
            "id": 1,
            "chapter_idx": ch_idx,
            "characters_in_scene": [],
            "scene_background": "",
            "text": "test",
        }]
    })

    # The current page file key (what the endpoint checks for existence)
    pdir = f"{b}/chapters/ch{ch_idx:02d}/pages"
    current_ck = f"{pdir}/page_{page_num:03d}.png"

    # Write the current page image file so storage.exists(current_ck) is True
    page_file = tmp_path / current_ck
    page_file.parent.mkdir(parents=True, exist_ok=True)
    page_file.write_bytes(b"CURRENT-PAGE")

    # The URL the endpoint will compute for the current page
    current_url = storage.image_url(current_ck)  # "/static/{b}/chapters/ch00/pages/page_001.png"

    # Also seed a history image
    hdir = f"{b}/chapters/ch{ch_idx:02d}/history"
    hist_ts = "9999999999"
    hist_ck = f"{hdir}/page_{page_num:03d}_{hist_ts}.png"
    hist_file = tmp_path / hist_ck
    hist_file.parent.mkdir(parents=True, exist_ok=True)
    hist_file.write_bytes(b"HISTORY-PAGE")
    hist_url = storage.image_url(hist_ck)

    # Register both as versions with URLs matching what the endpoint will compute
    v1 = store.add_asset_version(b, "page", asset_key, hist_url,
                                 image_hash="histhash1", storage_key=hist_ck)
    store.set_version_quality(b, "page", asset_key, v1, {"overall_score": 72})

    v2 = store.add_asset_version(b, "page", asset_key, current_url,
                                 image_hash="currhash2", storage_key=current_ck)
    store.set_version_quality(b, "page", asset_key, v2, {"overall_score": 91})

    # Patch _load_json for the endpoint (analysis.json already in store, but
    # helpers._load_json falls through to store.load_preprocess_file which works)
    from src.app import app
    from fastapi.testclient import TestClient
    client = TestClient(app, raise_server_exceptions=True)

    resp = client.get(f"/api/book/{b}/segment/1/history")
    assert resp.status_code == 200, f"History endpoint returned {resp.status_code}: {resp.text}"
    data = resp.json()
    images = data["images"]
    assert len(images) >= 1, f"Expected at least one image in carousel; got {images}"

    # Find the current image entry
    current_entries = [img for img in images if img.get("version") == "current"]
    assert len(current_entries) == 1, f"Expected exactly one 'current' entry; got {images}"
    current_entry = current_entries[0]

    # The url-key lookup: current_url must be in _quality_by_url (built from version records)
    assert "quality" in current_entry, (
        f"Per-version QA did NOT attach to current carousel entry.\n"
        f"current_url={current_url!r}\n"
        f"Version records: {store.list_asset_versions(b, 'page', asset_key)['versions']}\n"
        f"Entry: {current_entry}\n"
        "BUG: The url-key lookup in get_segment_illustration_history misses because "
        "the version URL (content-addressed storage key) differs from the page file URL."
    )
    assert current_entry["quality"]["overall_score"] == 91, (
        f"Expected score 91 for current entry; got {current_entry.get('quality')}"
    )

    # Historical image entries should also carry QA
    hist_entries = [img for img in images if img.get("version") == hist_ts]
    assert len(hist_entries) == 1, f"Expected history entry with version={hist_ts}; got {images}"
    hist_entry = hist_entries[0]
    assert "quality" in hist_entry, (
        f"Per-version QA did NOT attach to history carousel entry.\n"
        f"hist_url={hist_url!r}\n"
        f"Entry: {hist_entry}"
    )
    assert hist_entry["quality"]["overall_score"] == 72, (
        f"Expected score 72 for history entry; got {hist_entry.get('quality')}"
    )
