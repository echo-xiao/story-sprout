"""get_chapter_characters (agents/analyzer.py) — feeds character-sheet
generation in the chapter pipeline (adk_pipeline.ArtistSetupStage).

Role filtering (P0-2): one-off "minor" names must NOT get a sheet generated.

Character profiles now come from the consistency hub (load_character_profiles
→ load_characters), the single source shared with the web paths — NOT the
stale analysis.json copy. Tests seed the hub via monkeypatch.
"""

from __future__ import annotations

import pytest

import src.routes.helpers as helpers
from src.agents.analyzer import AnalyzerAgent
from tests.conftest import make_segment


def _profiles():
    # load_character_profiles shape (name/role), as the hub accessor returns.
    return [
        {"name": "Nick Carraway", "role": "main"},
        {"name": "Jay Gatsby", "role": "main"},
        {"name": "Owl Eyes", "role": "minor"},
        {"name": "The Chester Beckers", "role": "minor"},
    ]


@pytest.fixture()
def hub(monkeypatch):
    """Seed the consistency hub; tests may override the return value."""
    box = {"profiles": _profiles()}
    monkeypatch.setattr(helpers, "load_character_profiles", lambda bid: box["profiles"])
    return box


def test_collects_characters_from_scene_annotations(hub):
    segs = [
        make_segment(0, characters_in_scene=["Nick Carraway"]),
        make_segment(1, characters_in_scene=["Jay Gatsby"]),
    ]
    names, chapter_profiles = AnalyzerAgent("b").get_chapter_characters({}, segs)
    assert names == {"Nick Carraway", "Jay Gatsby"}
    assert {p["name"] for p in chapter_profiles} == {"Nick Carraway", "Jay Gatsby"}


def test_short_segments_are_ignored(hub):
    segs = [make_segment(0, words=3, characters_in_scene=["Jay Gatsby"])]
    names, _ = AnalyzerAgent("b").get_chapter_characters({}, segs)
    assert names == set()


def test_caps_at_five_characters_per_segment(hub):
    many = [f"Guest {i}" for i in range(8)]
    hub["profiles"] = [{"name": n, "role": "minor"} for n in many]
    segs = [make_segment(0, characters_in_scene=many)]
    names, _ = AnalyzerAgent("b").get_chapter_characters({}, segs)
    assert len(names) == 5


def test_falls_back_to_text_matching_without_annotations(hub):
    seg = make_segment(0)
    seg["text"] = "That evening Nick Carraway walked along the shore thinking about the green light."
    seg["characters_in_scene"] = None
    names, _ = AnalyzerAgent("b").get_chapter_characters({}, [seg])
    assert names == {"Nick Carraway"}


def test_minor_characters_excluded_from_sheet_profiles(hub):
    segs = [
        make_segment(0, characters_in_scene=["Nick Carraway", "Owl Eyes"]),
        make_segment(1, characters_in_scene=["The Chester Beckers"]),
    ]
    _, chapter_profiles = AnalyzerAgent("b").get_chapter_characters({}, segs)
    roles = {p["name"]: p.get("role") for p in chapter_profiles}
    assert "Nick Carraway" in roles
    assert all(r in ("main", "supporting") for r in roles.values()), roles
