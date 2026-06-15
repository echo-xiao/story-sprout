"""Regression: force-regen re-simplifies EVERY page; normal runs keep existing text.

The chapter Writer used to always keep pages that already had text, so "Gen
chapter" never rewrote the preprocess annotation's robotic ("Nick Carraway sat.
Nick Carraway thought.") text. Under PBG_FORCE_REGEN the warm simplifier must run
on every page; without it, edited/existing text is preserved.
"""

from __future__ import annotations

from src.agents.adk_pipeline import _writer_split


SCENES = [
    {"page_number": 1, "simplified_text": "Nick Carraway sat in a quiet room."},  # has text
    {"page_number": 2, "simplified_text": ""},                                     # empty
    {"page_number": 3},                                                            # no field
]


def test_force_resimplifies_every_page():
    to_write, kept = _writer_split(SCENES, force=True)
    assert [s["page_number"] for s in to_write] == [1, 2, 3]  # all re-simplified
    assert kept == []                                          # nothing kept


def test_no_force_keeps_existing_text():
    to_write, kept = _writer_split(SCENES, force=False)
    assert [s["page_number"] for s in to_write] == [2, 3]      # only the empty ones
    assert [s["page_number"] for s in kept] == [1]             # page 1's text preserved
    assert kept[0]["page_text"] == "Nick Carraway sat in a quiet room."
