"""Task 1: segment history carousel built from version store (TDD).

Asserts:
(a) get_segment_illustration_history returns one entry per stored version,
    the selected version maps to version=="current", EACH entry carries its
    own quality from the store.
(b) restore_segment_version(seg, other_vid) delegates to set_selected_version
    so get_selected_version(...).id == other_vid afterwards.

Mirrors test_stale_versioned.py's stubbing of _load_json for analysis data
so segment_page_num can resolve the page number.
"""
from __future__ import annotations

import asyncio

import pytest

import src.core.store as store
import src.routes.editor as editor


BOOK = "pcv_book"
SEG_ID = 3
CH_IDX = 0
PAGE_NUM = 1  # first segment of ch0 → page 1
ASSET_KEY = f"ch{CH_IDX:02d}:p{PAGE_NUM:03d}"


@pytest.fixture()
def book_env(monkeypatch, tmp_path):
    """A minimal single-segment book with two page versions in the store."""
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.routes.helpers.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)

    analysis = {
        "segments": [
            {
                "id": SEG_ID,
                "chapter_idx": CH_IDX,
                "text": " ".join(f"word{i}" for i in range(20)),
                "characters_in_scene": [],
                "character_actions": [],
                "scene_background": "",
                "scene_summary": "test summary",
                "sentiment": "neutral",
            }
        ]
    }
    monkeypatch.setattr(
        "src.routes.editor._load_json",
        lambda book_id, fn: analysis if fn == "analysis.json" else {},
    )

    # Seed two page versions in the store.
    v1 = store.add_asset_version(
        BOOK, "page", ASSET_KEY, "http://example.com/v1.png",
        image_hash="hash1", storage_key="k1.png",
    )
    v2 = store.add_asset_version(
        BOOK, "page", ASSET_KEY, "http://example.com/v2.png",
        image_hash="hash2", storage_key="k2.png",
    )
    # Per-version quality.
    store.set_version_quality(BOOK, "page", ASSET_KEY, v1, {"overall_score": 71})
    store.set_version_quality(BOOK, "page", ASSET_KEY, v2, {"overall_score": 88})
    # Select v1 as the "current" selected version.
    store.set_selected_version(BOOK, "page", ASSET_KEY, v1)

    return {"v1": v1, "v2": v2}


# ---------------------------------------------------------------------------
# (a) history carousel
# ---------------------------------------------------------------------------

def test_history_returns_two_entries(client, book_env):
    resp = client.get(f"/api/book/{BOOK}/segment/{SEG_ID}/history")
    assert resp.status_code == 200
    images = resp.json()["images"]
    assert len(images) == 2, f"Expected 2 entries, got {len(images)}: {images}"


def test_selected_version_maps_to_current(client, book_env):
    resp = client.get(f"/api/book/{BOOK}/segment/{SEG_ID}/history")
    assert resp.status_code == 200
    images = resp.json()["images"]
    current_entries = [img for img in images if img["version"] == "current"]
    assert len(current_entries) == 1, (
        f"Expected exactly 1 entry with version='current'; got {current_entries}"
    )
    # The selected version is v1.
    assert current_entries[0]["url"] == "http://example.com/v1.png"


def test_each_entry_carries_own_quality(client, book_env):
    resp = client.get(f"/api/book/{BOOK}/segment/{SEG_ID}/history")
    assert resp.status_code == 200
    images = resp.json()["images"]
    # Build a map: url → quality score.
    by_url = {img["url"]: img.get("quality", {}).get("overall_score") for img in images}
    assert by_url.get("http://example.com/v1.png") == 71, (
        f"v1 should carry score 71; got {by_url}"
    )
    assert by_url.get("http://example.com/v2.png") == 88, (
        f"v2 should carry score 88; got {by_url}"
    )


def test_non_selected_version_has_version_id(client, book_env):
    v2 = book_env["v2"]
    resp = client.get(f"/api/book/{BOOK}/segment/{SEG_ID}/history")
    assert resp.status_code == 200
    images = resp.json()["images"]
    non_current = [img for img in images if img["version"] != "current"]
    assert len(non_current) == 1
    assert non_current[0]["version"] == v2


# ---------------------------------------------------------------------------
# (b) restore_segment_version switches the selected version
# ---------------------------------------------------------------------------

def test_restore_switches_selected_version(client, book_env, monkeypatch):
    """restore_segment_version(seg, v2) must make v2 the selected version."""
    v2 = book_env["v2"]

    # _promote_selected calls storage.get_image; stub it out.
    monkeypatch.setattr("src.core.storage.get_image", lambda key: None)

    resp = client.post(
        f"/api/book/{BOOK}/segment/{SEG_ID}/restore-version?version={v2}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "restored"

    # The store must now point at v2.
    sel = store.get_selected_version(BOOK, "page", ASSET_KEY)
    assert sel is not None
    assert sel["id"] == v2, f"Expected selected={v2}, got {sel['id']}"


def test_restore_unknown_version_returns_404(client, book_env, monkeypatch):
    """Passing a version id that doesn't exist must return 404."""
    monkeypatch.setattr("src.core.storage.get_image", lambda key: None)
    resp = client.post(
        f"/api/book/{BOOK}/segment/{SEG_ID}/restore-version?version=nonexistent_id"
    )
    assert resp.status_code == 404
