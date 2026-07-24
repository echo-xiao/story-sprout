"""Special-page edits must persist reliably.

Two real bugs behind "修改之后无法保存 / regen 还是原来那个样子":

1. Lost updates — special_pages.json is ONE shared GCS blob edited in place, but
   the save did an UNLOCKED load+save (_save_json). Concurrent writers (multiple
   serverless instances, a stale browser re-saving, Save-then-Regen firing a
   save then a regen that re-reads) clobbered each other; the user's edit
   vanished and regen read stale characters.
2. False success — _save_json swallowed a failed GCS write and the endpoint
   still returned 200, so the UI reported "saved" when nothing persisted.

Fix: the overlay write goes through store.mutate_preprocess_file (GCS optimistic
concurrency, if_generation_match + retry) and a durable-write failure surfaces
as HTTP 500 instead of a fake OK.
"""

from __future__ import annotations

import pytest

import src.core.store as store


def test_mutate_preprocess_file_no_lost_update_on_interleaved_write():
    """A writer preceded by a concurrent write to the SAME preprocess blob must
    retry and preserve BOTH edits, not clobber the other."""
    book = "sprace"
    saw: list[dict] = []

    def writer_a(obj: dict):
        saw.append(dict(obj))
        pages = obj.setdefault("pages", {})
        if len(saw) == 1:
            # A concurrent B commits first, so A's write hits a stale generation.
            store.mutate_preprocess_file(
                book, "special_pages.json",
                lambda o: o.setdefault("pages", {}).__setitem__("back_cover", {"title_text": "B"}),
            )
        pages["book_cover"] = {"title_text": "A"}

    store.mutate_preprocess_file(book, "special_pages.json", writer_a)

    final = store.load_preprocess_file(book, "special_pages.json")
    assert final["pages"].get("book_cover") == {"title_text": "A"}
    assert final["pages"].get("back_cover") == {"title_text": "B"}, "B's edit was clobbered"
    assert len(saw) == 2, "A must re-read + retry after B's interleaved write"


def test_update_special_page_persists_and_is_readable(client):
    """PUT then GET reflects the edit — the whole point of 'save'."""
    book = "spbook"
    # Seed the derived source so load_special_records has a book_cover record.
    store.save_preprocess_file(book, "meta.json", {"title": "T"})
    store.save_preprocess_file(book, "analysis.json", {"segments": [
        {"id": 0, "chapter_idx": 0, "characters_in_scene": ["Hero"], "scene_background": "bg"},
    ]})
    store.save_preprocess_file(book, "chapter_segments.json", {"0": {"chapter_title": "Ch"}})

    r = client.put(f"/api/book/{book}/special/book_cover?chapter=0",
                   json={"title_text": "EDITED TITLE"})
    assert r.status_code == 200, r.text

    g = client.get(f"/api/book/{book}/special-pages")
    assert g.status_code == 200, g.text
    cover = [p for p in g.json()["pages"] if p["type"] == "book_cover"][0]
    assert cover["title_text"] == "EDITED TITLE"


def test_update_special_page_surfaces_durable_write_failure(client, monkeypatch):
    """A failed durable write must return 500, not a fake 200."""
    book = "spfail"
    store.save_preprocess_file(book, "meta.json", {"title": "T"})
    store.save_preprocess_file(book, "analysis.json", {"segments": [
        {"id": 0, "chapter_idx": 0, "characters_in_scene": ["Hero"], "scene_background": "bg"},
    ]})
    store.save_preprocess_file(book, "chapter_segments.json", {"0": {"chapter_title": "Ch"}})

    def _boom(*a, **k):
        raise RuntimeError("GCS down")
    monkeypatch.setattr("src.core.store.mutate_preprocess_file", _boom)

    r = client.put(f"/api/book/{book}/special/book_cover?chapter=0",
                   json={"title_text": "X"})
    assert r.status_code == 500, r.text
    assert "persist" in r.json()["detail"].lower()
