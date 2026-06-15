"""Layer 2/3 checkpointing + Layer 6 checkpoint poisoning guards
(src/preprocessing/pipeline.py), plus the roman-numeral chapter detection fix
(src/extraction/text_input.py).

- Layers 2-3 (LLM character/location identification) used to re-run on every
  attempt; being non-deterministic they shifted aliases → cleaned text →
  segmentation → every Layer-6 fingerprint mismatched, so resume after a
  timeout re-billed everything. A sidecar checkpoints.json now records input
  fingerprints and the saved outputs are reused when they match.
- A PARTIAL scene_number match (3 of 10) used to checkpoint a chapter whose
  unmatched segments carried empty defaults — permanently. Checkpoints are
  now written only for fully-annotated chapters.
- The annotation fingerprint now includes the character roster (annotations
  reference canonical names), so a roster change invalidates a replay.
"""

from __future__ import annotations

import json

import pytest

from src.extraction.text_input import _ROMAN_NUMERAL_PATTERN

import src.preprocessing.pipeline as pipeline
from tests.conftest import make_segment


# ── Layer 2/3 checkpoint reuse ───────────────────────────────────


CHAPTERS = [{"title": "Ch 1", "text": "Alice fell down the rabbit hole."}]


@pytest.fixture()
def l2(monkeypatch, tmp_path):
    calls = {"chars": 0, "locs": 0}
    monkeypatch.setattr(pipeline, "GENERATED_DIR", tmp_path)
    # _save mirrors to Mongo best-effort — no network in tests.
    monkeypatch.setattr("src.core.db.save_preprocess_file", lambda *a, **k: True)

    def fake_chars(title, chapters):
        calls["chars"] += 1
        return [{"canonical_name": "Alice", "gender": "female", "role": "main",
                 "visual_details": {"hair": "blond"}}]  # hair set → no autofill LLM call

    def fake_locs(title, chapters):
        calls["locs"] += 1
        return [{"name": "Wonderland", "importance": "major"}]

    monkeypatch.setattr(pipeline, "_llm_identify_characters", fake_chars)
    monkeypatch.setattr(pipeline, "_llm_identify_locations", fake_locs)

    pre = tmp_path / "book" / "preprocess"
    pre.mkdir(parents=True)
    return {"calls": calls, "dir": pre}


def test_layer2_3_reuse_skips_llm_on_same_inputs(l2):
    first = pipeline._layer2_identify_characters("book", l2["dir"], CHAPTERS, "Alice")
    assert l2["calls"] == {"chars": 1, "locs": 1}
    second = pipeline._layer2_identify_characters("book", l2["dir"], CHAPTERS, "Alice")
    assert l2["calls"] == {"chars": 1, "locs": 1}, "matching fingerprint must skip the LLM"
    assert [c["canonical_name"] for c in second] == [c["canonical_name"] for c in first]
    # Sidecar recorded both layers
    ckpts = json.loads((l2["dir"] / "checkpoints.json").read_text())
    assert ckpts["layer2_fp"] == ckpts["layer3_fp"]


def test_layer2_3_invalidated_by_text_change(l2):
    pipeline._layer2_identify_characters("book", l2["dir"], CHAPTERS, "Alice")
    changed = [{"title": "Ch 1", "text": "A completely different chapter text."}]
    pipeline._layer2_identify_characters("book", l2["dir"], changed, "Alice")
    assert l2["calls"] == {"chars": 2, "locs": 2}


def test_layer2_3_invalidated_by_title_change(l2):
    pipeline._layer2_identify_characters("book", l2["dir"], CHAPTERS, "Alice")
    pipeline._layer2_identify_characters("book", l2["dir"], CHAPTERS, "Alice 2nd ed")
    assert l2["calls"] == {"chars": 2, "locs": 2}


def test_layer2_no_reuse_without_sidecar(l2):
    """A pre-existing llm_characters.json WITHOUT a checkpoint record (legacy
    book) must not be blindly trusted — its inputs are unknown."""
    (l2["dir"] / "llm_characters.json").write_text(json.dumps({"characters": [
        {"canonical_name": "Stale"}]}))
    chars = pipeline._layer2_identify_characters("book", l2["dir"], CHAPTERS, "Alice")
    assert l2["calls"]["chars"] == 1
    assert chars[0]["canonical_name"] == "Alice"


