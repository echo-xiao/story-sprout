"""Page-number invariants.

There are (at least) three page-number derivations in the codebase:
  1. helpers.segment_page_num — 1-based position within the chapter's segments
     sorted by id (the canonical one; regen/quality endpoints use it).
  2. analyzer.build_scenes — enumerate index + 1 over the segment list.
  3. routes/editor.py get_chapter_segments — ``seg.id - min(ids) + 1``.

They only agree while chapter segment ids are contiguous and id-ordered.
These tests pin the canonical semantics and document where #3 diverges
(review finding P1-7).
"""

from __future__ import annotations

import pytest

from src.routes.helpers import segment_page_num
from tests.conftest import make_segment


# ---------------------------------------------------------------------------
# Canonical semantics (must stay green)
# ---------------------------------------------------------------------------

def test_contiguous_ids_map_to_position():
    segs = [make_segment(i) for i in range(4)]
    assert [segment_page_num(segs, 0, i) for i in range(4)] == [1, 2, 3, 4]


def test_gap_in_ids_still_counts_position_not_id():
    # id 1 was deleted: pages must stay dense (1, 2, 3), not follow raw ids.
    segs = [make_segment(0), make_segment(2), make_segment(3)]
    assert segment_page_num(segs, 0, 0) == 1
    assert segment_page_num(segs, 0, 2) == 2
    assert segment_page_num(segs, 0, 3) == 3


def test_unsorted_input_is_sorted_by_id():
    segs = [make_segment(5), make_segment(3), make_segment(4)]
    assert segment_page_num(segs, 0, 3) == 1
    assert segment_page_num(segs, 0, 5) == 3


def test_other_chapters_are_ignored():
    segs = [make_segment(0, ch_idx=0), make_segment(1, ch_idx=1), make_segment(2, ch_idx=1)]
    assert segment_page_num(segs, 1, 1) == 1
    assert segment_page_num(segs, 1, 2) == 2


def test_unknown_segment_falls_back_to_1():
    assert segment_page_num([make_segment(0)], 0, 99) == 1


def test_build_scenes_page_numbers_match_segment_page_num():
    """build_scenes (pipeline) and segment_page_num (routes) must agree on
    which page a segment becomes, including when short segments are skipped
    (the skipped slot keeps its page number as a hole)."""
    from src.agents.analyzer import AnalyzerAgent

    segs = [
        make_segment(0),
        make_segment(1, words=3),  # <10 words -> skipped by build_scenes
        make_segment(2),
        make_segment(3),
    ]
    analyzer = AnalyzerAgent("test_book")
    scenes = analyzer.build_scenes(segs, characters=[])
    by_seg = {s["source_segment_id"]: s["page_number"] for s in scenes}

    assert by_seg == {0: 1, 2: 3, 3: 4}
    for seg_id, page in by_seg.items():
        assert segment_page_num(segs, 0, seg_id) == page


# ---------------------------------------------------------------------------
# BUG documentation (xfail strict — flip to plain test when fixed)
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    strict=True,
    reason="BUG P1-7 (CODE_REVIEW_2026-06-11.md): editor.py:556 computes the "
    "illustration page as `id - min(ids) + 1`, which diverges from "
    "segment_page_num as soon as chapter ids have a gap.",
)
def test_editor_segments_route_uses_canonical_page_numbers(client, monkeypatch, tmp_path):
    """With ids [0, 2, 3] (id 1 deleted), segment 2 is page 2 and its
    illustration is page_002.png. The current formula looks for page_003."""
    analysis = {"segments": [make_segment(0), make_segment(2), make_segment(3)]}

    def fake_load(book_id, filename):
        return analysis if filename == "analysis.json" else {}

    monkeypatch.setattr("src.routes.editor._load_json", fake_load)
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)
    pages_dir = tmp_path / "somebook" / "chapters" / "ch00" / "pages"
    pages_dir.mkdir(parents=True)
    (pages_dir / "page_002.png").write_bytes(b"png")

    resp = client.get("/api/book/somebook/preprocess/chapter/0/segments")
    assert resp.status_code == 200
    seg2 = next(s for s in resp.json()["segments"] if s["id"] == 2)
    assert seg2.get("illustration_url", "").endswith("page_002.png")


def test_editor_segments_route_contiguous_ids(client, monkeypatch, tmp_path):
    """Green today: with contiguous ids both formulas agree — this guards the
    route while its page formula is being replaced."""
    analysis = {"segments": [make_segment(0), make_segment(1)]}

    def fake_load(book_id, filename):
        return analysis if filename == "analysis.json" else {}

    monkeypatch.setattr("src.routes.editor._load_json", fake_load)
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)
    pages_dir = tmp_path / "somebook" / "chapters" / "ch00" / "pages"
    pages_dir.mkdir(parents=True)
    (pages_dir / "page_001.png").write_bytes(b"png")
    (pages_dir / "page_002.png").write_bytes(b"png")

    resp = client.get("/api/book/somebook/preprocess/chapter/0/segments")
    assert resp.status_code == 200
    segs = {s["id"]: s for s in resp.json()["segments"]}
    assert segs[0]["illustration_url"].endswith("page_001.png")
    assert segs[1]["illustration_url"].endswith("page_002.png")
