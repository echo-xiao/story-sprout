"""Post-Firestore-cutover chapter_data store-authority tests.

I1 — PDF chapter enumeration reads from the JSON store (_list_keys + get_json),
     not GCS image blobs, so it works on the Firestore backend where
     chapter_data.json is never a GCS object.

I2 — update_chapter_data_page is atomic via store._mutate_json (authoritative
     read-modify-write), so a cold serverless instance with no local /tmp files
     cannot bootstrap from empty and overwrite pages that only live in the store.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import src.core.store as _store
import src.core.storage as _storage
import src.routes.books as _books
import src.routes.helpers as _helpers


# ---------------------------------------------------------------------------
# I1: PDF chapter enumeration runs on the FIRESTORE backend (store-authoritative)
# ---------------------------------------------------------------------------

class TestPdfStoreEnumeration:
    """download_book_pdf must enumerate chapter_data via store._list_keys
    (not storage.list_keys) so it works on both GCS and Firestore backends.
    Seeds via store.put_json which is backend-agnostic.
    """

    @pytest.fixture()
    def cold_instance(self, monkeypatch, tmp_path):
        """Cold instance: no local chapter files.  chapter_data seeded in the
        authoritative store (Firestore fake or GCS fake, per STORE_BACKEND).
        storage.list_keys (image layer) is stubbed to return [] — no special
        images; only the store-layer chapter_data enumeration is under test."""
        monkeypatch.setattr(_storage, "GENERATED_DIR", tmp_path)
        monkeypatch.setattr(_books, "GENERATED_DIR", tmp_path)
        monkeypatch.setattr(_books, "_load_json", lambda bid, fn: {"title": "Cold Book"})
        monkeypatch.setattr(_storage, "localize", lambda key: None)
        # Stub the image-layer list_keys (special images) to avoid GCS calls.
        monkeypatch.setattr(_storage, "list_keys", lambda prefix: [])
        return tmp_path

    def test_enumeration_finds_chapter_data_from_store(self, cold_instance, monkeypatch, client):
        """No local files; chapter_data seeded via store.put_json → PDF built (200)."""
        tmp_path = cold_instance
        _store.put_json("storeBook/chapters/ch00/chapter_data.json", {
            "chapter_idx": 0,
            "pages": [{"page_number": 1, "image_path": "", "text": "store page 1"}],
        })

        captured = {}

        def _fake_export(pages, title, out_path, special_dir=""):
            captured["pages"] = pages
            Path(out_path).write_bytes(b"%PDF-1.4 fake")

        monkeypatch.setattr("src.renderer.pdf_export.export_pdf", _fake_export)

        resp = client.get("/api/book/storeBook/pdf")
        assert resp.status_code == 200, resp.text
        assert captured.get("pages"), "export_pdf must be called with pages from the store"
        assert captured["pages"][0]["text"] == "store page 1"

    def test_enumeration_multiple_chapters_sorted(self, cold_instance, monkeypatch, client):
        """Multiple chapters seeded in the store → assembled in chapter_idx order."""
        tmp_path = cold_instance
        # Seed out-of-order in the store.
        _store.put_json("multiBook/chapters/ch01/chapter_data.json", {
            "chapter_idx": 1,
            "pages": [{"page_number": 1, "image_path": "", "text": "ch1 p1"}],
        })
        _store.put_json("multiBook/chapters/ch00/chapter_data.json", {
            "chapter_idx": 0,
            "pages": [{"page_number": 1, "image_path": "", "text": "ch0 p1"}],
        })

        captured = {}

        def _fake_export(pages, title, out_path, special_dir=""):
            captured["pages"] = pages
            Path(out_path).write_bytes(b"%PDF-1.4 fake")

        monkeypatch.setattr("src.renderer.pdf_export.export_pdf", _fake_export)

        resp = client.get("/api/book/multiBook/pdf")
        assert resp.status_code == 200
        assert [p["_chapter_num"] for p in captured["pages"]] == [1, 2]

    def test_enumeration_empty_store_and_no_local_gives_404(self, cold_instance, client):
        """No store entries AND no local files → 404."""
        resp = client.get("/api/book/ghostBook/pdf")
        assert resp.status_code == 404

    def test_enumeration_local_fallback_when_store_empty(self, monkeypatch, tmp_path, client):
        """Store is empty (e.g. dev mode) → falls back to local glob."""
        monkeypatch.setattr(_storage, "GCS_BUCKET", "", raising=False)
        monkeypatch.setattr(_storage, "GENERATED_DIR", tmp_path)
        monkeypatch.setattr(_books, "GENERATED_DIR", tmp_path)
        monkeypatch.setattr(_books, "_load_json", lambda bid, fn: {"title": "Local Book"})
        monkeypatch.setattr(_storage, "localize", lambda key: None)

        # Only local chapter_data — nothing in the store.
        ch_dir = tmp_path / "localFallBook" / "chapters" / "ch00"
        ch_dir.mkdir(parents=True)
        (ch_dir / "chapter_data.json").write_text(json.dumps({
            "chapter_idx": 0,
            "pages": [{"page_number": 1, "image_path": "", "text": "local page"}],
        }))

        captured = {}

        def _fake_export(pages, title, out_path, special_dir=""):
            captured["pages"] = pages
            Path(out_path).write_bytes(b"%PDF-1.4 fake")

        monkeypatch.setattr("src.renderer.pdf_export.export_pdf", _fake_export)

        resp = client.get("/api/book/localFallBook/pdf")
        assert resp.status_code == 200
        assert captured["pages"][0]["text"] == "local page"


# ---------------------------------------------------------------------------
# I2: update_chapter_data_page data-loss prevention (cold serverless instance)
# ---------------------------------------------------------------------------

class TestUpdateChapterDataPageStorageAuthority:
    """update_chapter_data_page must read from the store (not local /tmp) so a
    cold serverless instance with an empty /tmp cannot clobber pages that exist
    only in the store.
    """

    @pytest.fixture()
    def cold_dir(self, monkeypatch, tmp_path):
        """A cold-instance tmp_path with NO local chapter files."""
        monkeypatch.setattr(_helpers, "GENERATED_DIR", tmp_path)
        return tmp_path

    def test_cold_instance_preserves_all_store_pages(self, cold_dir):
        """Store has pages [1,2,3]; local /tmp is EMPTY.
        Edit page 2 → store doc still has all 3 pages with page 2 updated.
        This is the exact data-loss scenario from the original bug.
        """
        book_id = "coldBook"
        store_key = f"{book_id}/chapters/ch00/chapter_data.json"
        original = {
            "chapter_idx": 0,
            "pages": [
                {"page_number": 1, "image_path": "/x/p1.png", "text": "page 1"},
                {"page_number": 2, "image_path": "/x/p2.png", "text": "page 2"},
                {"page_number": 3, "image_path": "/x/p3.png", "text": "page 3"},
            ],
        }
        _store.put_json(store_key, original)

        # No local file exists — simulates a cold serverless instance.
        local_path = cold_dir / book_id / "chapters" / "ch00" / "chapter_data.json"
        assert not local_path.exists(), "test setup: local file must NOT exist"

        # Edit page 2 only.
        _helpers.update_chapter_data_page(book_id, 0, 2, text="page 2 UPDATED")

        stored = _store.get_json(store_key)
        assert stored is not None
        assert len(stored["pages"]) == 3, (
            "all 3 pages must be preserved — pre-fix bootstrap-from-empty would "
            "have truncated to just page 2"
        )
        page_nums = [p["page_number"] for p in stored["pages"]]
        assert page_nums == [1, 2, 3]
        texts = {p["page_number"]: p["text"] for p in stored["pages"]}
        assert texts[1] == "page 1", "page 1 untouched"
        assert texts[2] == "page 2 UPDATED", "page 2 updated"
        assert texts[3] == "page 3", "page 3 untouched"

    def test_cold_instance_local_mirror_written_after_store(self, cold_dir):
        """After a successful store mutate, the local mirror file is written
        so the same-invocation PDF/generator fast path can find it."""
        book_id = "coldMirrorBook"
        store_key = f"{book_id}/chapters/ch00/chapter_data.json"
        _store.put_json(store_key, {
            "chapter_idx": 0,
            "pages": [{"page_number": 1, "image_path": "", "text": "original"}],
        })

        local_path = cold_dir / book_id / "chapters" / "ch00" / "chapter_data.json"
        assert not local_path.exists()

        _helpers.update_chapter_data_page(book_id, 0, 1, text="updated")

        assert local_path.exists(), "local mirror must be written after store mutate"
        local_data = json.loads(local_path.read_text())
        assert local_data["pages"][0]["text"] == "updated"

    def test_store_is_consistent_with_local_mirror(self, cold_dir):
        """Store doc and local mirror must agree after update_chapter_data_page."""
        book_id = "consistBook"
        store_key = f"{book_id}/chapters/ch00/chapter_data.json"
        data = {
            "chapter_idx": 0,
            "pages": [
                {"page_number": 1, "image_path": "/a.png", "text": "a"},
                {"page_number": 2, "image_path": "/b.png", "text": "b"},
            ],
        }
        _store.put_json(store_key, data)

        _helpers.update_chapter_data_page(book_id, 0, 1, text="a edited", image_path="/a2.jpg")

        stored = _store.get_json(store_key)
        local = json.loads((cold_dir / book_id / "chapters" / "ch00" / "chapter_data.json").read_text())
        assert stored["pages"] == local["pages"], "store and local mirror must agree"
        assert stored["pages"][0]["text"] == "a edited"
        assert stored["pages"][0]["image_path"] == "/a2.jpg"
        assert stored["pages"][1]["text"] == "b"  # unchanged


# ---------------------------------------------------------------------------
# I2: Concurrent updates to the same chapter don't lose each other
# ---------------------------------------------------------------------------

class TestUpdateChapterDataPageConcurrency:
    """Two concurrent update_chapter_data_page calls to DIFFERENT pages of the
    same chapter must both be committed — neither clobbers the other.
    Uses the store's atomic mutate (_mutate_json) which serializes concurrent
    writes via Firestore transactions / GCS optimistic-concurrency retry.
    """

    @pytest.fixture()
    def setup(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_helpers, "GENERATED_DIR", tmp_path)
        return tmp_path

    def test_concurrent_updates_different_pages_both_committed(self, setup):
        """Two threads update pages 1 and 2 concurrently — both changes survive."""
        book_id = "concBook"
        store_key = f"{book_id}/chapters/ch00/chapter_data.json"
        original = {
            "chapter_idx": 0,
            "pages": [
                {"page_number": 1, "image_path": "/p1.png", "text": "p1 original"},
                {"page_number": 2, "image_path": "/p2.png", "text": "p2 original"},
            ],
        }
        _store.put_json(store_key, original)

        errors: list = []

        def _edit_page1():
            try:
                _helpers.update_chapter_data_page(book_id, 0, 1, text="p1 UPDATED")
            except Exception as e:
                errors.append(e)

        def _edit_page2():
            try:
                _helpers.update_chapter_data_page(book_id, 0, 2, text="p2 UPDATED")
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=_edit_page1)
        t2 = threading.Thread(target=_edit_page2)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"concurrent updates raised: {errors}"

        stored = _store.get_json(store_key)
        assert stored is not None
        assert len(stored["pages"]) == 2, "both pages must still exist"
        texts = {p["page_number"]: p["text"] for p in stored["pages"]}
        assert texts[1] == "p1 UPDATED", "page 1 update must be committed"
        assert texts[2] == "p2 UPDATED", "page 2 update must be committed"

    def test_concurrent_updates_same_page_last_writer_wins(self, setup):
        """Two concurrent updates to THE SAME page — one must win (no crash, no
        partial write, and exactly one of the two texts is in the store)."""
        book_id = "concSameBook"
        store_key = f"{book_id}/chapters/ch00/chapter_data.json"
        _store.put_json(store_key, {
            "chapter_idx": 0,
            "pages": [{"page_number": 1, "image_path": "", "text": "original"}],
        })

        errors: list = []

        def _edit(text):
            try:
                _helpers.update_chapter_data_page(book_id, 0, 1, text=text)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=_edit, args=("writer A",))
        t2 = threading.Thread(target=_edit, args=("writer B",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"concurrent same-page updates raised: {errors}"

        stored = _store.get_json(store_key)
        assert stored is not None
        assert len(stored["pages"]) == 1, "page count must remain 1"
        final_text = stored["pages"][0]["text"]
        assert final_text in ("writer A", "writer B"), (
            f"exactly one writer must win; got '{final_text}'"
        )
