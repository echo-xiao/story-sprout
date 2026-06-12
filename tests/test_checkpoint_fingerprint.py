"""Annotation checkpoints replay only onto the SAME segmentation (pipeline.py).

Medium-risk review finding: checkpoints were replayed BY INDEX with no
validation — after a re-run re-segmented a chapter (LLM character extraction
is non-deterministic, so cleaned text and TextTiling boundaries shift), stale
annotations were pasted onto different text and extra segments were silently
left unannotated.
"""

from __future__ import annotations

import json

import pytest

import src.preprocessing.pipeline as pipeline
from tests.conftest import make_segment


@pytest.fixture()
def env(monkeypatch, tmp_path):
    calls = {"annotate": 0}

    def fake_annotate(title, ch_title, segs, characters):
        calls["annotate"] += 1
        for s in segs:
            s["scene_summary"] = f"summary of: {s['text'][:20]}"
            # Real _llm_annotate_chapter marks segments it annotated; the
            # caller only checkpoints fully-annotated chapters (and strips
            # the marker). Mirror that contract here.
            s["_annotated"] = True
        return segs

    monkeypatch.setattr(pipeline, "_llm_annotate_chapter", fake_annotate)
    monkeypatch.setattr("src.llm_client.generate_json", lambda *a, **k: {"summary": "s"})
    monkeypatch.setattr(pipeline, "_save", lambda *a, **k: None)

    preprocess_dir = tmp_path / "preprocess"
    preprocess_dir.mkdir()

    def run(segs):
        pipeline._layer6_annotate(
            "testbook", preprocess_dir, [{"title": "Ch 1"}], [], "Test Title",
            {0: segs}, skip_sheets=True,
        )
        return segs

    return {"run": run, "calls": calls, "dir": preprocess_dir}


def test_same_segmentation_replays(env):
    env["run"]([make_segment(0)])
    env["run"]([make_segment(0)])
    assert env["calls"]["annotate"] == 1


def test_changed_text_discards_checkpoint(env):
    env["run"]([make_segment(0)])
    changed = make_segment(0)
    changed["text"] = "completely different chapter text after a re-run " * 3
    restored = env["run"]([changed])
    assert env["calls"]["annotate"] == 2, "stale checkpoint must not replay onto new text"
    assert "completely different" in restored[0]["scene_summary"]


def test_extra_segment_gets_annotated_not_skipped(env):
    """The old index replay left segments beyond len(cached) silently
    unannotated; a count change must re-annotate the whole chapter."""
    env["run"]([make_segment(0)])
    two = [make_segment(0), make_segment(1, words=25)]
    restored = env["run"](two)
    assert env["calls"]["annotate"] == 2
    assert all("scene_summary" in s and s["scene_summary"] for s in restored)


def test_legacy_list_checkpoint_replays_on_length_match(env):
    """Pre-fingerprint checkpoints (bare lists) replay when the segment count
    matches — existing books must not re-bill annotation."""
    (env["dir"] / "annotations" ).mkdir(exist_ok=True)
    (env["dir"] / "annotations" / "ch000.json").write_text(
        json.dumps([{"scene_summary": "from legacy checkpoint"}])
    )
    restored = env["run"]([make_segment(0)])
    assert env["calls"]["annotate"] == 0
    assert restored[0]["scene_summary"] == "from legacy checkpoint"


def test_legacy_list_checkpoint_discarded_on_length_mismatch(env):
    (env["dir"] / "annotations").mkdir(exist_ok=True)
    (env["dir"] / "annotations" / "ch000.json").write_text(
        json.dumps([{"scene_summary": "legacy"}])
    )
    restored = env["run"]([make_segment(0), make_segment(1, words=25)])
    assert env["calls"]["annotate"] == 1
    assert all(s["scene_summary"].startswith("summary of:") for s in restored)