def test_layer2_corrupt_saved_file_reruns_llm(l2):
    pipeline._layer2_identify_characters("book", l2["dir"], CHAPTERS, "Alice")
    (l2["dir"] / "llm_characters.json").write_text('{"characters": [tor')  # torn
    chars = pipeline._layer2_identify_characters("book", l2["dir"], CHAPTERS, "Alice")
    assert l2["calls"]["chars"] == 2
    assert chars[0]["canonical_name"] == "Alice"


# ── Layer 6: partial-match checkpoint poisoning ──────────────────


@pytest.fixture()
def l6(monkeypatch, tmp_path):
    """Drive the REAL _llm_annotate_chapter through _layer6_annotate with a
    stubbed LLM; capture what _save would persist."""
    saved = {}
    monkeypatch.setattr(pipeline, "_save",
                        lambda pre, name, data, subdir=None: saved.__setitem__(name, data))
    # Text simplification is now a separate preprocess pass — stub it so the
    # tests stay hermetic (text_simplifier imports generate_json at module load,
    # so the src.llm_client mock wouldn't reach it).
    monkeypatch.setattr(
        "src.generation.text_simplifier.simplify_text",
        lambda scenes, **k: [{"page_text": f"kid text {i}", "scene_direction": "dir"}
                             for i, _ in enumerate(scenes)],
    )
    pre = tmp_path / "preprocess"
    pre.mkdir()
    return {"dir": pre, "saved": saved,
            "ckpt": pre / "annotations" / "ch000.json"}


def _ann(n, **extra):
    return {"scene_number": n, "scene_summary": f"summary {n}",
            "scene_background": "bg", "sentiment": "neutral",
            "simplified_text": "simple", "is_key_event": False,
            "event_description": None, "characters_in_scene": [], **extra}


def test_partial_match_writes_no_checkpoint(l6, monkeypatch):
    # 1 of 2 segments annotated → must NOT checkpoint (the empty defaults
    # would replay forever under a valid fingerprint).
    monkeypatch.setattr("src.llm_client.generate_json",
                        lambda *a, **k: {"annotations": [_ann(1)], "summary": "s"})
    segs = [make_segment(0), make_segment(1, words=25)]
    pipeline._layer6_annotate("book", l6["dir"], [{"title": "Ch 1"}], [], "Title",
                              {0: segs}, skip_sheets=True)
    assert not l6["ckpt"].exists(), "partial annotation must not be checkpointed"
    # The marker is internal — never persisted
    assert all("_annotated" not in s for s in l6["saved"]["analysis"]["segments"])


def test_full_match_writes_checkpoint_with_roster(l6, monkeypatch):
    monkeypatch.setattr("src.llm_client.generate_json",
                        lambda *a, **k: {"annotations": [_ann(1), _ann(2)], "summary": "s"})
    segs = [make_segment(0), make_segment(1, words=25)]
    characters = [{"canonical_name": "Alice"}]
    pipeline._layer6_annotate("book", l6["dir"], [{"title": "Ch 1"}], characters, "Title",
                              {0: segs}, skip_sheets=True)
    ckpt = json.loads(l6["ckpt"].read_text())
    assert len(ckpt["annotations"]) == 2
    # Fingerprint carries a roster hash (+ a trailing schema-version token).
    assert any(h.startswith("roster:") for h in ckpt["fingerprint"])
    assert all("_annotated" not in a for a in ckpt["annotations"])
    assert l6["saved"]["analysis"]["annotation_failed_chapters"] == []


def test_fingerprint_roster_sensitivity():
    segs = [make_segment(0)]
    fp_a = pipeline._annotation_fingerprint(segs, [{"canonical_name": "Alice"}])
    fp_b = pipeline._annotation_fingerprint(segs, [{"canonical_name": "Bob"}])
    fp_a2 = pipeline._annotation_fingerprint(segs, [{"canonical_name": "Alice"}])
    assert fp_a != fp_b, "a roster change must invalidate the checkpoint"
    assert fp_a == fp_a2


