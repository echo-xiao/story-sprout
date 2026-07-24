"""restore-version endpoint tests (new version-store contract).

Task 1 replaced the history/-file rename dance with a pure version-store
pointer swap: restore_segment_version now calls set_selected_version and then
_promote_selected to copy bytes onto the live page image.

Previous tests exercised the OLD file-shuffle behavior (copy-to-tmp, rename
current to history, rename tmp to current). That behavior is gone — these
tests now assert the NEW contract:

  - set_selected_version is called with the given version id
  - _promote_selected copies the selected version's bytes to the live path
  - 404 is returned for unknown version ids
  - 409 is returned when a regen is active
"""

from __future__ import annotations

import pytest
import src.core.store as store
from tests.conftest import make_segment


BOOK = "rvtest"
SEG_ID = 0
CH_IDX = 0
PAGE_NUM = 1
ASSET_KEY = f"ch{CH_IDX:02d}:p{PAGE_NUM:03d}"


@pytest.fixture()
def book_with_versions(monkeypatch, tmp_path):
    """One-segment book with two page versions in the store."""
    analysis = {"segments": [make_segment(SEG_ID)]}
    monkeypatch.setattr(
        "src.routes.editor._load_json",
        lambda book_id, fn: analysis if fn == "analysis.json" else {},
    )
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.routes.helpers.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)

    # Write a live current image so _promote_selected has a place to write to.
    pages_dir = tmp_path / BOOK / "chapters" / "ch00" / "pages"
    pages_dir.mkdir(parents=True)
    (pages_dir / "page_001.png").write_bytes(b"CURRENT")

    # Seed two versions in the store; v1 is selected (current).
    v1 = store.add_asset_version(
        BOOK, "page", ASSET_KEY, "http://example.com/v1.png",
        image_hash="hash1", storage_key="k1.png",
    )
    v2 = store.add_asset_version(
        BOOK, "page", ASSET_KEY, "http://example.com/v2.png",
        image_hash="hash2", storage_key="k2.png",
    )
    store.set_selected_version(BOOK, "page", ASSET_KEY, v1)
    return {"v1": v1, "v2": v2}


def test_restore_switches_selected_version(client, book_with_versions, monkeypatch):
    """After restore, the store's selected_version_id points at the given version."""
    v2 = book_with_versions["v2"]
    # _promote_selected calls storage.get_image; stub it to return no bytes
    # (the live-file write path becomes a no-op when bytes are None).
    monkeypatch.setattr("src.core.storage.get_image", lambda key: None)

    resp = client.post(f"/api/book/{BOOK}/segment/{SEG_ID}/restore-version?version={v2}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "restored"

    sel = store.get_selected_version(BOOK, "page", ASSET_KEY)
    assert sel is not None
    assert sel["id"] == v2, f"Expected selected={v2}, got {sel['id']}"


def test_restore_unknown_version_returns_404(client, book_with_versions, monkeypatch):
    """A version id that doesn't exist in the store must return 404."""
    monkeypatch.setattr("src.core.storage.get_image", lambda key: None)
    resp = client.post(
        f"/api/book/{BOOK}/segment/{SEG_ID}/restore-version?version=deadbeef0000"
    )
    assert resp.status_code == 404


def test_restore_409_when_regen_active(client, book_with_versions, monkeypatch):
    """Returns 409 while a regen is mid-flight for this segment."""
    from src.routes.helpers import _active_regens
    _active_regens.add((BOOK, "segment", SEG_ID))
    try:
        resp = client.post(
            f"/api/book/{BOOK}/segment/{SEG_ID}/restore-version?version={book_with_versions['v2']}"
        )
        assert resp.status_code == 409
    finally:
        _active_regens.discard((BOOK, "segment", SEG_ID))


def test_restore_promotes_bytes_to_live_path(client, book_with_versions, monkeypatch, tmp_path):
    """_promote_selected is invoked: when storage has bytes, the live file is updated."""
    v2 = book_with_versions["v2"]
    # Make get_image return real bytes for k2.png.
    monkeypatch.setattr("src.core.storage.get_image", lambda key: b"\x89PNG\r\n\x1a\nV2" if key == "k2.png" else None)
    # put_image is a no-op (no real GCS).
    monkeypatch.setattr("src.core.storage.put_image", lambda *a, **kw: None)

    resp = client.post(f"/api/book/{BOOK}/segment/{SEG_ID}/restore-version?version={v2}")
    assert resp.status_code == 200

    pages_dir = tmp_path / BOOK / "chapters" / "ch00" / "pages"
    live = pages_dir / "page_001.png"
    assert live.exists()
    assert live.read_bytes() == b"\x89PNG\r\n\x1a\nV2"
