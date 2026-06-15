"""Regression: force-regen re-simplifies EVERY page; normal runs keep only text
the user or the Writer owns.

The chapter Writer used to keep ANY page that already had text, so the preprocess
annotation's robotic ("Nick Carraway sat. Nick Carraway thought.") text survived
forever. Keeping is now driven by provenance (text_source), not "is it non-empty?":
robotic preprocess text is rewritten even without force, while user edits and
prior Writer text are preserved. Under PBG_FORCE_REGEN every page is rewritten.
"""

from __future__ import annotations

from src.agents.adk_pipeline import _writer_split


SCENES = [
    # Robotic preprocess text — replaceable, even on a non-force run.
    {"page_number": 1, "simplified_text": "Nick Carraway sat. Nick Carraway thought.",
     "text_source": "preprocess"},
    {"page_number": 2, "simplified_text": ""},                                     # empty
    {"page_number": 3},                                                            # no field (legacy → preprocess)
    # A hand-edit and an earlier Writer rewrite — both kept on a non-force run.
    {"page_number": 4, "simplified_text": "I wrote this myself.", "text_source": "user"},
    {"page_number": 5, "simplified_text": "Off he set for the East.", "text_source": "writer"},
]


def test_force_resimplifies_every_page():
    to_write, kept = _writer_split(SCENES, force=True)
    assert [s["page_number"] for s in to_write] == [1, 2, 3, 4, 5]  # all re-simplified
    assert kept == []                                               # nothing kept


def test_no_force_resimplifies_robotic_and_empty_keeps_user_and_writer():
    to_write, kept = _writer_split(SCENES, force=False)
    assert [s["page_number"] for s in to_write] == [1, 2, 3]   # robotic + empty + legacy
    assert [s["page_number"] for s in kept] == [4, 5]          # user + writer text preserved
    assert {s["page_text"] for s in kept} == {"I wrote this myself.", "Off he set for the East."}