def test_roster_change_forces_reannotation(l6, monkeypatch):
    calls = {"n": 0}

    def fake_annotate(title, ch_title, segs, characters):
        calls["n"] += 1
        for s in segs:
            s["scene_summary"] = "s"
            s["_annotated"] = True
        return segs

    monkeypatch.setattr(pipeline, "_llm_annotate_chapter", fake_annotate)
    monkeypatch.setattr("src.llm_client.generate_json", lambda *a, **k: {"summary": "s"})

    pipeline._layer6_annotate("book", l6["dir"], [{"title": "Ch 1"}],
                              [{"canonical_name": "Alice"}], "Title",
                              {0: [make_segment(0)]}, skip_sheets=True)
    pipeline._layer6_annotate("book", l6["dir"], [{"title": "Ch 1"}],
                              [{"canonical_name": "Bob"}], "Title",
                              {0: [make_segment(0)]}, skip_sheets=True)
    assert calls["n"] == 2, "renamed roster must discard the old checkpoint"


def test_unparseable_scene_number_fallback_never_overwrites(monkeypatch):
    """The idx+1 fallback used to clobber a genuine annotation at that key."""
    monkeypatch.setattr("src.llm_client.generate_json", lambda *a, **k: {"annotations": [
        {"scene_number": 2, "scene_summary": "genuine for scene 2"},
        {"scene_number": "junk", "scene_summary": "fallback would land on 2"},
    ]})
    segs = [{"text": "seg one"}, {"text": "seg two"}]
    out = pipeline._llm_annotate_chapter("Title", "Ch 1", segs, [])
    assert out[1]["scene_summary"] == "genuine for scene 2"


def test_all_chapters_failed_exits_nonzero(l6, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(pipeline, "_llm_annotate_chapter", boom)
    with pytest.raises(SystemExit) as exc:
        pipeline._layer6_annotate("book", l6["dir"], [{"title": "Ch 1"}], [], "Title",
                                  {0: [make_segment(0)]}, skip_sheets=True)
    assert exc.value.code != 0


def test_failed_chapters_recorded_in_analysis(l6, monkeypatch):
    def flaky(title, ch_title, segs, characters):
        if ch_title == "Ch B":
            raise RuntimeError("LLM hiccup")
        for s in segs:
            s["scene_summary"] = "s"
            s["_annotated"] = True
        return segs

    monkeypatch.setattr(pipeline, "_llm_annotate_chapter", flaky)
    monkeypatch.setattr("src.llm_client.generate_json", lambda *a, **k: {"summary": "s"})

    pipeline._layer6_annotate(
        "book", l6["dir"], [{"title": "Ch A"}, {"title": "Ch B"}], [], "Title",
        {0: [make_segment(0)], 1: [make_segment(1, ch_idx=1, words=25)]},
        skip_sheets=True,
    )
    assert l6["saved"]["analysis"]["annotation_failed_chapters"] == [1]


# ── Roman numeral chapter detection (text_input.py) ──────────────


@pytest.mark.parametrize("numeral", [
    "I", "II", "III", "IV", "V", "IX", "X", "XIV", "XV",
    "XVI", "XIX", "XX", "XXIX", "XXX", "XXXIV", "XXXIX",
])
def test_roman_numerals_1_to_39_accepted(numeral):
    assert _ROMAN_NUMERAL_PATTERN.match(numeral), f"{numeral} must be a chapter marker"


@pytest.mark.parametrize("bad", ["", "IIII", "VX", "XXXX", "XL", "IC", "VV", "A", "XVX"])
def test_invalid_roman_numerals_rejected(bad):
    assert not _ROMAN_NUMERAL_PATTERN.match(bad)


def test_chapters_16_plus_detected_not_merged():
    """Chapters XVI+ used to silently merge into chapter XV."""
    text = "\n\n".join(
        f"{numeral}\n\nSome chapter body text for this chapter."
        for numeral in ["XIV", "XV", "XVI", "XVII"]
    )
    from src.extraction.text_input import _detect_chapters
    chapters = _detect_chapters(text)
    titles = [c["title"] for c in chapters]
    assert "Chapter XVI" in titles and "Chapter XVII" in titles
