"""Task 7: version-based stale detection.

A page is stale when the version-id it was generated against differs from the
currently-selected version-id for a character or scene it references.
Legacy pages (no refs) are never falsely marked stale.
"""
import asyncio

import src.core.store as store
import src.routes.generation as gen


def test_page_stale_when_selected_char_version_differs(monkeypatch, tmp_path):
    monkeypatch.setattr("src.routes.generation.GENERATED_DIR", tmp_path)
    b = "stb"
    # analysis: one page (seg 1) in ch0 referencing character "Swallow"
    monkeypatch.setattr(
        "src.routes.generation._load_json",
        lambda book_id, fn: {
            "segments": [
                {
                    "id": 1,
                    "chapter_idx": 0,
                    "characters_in_scene": ["Swallow"],
                    "scene_background": "",
                }
            ]
        }
        if fn == "analysis.json"
        else {},
    )
    # character has two versions; page was generated against v1; user has selected v2
    v1 = store.add_asset_version(b, "character", "Swallow", "u1", image_hash="h1", storage_key="k1")
    v2 = store.add_asset_version(b, "character", "Swallow", "u2", image_hash="h2", storage_key="k2")
    store.set_selected_version(b, "character", "Swallow", v2)
    # chapter_data records the page's provenance = v1
    store.put_json(
        f"{b}/chapters/ch00/chapter_data.json",
        {"pages": [{"page_number": 1, "refs": {"characters": {"Swallow": v1}, "scenes": {}}}]},
    )
    res = asyncio.run(gen.get_stale_pages(b, 0))
    assert res["stale"] and res["stale"][0]["page"] == 1

    # after the user selects v1 (matching the page), it is NOT stale
    store.set_selected_version(b, "character", "Swallow", v1)
    res2 = asyncio.run(gen.get_stale_pages(b, 0))
    assert res2["stale"] == []


def test_only_referenced_page_is_stale(monkeypatch, tmp_path):
    """Two pages: page 1 references the updated character, page 2 does not.
    Only page 1 should be stale.
    """
    monkeypatch.setattr("src.routes.generation.GENERATED_DIR", tmp_path)
    b = "stb2"
    # analysis: two pages in ch0; only seg 1 has "Hero"
    monkeypatch.setattr(
        "src.routes.generation._load_json",
        lambda book_id, fn: {
            "segments": [
                {
                    "id": 1,
                    "chapter_idx": 0,
                    "characters_in_scene": ["Hero"],
                    "scene_background": "",
                },
                {
                    "id": 2,
                    "chapter_idx": 0,
                    "characters_in_scene": [],
                    "scene_background": "",
                },
            ]
        }
        if fn == "analysis.json"
        else {},
    )
    v1 = store.add_asset_version(b, "character", "Hero", "url1", image_hash="hA", storage_key="kA")
    v2 = store.add_asset_version(b, "character", "Hero", "url2", image_hash="hB", storage_key="kB")
    # user has selected v2
    store.set_selected_version(b, "character", "Hero", v2)
    # page 1 was generated against v1 (stale); page 2 has no refs (legacy → not stale)
    store.put_json(
        f"{b}/chapters/ch00/chapter_data.json",
        {
            "pages": [
                {"page_number": 1, "refs": {"characters": {"Hero": v1}, "scenes": {}}},
                {"page_number": 2},  # no refs — legacy page
            ]
        },
    )
    res = asyncio.run(gen.get_stale_pages(b, 0))
    stale_pages = [s["page"] for s in res["stale"]]
    assert stale_pages == [1], f"Expected only page 1 stale, got {stale_pages}"
