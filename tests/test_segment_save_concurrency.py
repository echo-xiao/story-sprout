"""Segment edits must not lose updates under concurrency.

"pages 页面 edit 了之后无法保存": analysis.json is one big shared GCS blob edited
by segment saves AND regen's text-simplification. update_segment did a plain
load+save with only an in-process lock — a cross-request/cross-instance race
(the user editing while a regen runs, or two edits at once) clobbered an edit,
so a save "didn't stick". Fix: route the edit through store.mutate_preprocess_file
(GCS optimistic concurrency, if_generation_match + retry).
"""

from __future__ import annotations

import src.core.store as store


def _seed(book: str):
    store.save_preprocess_file(book, "analysis.json", {"segments": [
        {"id": 1, "chapter_idx": 0, "scene_summary": "a", "text": "t1"},
        {"id": 2, "chapter_idx": 0, "scene_summary": "b", "text": "t2"},
    ]})


def test_segment_edit_persists(client):
    book = "segpersist"
    _seed(book)
    r = client.put(f"/api/book/{book}/segment/1", json={"scene_summary": "EDITED"})
    assert r.status_code == 200, r.text
    an = store.load_preprocess_file(book, "analysis.json")
    seg = next(s for s in an["segments"] if s["id"] == 1)
    assert seg["scene_summary"] == "EDITED"


def test_concurrent_segment_edits_dont_lose_updates():
    """Two edits to DIFFERENT segments of the same analysis.json, one committing
    mid-flight of the other, must both survive (OCC retry), not clobber."""
    book = "segrace"
    _seed(book)
    saw: list[int] = []

    def edit_seg1(analysis: dict):
        saw.append(1)
        if len(saw) == 1:
            # A concurrent edit to seg 2 commits first -> seg1's write hits a
            # stale generation and must retry.
            store.mutate_preprocess_file(
                book, "analysis.json",
                lambda a: next(s for s in a["segments"] if s["id"] == 2).__setitem__("scene_summary", "B2"),
            )
        next(s for s in analysis["segments"] if s["id"] == 1)["scene_summary"] = "A1"

    store.mutate_preprocess_file(book, "analysis.json", edit_seg1)

    an = store.load_preprocess_file(book, "analysis.json")
    by_id = {s["id"]: s for s in an["segments"]}
    assert by_id[1]["scene_summary"] == "A1"
    assert by_id[2]["scene_summary"] == "B2", "the concurrent edit to seg 2 was clobbered"
    assert len(saw) == 2, "seg-1 edit must re-read + retry after the interleaved write"


def test_segment_edit_404_when_missing(client):
    book = "segmissing"
    _seed(book)
    r = client.put(f"/api/book/{book}/segment/999", json={"scene_summary": "x"})
    assert r.status_code == 404, r.text
