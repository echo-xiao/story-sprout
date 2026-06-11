"""get_chapter_characters (agents/analyzer.py) — feeds character-sheet
generation in the chapter pipeline (adk_pipeline.ArtistSetupStage).

Review finding P0-2: this collector ignores character roles, so one-off
"minor" names (e.g. Gatsby's chapter-4 guest list — 105 of the book's 116
extracted characters) each get a portrait + sheet generated. The role filter
exists on the preprocess path (preprocessing/pipeline.py:588) but not here.

NOTE: if the fix lands in ArtistAgent.generate_character_sheets instead of
here, move the xfail test accordingly.
"""

from __future__ import annotations

import pytest

from src.agents.analyzer import AnalyzerAgent
from tests.conftest import make_segment


def profiles():
    return [
        {"name": "Nick Carraway", "role": "main"},
        {"name": "Jay Gatsby", "role": "main"},
        {"name": "Owl Eyes", "role": "minor"},
        {"name": "The Chester Beckers", "role": "minor"},
    ]


def data():
    return {
        "analysis": {
            "characters": [{"name": p["name"]} for p in profiles()],
            "character_profiles": profiles(),
        }
    }


def test_collects_characters_from_scene_annotations():
    segs = [
        make_segment(0, characters_in_scene=["Nick Carraway"]),
        make_segment(1, characters_in_scene=["Jay Gatsby"]),
    ]
    names, chapter_profiles = AnalyzerAgent("b").get_chapter_characters(data(), segs)
    assert names == {"Nick Carraway", "Jay Gatsby"}
    assert {p["name"] for p in chapter_profiles} == {"Nick Carraway", "Jay Gatsby"}


def test_short_segments_are_ignored():
    segs = [make_segment(0, words=3, characters_in_scene=["Jay Gatsby"])]
    names, _ = AnalyzerAgent("b").get_chapter_characters(data(), segs)
    assert names == set()


def test_caps_at_five_characters_per_segment():
    many = [f"Guest {i}" for i in range(8)]
    d = data()
    d["analysis"]["character_profiles"] = [{"name": n, "role": "minor"} for n in many]
    segs = [make_segment(0, characters_in_scene=many)]
    names, _ = AnalyzerAgent("b").get_chapter_characters(d, segs)
    assert len(names) == 5


def test_falls_back_to_text_matching_without_annotations():
    seg = make_segment(0)
    seg["text"] = "That evening Nick Carraway walked along the shore thinking about the green light."
    seg.pop("characters_in_scene")
    seg["characters_in_scene"] = None
    names, _ = AnalyzerAgent("b").get_chapter_characters(data(), [seg])
    assert names == {"Nick Carraway"}


@pytest.mark.xfail(
    strict=True,
    reason="BUG P0-2 (CODE_REVIEW_2026-06-11.md): minor-role characters are "
    "returned for sheet generation; each costs ~2 image calls. Only main + "
    "supporting should get reference sheets (parity with "
    "preprocessing/pipeline.py:588).",
)
def test_minor_characters_excluded_from_sheet_profiles():
    segs = [
        make_segment(0, characters_in_scene=["Nick Carraway", "Owl Eyes"]),
        make_segment(1, characters_in_scene=["The Chester Beckers"]),
    ]
    _, chapter_profiles = AnalyzerAgent("b").get_chapter_characters(data(), segs)
    roles = {p["name"]: p.get("role") for p in chapter_profiles}
    assert "Nick Carraway" in roles
    assert all(r in ("main", "supporting") for r in roles.values()), roles
