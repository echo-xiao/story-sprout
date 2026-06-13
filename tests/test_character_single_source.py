"""Character profile data has ONE read source: the consistency hub.

Root cause A (whack-a-mole bug class): character data existed in three copies
— the `characters` collection (edited by the editor), llm_characters.json, and
analysis.json['character_profiles'] (written once at preprocess, NEVER updated
by an edit). Whole-chapter generation read the dead copy, so a renamed/
re-described character was drawn with its stale look.

Fix = delete the copy: every generation path resolves character profiles
through load_character_profiles (→ load_characters → the hub). These tests pin
that the subprocess read path honours a hub edit and that no generation code
still reads the dead key.
"""

from __future__ import annotations

import pathlib
import re

import src.routes.helpers as helpers
from src.agents.analyzer import AnalyzerAgent
from tests.conftest import make_segment


def test_get_chapter_characters_honours_hub_edit(monkeypatch):
    # Hub (characters collection) carries the CURRENT, edited appearance.
    monkeypatch.setattr(helpers, "load_characters",
                        lambda bid: [{"canonical_name": "Alice", "role": "main",
                                      "appearance": "NEW silver coat", "description": ""}])
    # analysis.json still holds the STALE pre-edit copy — it must be ignored.
    data = {"analysis": {
        "characters": [{"name": "Alice"}],
        "character_profiles": [{"name": "Alice", "role": "main",
                                "appearance_description": ["OLD red coat", ""]}],
    }}
    seg = make_segment(0, characters_in_scene=["Alice"], text="Alice walks " * 10)

    _, profiles = AnalyzerAgent("b").get_chapter_characters(data, [seg])

    assert profiles, "Alice should be selected for the chapter"
    assert profiles[0]["appearance_description"][0] == "NEW silver coat"


def test_load_character_profiles_maps_hub_shape(monkeypatch):
    monkeypatch.setattr(helpers, "load_characters",
                        lambda bid: [{"canonical_name": "Bob", "role": "supporting",
                                      "gender": "male", "appearance": "tall",
                                      "description": "kind", "visual_details": {"hair": "black"}}])
    profs = helpers.load_character_profiles("b")
    assert profs == [{
        "name": "Bob", "role": "supporting", "gender": "male",
        "aliases": [], "personality_traits": [],
        "appearance_description": ["tall", "kind"],
        "visual_details": {"hair": "black"},
    }]


def test_no_generation_path_reads_the_dead_copy():
    """grep invariant: the analysis['character_profiles'] key has no readers
    left in the generation chain (src/agents, scripts, src/generation)."""
    root = pathlib.Path(__file__).resolve().parent.parent
    pat = re.compile(r"""\[["']character_profiles["']\]|\.get\(["']character_profiles["']""")
    offenders = []
    for sub in ("src/agents", "src/generation", "scripts"):
        for f in (root / sub).rglob("*.py"):
            if pat.search(f.read_text(encoding="utf-8")):
                offenders.append(str(f.relative_to(root)))
    assert not offenders, f"character_profiles still read in generation chain: {offenders}"
