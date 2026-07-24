"""chapter_data.json stays in step after single-page changes (H5).

High-risk review finding: chapter_data.json (the combined PDF's source)
stored the absolute image path + text from generation time. A single-page
regen that switched extensions (.png → .jpg) left a dead path — a silently
blank page in the next book.pdf — and edited/restored text never reached it.
"""

from __future__ import annotations

import json

import pytest

import src.core.store as _store
from src.routes.helpers import update_chapter_data_page
from tests.conftest import make_segment


@pytest.fixture()
def chapter_data(monkeypatch, tmp_path):
    monkeypatch.setattr("src.routes.helpers.GENERATED_DIR", tmp_path)
    ch = tmp_path / "somebook" / "chapters" / "ch00"
    ch.mkdir(parents=True)
    path = ch / "chapter_data.json"
    data = {
        "chapter_idx": 0, "chapter_title": "Ch 1",
        "pages": [
            {"text": "old text", "image_path": "/app/data/ch00/pages/page_001.png", "page_number": 1},
            {"text": "page two", "image_path": "/app/data/ch00/pages/page_002.png", "page_number": 2},
        ],
    }
    path.write_text(json.dumps(data))
    # Seed the store (authoritative RMW base).
    _store.put_json("somebook/chapters/ch00/chapter_data.json", data)
    return path


def _pages(path):
    return json.loads(path.read_text())["pages"]


def test_updates_image_path_and_text(chapter_data):
    update_chapter_data_page("somebook", 0, 1,
                             image_path="/app/data/ch00/pages/page_001.jpg",
                             text="new text")
    pages = _pages(chapter_data)
    assert pages[0]["image_path"].endswith("page_001.jpg")
    assert pages[0]["text"] == "new text"
    assert pages[1]["text"] == "page two", "other pages untouched"


def test_text_only_update_keeps_image(chapter_data):
    update_chapter_data_page("somebook", 0, 2, text="edited")
    pages = _pages(chapter_data)
    assert pages[1]["text"] == "edited"
    assert pages[1]["image_path"].endswith("page_002.png")


def test_legacy_entry_matched_via_filename(chapter_data):
    data = json.loads(chapter_data.read_text())
    for p in data["pages"]:
        del p["page_number"]  # pre-page_number schema
    chapter_data.write_text(json.dumps(data))
    # Also update the store (authoritative) with the legacy-schema data.
    _store.put_json("somebook/chapters/ch00/chapter_data.json", data)

    update_chapter_data_page("somebook", 0, 2, text="legacy edited")
    assert _pages(chapter_data)[1]["text"] == "legacy edited"


def test_noop_when_chapter_never_generated(monkeypatch, tmp_path):
    monkeypatch.setattr("src.routes.helpers.GENERATED_DIR", tmp_path)
    update_chapter_data_page("somebook", 0, 1, text="x")  # must not raise


def test_restore_version_updates_chapter_data(client, monkeypatch, tmp_path):
    """End-to-end: restoring a .jpg version must repoint chapter_data.json at the
    restored file. Uses the version store (Task 1 new contract)."""
    import src.core.store as store

    analysis = {"segments": [make_segment(0)]}
    monkeypatch.setattr(
        "src.routes.editor._load_json",
        lambda book_id, filename: analysis if filename == "analysis.json" else {},
    )
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.routes.helpers.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)

    book_id = "somebook"
    ch = tmp_path / book_id / "chapters" / "ch00"
    pages_dir = ch / "pages"
    pages_dir.mkdir(parents=True)
    (pages_dir / "page_001.png").write_bytes(b"CURRENT")
    chapter_data = {
        "pages": [{"text": "t", "image_path": str(pages_dir / "page_001.png"), "page_number": 1}],
    }
    (ch / "chapter_data.json").write_text(json.dumps(chapter_data))
    # Seed the store (authoritative RMW base).
    store.put_json(f"{book_id}/chapters/ch00/chapter_data.json", chapter_data)

    # Seed two versions: v1=.png (current), v2=.jpg (to be restored).
    asset_key = "ch00:p001"
    # Write the v2 bytes into tmp_path so _promote_selected can write the live file.
    storage_key_v2 = f"{book_id}/chapters/ch00/pages/page_001_v2.jpg"
    (tmp_path / storage_key_v2).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / storage_key_v2).write_bytes(b"OLD-JPG")
    v1 = store.add_asset_version(book_id, "page", asset_key, "u1", image_hash="h1", storage_key="s1.png")
    v2 = store.add_asset_version(book_id, "page", asset_key, "u2", image_hash="h2", storage_key=storage_key_v2)
    store.set_selected_version(book_id, "page", asset_key, v1)

    # get_image reads from local storage (GCS_BUCKET is "" → local file fallback).
    # put_image is a no-op.
    monkeypatch.setattr("src.core.storage.put_image", lambda *a, **kw: None)

    resp = client.post(f"/api/book/{book_id}/segment/0/restore-version?version={v2}")
    assert resp.status_code == 200
    pages = json.loads((ch / "chapter_data.json").read_text())["pages"]
    assert pages[0]["image_path"].endswith("page_001.jpg")
