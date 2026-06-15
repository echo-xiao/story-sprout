"""Image URLs carry a ?v=<mtime> cache-buster — the framework fix for the
"Gen chapter has no visible effect" bug.

A redraw overwrites the page image in place at a STABLE path (page_001.png).
With a versionless URL the browser kept serving the cached bytes, so the editor
showed the old image even though the file changed. Tying the version to the
file's mtime makes the URL change whenever the file does, so every consumer
re-fetches automatically — no per-component client counters.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.routes.helpers import versioned_static_url
from tests.conftest import make_segment


def test_versioned_static_url_uses_mtime(tmp_path):
    f = tmp_path / "page_001.png"
    f.write_bytes(b"img")
    os.utime(f, (1_000_000, 1_700_000_000))
    assert versioned_static_url("book/page_001.png", f) == "/static/book/page_001.png?v=1700000000"


def test_versioned_static_url_missing_file_has_no_version(tmp_path):
    # No crash, and no stale ?v when the file isn't there.
    assert versioned_static_url("book/gone.png", tmp_path / "gone.png") == "/static/book/gone.png"


@pytest.fixture()
def book(monkeypatch, tmp_path):
    """A one-segment chapter with a page image on disk."""
    analysis = {"segments": [make_segment(0)]}

    def load(book_id, filename):
        return analysis if filename == "analysis.json" else {}

    monkeypatch.setattr("src.routes.editor._load_json", load)
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.routes.helpers.GENERATED_DIR", tmp_path)

    img = tmp_path / "somebook" / "chapters" / "ch00" / "pages" / "page_001.png"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"img")
    return img


def _get_url(client) -> str:
    resp = client.get("/api/book/somebook/preprocess/chapter/0/segments")
    assert resp.status_code == 200
    return resp.json()["segments"][0]["illustration_url"]


def test_chapter_segments_url_is_versioned(client, book):
    url = _get_url(client)
    assert url.startswith("/static/somebook/chapters/ch00/pages/page_001.png?v=")
    assert url.split("?v=")[1] == str(int(book.stat().st_mtime))


def test_url_version_changes_when_image_is_redrawn(client, book):
    before = _get_url(client)
    # Simulate a redraw: overwrite the same path with a newer mtime.
    book.write_bytes(b"new image bytes")
    os.utime(book, (2_000_000, 2_000_000))
    after = _get_url(client)
    assert before != after, "URL must change after the image is overwritten"
    assert after.endswith("?v=2000000")


def test_special_cover_url_is_versioned(monkeypatch, tmp_path):
    """Special pages (covers) are overwritten in place too — same fix as pages."""
    from src.routes import editor

    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)
    special = tmp_path / "somebook" / "special"
    special.mkdir(parents=True)
    cover = special / "book_cover.png"
    cover.write_bytes(b"cover")
    os.utime(cover, (3_000_000, 3_000_000))

    url = editor._special_image_url("somebook", "book_cover")
    assert url == "/static/somebook/special/book_cover.png?v=3000000"

    # A re-gen overwrites in place → the version (and so the URL) changes.
    cover.write_bytes(b"new cover")
    os.utime(cover, (4_000_000, 4_000_000))
    assert editor._special_image_url("somebook", "book_cover").endswith("?v=4000000")
