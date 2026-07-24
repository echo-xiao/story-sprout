"""Task 2: special-page history carousel built from version store (TDD).

Asserts:
(a) get_special_page_history returns one entry per stored version,
    the selected version maps to version=="current", EACH entry carries its
    own quality from the store.
(b) restore_special_page_version(page_type, v2) delegates to set_selected_version
    so get_selected_version(...).id == v2 afterwards.

Mirrors test_page_carousel_versions.py's pattern but for asset_type "special"
and asset_key = f"{page_type}:{chapter}".
"""
from __future__ import annotations

import asyncio

import pytest

import src.core.store as store
import src.routes.editor as editor


BOOK = "scv_book"
PAGE_TYPE = "book_cover"
CHAPTER = 0
ASSET_KEY = f"{PAGE_TYPE}:{CHAPTER}"


@pytest.fixture()
def book_env(monkeypatch, tmp_path):
    """A minimal book with two special-page versions in the store."""
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.routes.helpers.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)

    # Seed two special versions in the store.
    v1 = store.add_asset_version(
        BOOK, "special", ASSET_KEY, "http://example.com/special_v1.png",
        image_hash="shash1", storage_key="sk1.png",
    )
    v2 = store.add_asset_version(
        BOOK, "special", ASSET_KEY, "http://example.com/special_v2.png",
        image_hash="shash2", storage_key="sk2.png",
    )
    # Per-version quality.
    store.set_version_quality(BOOK, "special", ASSET_KEY, v1, {"overall_score": 65})
    store.set_version_quality(BOOK, "special", ASSET_KEY, v2, {"overall_score": 90})
    # Select v1 as the "current" selected version.
    store.set_selected_version(BOOK, "special", ASSET_KEY, v1)

    return {"v1": v1, "v2": v2}


# ---------------------------------------------------------------------------
# (a) history carousel
# ---------------------------------------------------------------------------

def test_special_history_returns_two_entries(client, book_env):
    resp = client.get(f"/api/book/{BOOK}/special/{PAGE_TYPE}/history?chapter={CHAPTER}")
    assert resp.status_code == 200
    images = resp.json()["images"]
    assert len(images) == 2, f"Expected 2 entries, got {len(images)}: {images}"


def test_special_selected_version_maps_to_current(client, book_env):
    resp = client.get(f"/api/book/{BOOK}/special/{PAGE_TYPE}/history?chapter={CHAPTER}")
    assert resp.status_code == 200
    images = resp.json()["images"]
    current_entries = [img for img in images if img["version"] == "current"]
    assert len(current_entries) == 1, (
        f"Expected exactly 1 entry with version='current'; got {current_entries}"
    )
    # The selected version is v1.
    assert current_entries[0]["url"] == "http://example.com/special_v1.png"


def test_special_each_entry_carries_own_quality(client, book_env):
    resp = client.get(f"/api/book/{BOOK}/special/{PAGE_TYPE}/history?chapter={CHAPTER}")
    assert resp.status_code == 200
    images = resp.json()["images"]
    # Build a map: url → quality score.
    by_url = {img["url"]: img.get("quality", {}).get("overall_score") for img in images}
    assert by_url.get("http://example.com/special_v1.png") == 65, (
        f"v1 should carry score 65; got {by_url}"
    )
    assert by_url.get("http://example.com/special_v2.png") == 90, (
        f"v2 should carry score 90; got {by_url}"
    )


def test_special_non_selected_version_has_version_id(client, book_env):
    v2 = book_env["v2"]
    resp = client.get(f"/api/book/{BOOK}/special/{PAGE_TYPE}/history?chapter={CHAPTER}")
    assert resp.status_code == 200
    images = resp.json()["images"]
    non_current = [img for img in images if img["version"] != "current"]
    assert len(non_current) == 1
    assert non_current[0]["version"] == v2


# ---------------------------------------------------------------------------
# (b) restore_special_page_version switches the selected version
# ---------------------------------------------------------------------------

def test_special_restore_switches_selected_version(client, book_env, monkeypatch):
    """restore_special_page_version(page_type, v2) must make v2 the selected version."""
    v2 = book_env["v2"]

    # _promote_selected calls storage.get_image; stub it out.
    monkeypatch.setattr("src.core.storage.get_image", lambda key: None)

    resp = client.post(
        f"/api/book/{BOOK}/special/{PAGE_TYPE}/restore-version?version={v2}&chapter={CHAPTER}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "restored"

    # The store must now point at v2.
    sel = store.get_selected_version(BOOK, "special", ASSET_KEY)
    assert sel is not None
    assert sel["id"] == v2, f"Expected selected={v2}, got {sel['id']}"


def test_special_restore_unknown_version_returns_404(client, book_env, monkeypatch):
    """Passing a version id that doesn't exist must return 404."""
    monkeypatch.setattr("src.core.storage.get_image", lambda key: None)
    resp = client.post(
        f"/api/book/{BOOK}/special/{PAGE_TYPE}/restore-version?version=nonexistent_id&chapter={CHAPTER}"
    )
    assert resp.status_code == 404
