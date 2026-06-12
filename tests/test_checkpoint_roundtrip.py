"""Preprocess annotation checkpoint roundtrip (preprocessing/pipeline.py).

A chapter checkpoint must restore EVERYTHING _llm_annotate_chapter produced;
any field it drops silently disappears for every resumed run (review finding
P0-4: simplified_text is dropped, so resumed books QA against raw novel text).
"""

from __future__ import annotations

import pytest

import src.preprocessing.pipeline as pipeline
from tests.conftest import make_segment

ANNOTATION = {
    "characters_in_scene": ["Nick Carraway"],
    "character_actions": [{"name": "Nick Carraway", "action": "writes at a desk"}],
    "scene_background": "Nick Carraway's House",
    "scene_summary": "Nick remembers his father's advice.",
    "sentiment": "calm",
    "is_key_event": True,
    "event_description": "Nick reflects",
    "simplified_text": "Nick sat at his desk and remembered his dad's kind words.",
}


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """Run _layer6_annotate twice against the same checkpoint dir.

    First run: the annotate stub writes ANNOTATION onto each segment.
    Second run: fresh un-annotated segments; the stub must NOT be called
    (checkpoint hit) and everything must come back from the checkpoint file.
    """
    calls = {"annotate": 0}

    def fake_annotate(title, ch_title, segs, characters):
        calls["annotate"] += 1
        for s in segs:
            s.update(ANNOTATION)
            # Real _llm_annotate_chapter marks segments it annotated; the
            # caller only checkpoints fully-annotated chapters (and strips
            # the marker before dumping). Mirror that contract here.
            s["_annotated"] = True
        return segs

    monkeypatch.setattr(pipeline, "_llm_annotate_chapter", fake_annotate)
    # chapter summaries + analysis writes must not hit LLM/Mongo
    monkeypatch.setattr("src.llm_client.generate_json", lambda *a, **k: {"summary": "s"})
    monkeypatch.setattr(pipeline, "_save", lambda *a, **k: None)

    preprocess_dir = tmp_path / "preprocess"
    preprocess_dir.mkdir()

    def run():
        segs = [make_segment(0)]
        for key in ANNOTATION:  # fresh segments carry no annotations
            segs[0].pop(key, None)
        pipeline._layer6_annotate(
            "testbook", preprocess_dir, [{"title": "Ch 1"}], [], "Test Title",
            {0: segs}, skip_sheets=True,
        )
        return segs[0]

    return {"run": run, "calls": calls}


def test_second_run_uses_checkpoint(env):
    env["run"]()
    restored = env["run"]()
    assert env["calls"]["annotate"] == 1, "checkpoint hit must skip re-annotation"
    assert restored["scene_summary"] == ANNOTATION["scene_summary"]
    assert restored["characters_in_scene"] == ANNOTATION["characters_in_scene"]
    assert restored["sentiment"] == ANNOTATION["sentiment"]
    assert restored["is_key_event"] is True


def test_checkpoint_preserves_simplified_text(env):
    env["run"]()
    restored = env["run"]()
    assert restored.get("simplified_text") == ANNOTATION["simplified_text"]
