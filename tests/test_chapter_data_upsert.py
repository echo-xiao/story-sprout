"""update_chapter_data_page upserts (round-2 finding: pages with no
chapter_data row — short-at-pipeline-time segments, or chapters never
pipeline-generated — were silently absent from every PDF)."""

from __future__ import annotations

import json

import pytest

import src.core.store as _store
from src.routes.helpers import update_chapter_data_page


@pytest.fixture()
def book_dir(monkeypatch, tmp_path):
    monkeypatch.setattr("src.routes.helpers.GENERATED_DIR", tmp_path)
    return tmp_path / "somebook" / "chapters" / "ch00"


def _pages(book_dir):
    return json.loads((book_dir / "chapter_data.json").read_text())["pages"]


def test_bootstraps_missing_chapter_data(book_dir):
    # Chapter never pipeline-generated: neither local file nor store entry exists.
    update_chapter_data_page("somebook", 0, 3,
                             image_path="/x/pages/page_003.png", text="hello")
    pages = _pages(book_dir)
    assert pages == [{"page_number": 3, "image_path": "/x/pages/page_003.png", "text": "hello"}]


def test_inserts_missing_page_sorted(book_dir):
    book_dir.mkdir(parents=True)
    data = {
        "chapter_idx": 0,
        "pages": [
            {"page_number": 1, "image_path": "a.png", "text": "one"},
            {"page_number": 4, "image_path": "d.png", "text": "four"},
        ],
    }
    (book_dir / "chapter_data.json").write_text(json.dumps(data))
    # Seed the store (authoritative) so the RMW base has all existing pages.
    _store.put_json("somebook/chapters/ch00/chapter_data.json", data)
    # Page 3 had no row (segment was <10 words at pipeline time).
    update_chapter_data_page("somebook", 0, 3, image_path="c.png", text="three")
    pages = _pages(book_dir)
    assert [p["page_number"] for p in pages] == [1, 3, 4], "inserted in order"
    assert pages[1] == {"page_number": 3, "image_path": "c.png", "text": "three"}
    assert pages[2]["text"] == "four", "existing rows untouched"


def test_existing_entry_still_updated_in_place(book_dir):
    book_dir.mkdir(parents=True)
    data = {"pages": [{"page_number": 1, "image_path": "a.png", "text": "one"}]}
    (book_dir / "chapter_data.json").write_text(json.dumps(data))
    # Seed the store (authoritative) so the RMW base has the existing page.
    _store.put_json("somebook/chapters/ch00/chapter_data.json", data)
    update_chapter_data_page("somebook", 0, 1, text="edited")
    pages = _pages(book_dir)
    assert len(pages) == 1
    assert pages[0]["text"] == "edited"
    assert pages[0]["image_path"] == "a.png"
