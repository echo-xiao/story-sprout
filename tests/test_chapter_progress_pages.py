"""Regression: chapter progress reports WHICH pages are done, not just a count.

This backs the per-page live progress dots (each page turns green the moment its
image file appears during generation). No network: GENERATED_DIR is redirected to
a tmp dir and Mongo is forced unavailable.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def book(monkeypatch, tmp_path):
    monkeypatch.setattr("src.routes.generation.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.routes.helpers.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.db.is_available", lambda: False, raising=False)

    pre = tmp_path / "b1" / "preprocess"
    pre.mkdir(parents=True)
    segs = [{"id": i, "chapter_idx": 0, "text": " ".join(["word"] * 20)} for i in range(5)]
    (pre / "analysis.json").write_text(json.dumps({"segments": segs}))

    pages = tmp_path / "b1" / "chapters" / "ch00" / "pages"
    pages.mkdir(parents=True)
    # 3 of 5 pages drawn so far (mixed extensions), out of order.
    (pages / "page_001.png").write_bytes(b"x")
    (pages / "page_002.jpg").write_bytes(b"x")
    (pages / "page_004.png").write_bytes(b"x")

    # A live run is in flight.
    (tmp_path / "b1" / "chapters" / "ch00" / "progress.json").write_text(
        json.dumps({"status": "generating", "progress": 50, "current_step": "Illustrating..."})
    )
    return tmp_path


def test_progress_lists_completed_page_numbers(book):
    client = TestClient(__import__("src.app", fromlist=["app"]).app,
                        raise_server_exceptions=False)
    data = client.get("/api/book/b1/chapter/0/progress").json()
    assert data["status"] == "generating"
    assert data["total_pages"] == 5
    assert data["completed_pages"] == 3
    # The exact page numbers present — so the UI can turn just those green.
    assert data["completed_page_numbers"] == [1, 2, 4]
