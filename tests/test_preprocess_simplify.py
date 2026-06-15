"""Preprocess is the single home of text simplification (architecture fix B).

The combined Layer-6 annotation prompt used to emit simplified_text, and the
same call also demanded full character names for the illustrator fields — so it
bled "Name. Name. Name." into the reader text. Simplification now runs as its
own pass (_simplify_chapter) using the dedicated simplifier, tagged
text_source='writer' so generation keeps it instead of re-doing it every chapter.
"""

from __future__ import annotations

import pytest

from src.core.provenance import (
    TEXT_SOURCE_PREPROCESS,
    TEXT_SOURCE_WRITER,
    keeps_existing_text,
)
from tests.conftest import make_segment


def test_simplify_chapter_writes_natural_text_tagged_writer(monkeypatch):
    from src.preprocessing import pipeline

    segs = [
        make_segment(0, text="Nick finished school and went to war.",
                     characters_in_scene=["Nick Carraway"], scene_background="A dusty office"),
        make_segment(1, text="Tom drove fast.", characters_in_scene=["Tom Buchanan"]),
    ]

    def fake_simplify(scenes, **kwargs):
        # One natural rewrite per scene, in order.
        return [
            {"page_text": "Nick finished school and went to war. Then off he set east.",
             "scene_direction": "Nick at a desk"},
            {"page_text": "Tom drove fast, vroom!", "scene_direction": ""},
        ]

    monkeypatch.setattr("src.generation.text_simplifier.simplify_text", fake_simplify)
    pipeline._simplify_chapter(segs, characters=[])

    assert segs[0]["simplified_text"].startswith("Nick finished school")
    assert segs[0]["scene_direction"] == "Nick at a desk"
    assert segs[0]["text_source"] == TEXT_SOURCE_WRITER
    # No scene_direction from the LLM → fall back to the scene background.
    assert segs[1]["scene_direction"] == ""  # seg 1 has no scene_background either
    assert segs[1]["text_source"] == TEXT_SOURCE_WRITER
    # And generation must KEEP this text (not re-simplify it).
    assert keeps_existing_text(segs[0])


def test_simplify_failed_page_stays_replaceable(monkeypatch):
    from src.preprocessing import pipeline

    segs = [make_segment(0, text="Some prose.")]

    def fake_simplify(scenes, **kwargs):
        # Simulate the simplifier's fallback: summary text + failure marker.
        return [{"page_text": "Some prose.", "scene_direction": "", "simplify_failed": True}]

    monkeypatch.setattr("src.generation.text_simplifier.simplify_text", fake_simplify)
    pipeline._simplify_chapter(segs, characters=[])

    # Marked replaceable so generation retries it, not frozen as final.
    assert segs[0]["text_source"] == TEXT_SOURCE_PREPROCESS
    assert not keeps_existing_text(segs[0])


def test_simplify_exception_leaves_text_for_generation(monkeypatch):
    from src.preprocessing import pipeline

    segs = [make_segment(0, text="Some prose.")]

    def boom(scenes, **kwargs):
        raise RuntimeError("LLM down")

    monkeypatch.setattr("src.generation.text_simplifier.simplify_text", boom)
    pipeline._simplify_chapter(segs, characters=[])  # must not raise

    # No text, no tag → generation will simplify it (empty is replaceable).
    assert not segs[0].get("simplified_text")
    assert not keeps_existing_text(segs[0])


def test_schema_version_invalidates_old_checkpoints():
    """The fingerprint carries a schema version so pre-B checkpoints (robotic
    text) are discarded and re-annotated + re-simplified."""
    from src.preprocessing.pipeline import _annotation_fingerprint

    fp = _annotation_fingerprint([make_segment(0)], characters=[])
    assert any("schema:v2" in h for h in fp)
