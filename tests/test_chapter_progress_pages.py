"""Regression: live chapter progress trusts the generator's own per-page count,
not the on-disk file count.

During a force-regen the old page files sit on disk until each is overwritten in
place, so counting files would report "all done" instantly and every progress dot
would turn green before anything was redrawn. The in-flight branch must instead
report the count the artist writes to progress.json as each page finishes.

No network: GENERATED_DIR is redirected to a tmp dir and Mongo is forced off.
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

    ch = tmp_path / "b1" / "chapters" / "ch00"
    pages = ch / "pages"
    pages.mkdir(parents=True)
    # 3 STALE page files from the previous run still sit on disk (force-regen
    # overwrites in place).
    (pages / "page_001.png").write_bytes(b"x")
    (pages / "page_002.jpg").write_bytes(b"x")
    (pages / "page_004.png").write_bytes(b"x")

    # The artist has so far redrawn 2 pages this run (written to progress.json).
    (ch / "progress.json").write_text(json.dumps(
        {"status": "generating", "progress": 45, "current_step": "Illustrating page 3/5...",
         "completed_pages": 2}
    ))
    return tmp_path


def _client():
    return TestClient(__import__("src.app", fromlist=["app"]).app,
                      raise_server_exceptions=False)


def test_inflight_progress_uses_artist_count_not_file_count(book):
    data = _client().get("/api/book/b1/chapter/0/progress").json()
    assert data["status"] == "generating"
    assert data["total_pages"] == 5
    # The artist's reported count (2) — NOT the 3 stale files on disk.
    assert data["completed_pages"] == 2


def test_complete_progress_counts_real_files(book, monkeypatch):
    # When the run is done (no in-flight progress.json), the real file count is
    # authoritative again.
    ch = book / "b1" / "chapters" / "ch00"
    (ch / "progress.json").unlink()
    data = _client().get("/api/book/b1/chapter/0/progress").json()
    assert data["completed_pages"] == 3  # the 3 page files present
