"""Per-page text provenance — the framework fix for "Gen chapter changed nothing".

`simplified_text` lives in both analysis.json (editor) and chapter_data.json
(PDF). It used to be reconciled by "non-empty == user edit, don't overwrite",
but preprocess PRE-FILLS simplified_text, so that froze the robotic first-pass
text: the Writer's natural rewrite reached the PDF but never the editor.

Each segment now carries `text_source` ∈ {preprocess, writer, user}; only
`user` is protected from (re)generation. These tests pin that contract end to
end: the provenance helpers, the Writer split, and the editor write paths.
"""

from __future__ import annotations

import json

import pytest

from src.core.provenance import (
    TEXT_SOURCE_PREPROCESS,
    TEXT_SOURCE_USER,
    TEXT_SOURCE_WRITER,
    effective_source,
    is_user_edited,
    keeps_existing_text,
)
from tests.conftest import make_segment


# --- provenance helpers -----------------------------------------------------

def test_is_user_edited_only_true_for_user():
    assert is_user_edited({"text_source": TEXT_SOURCE_USER})
    assert not is_user_edited({"text_source": TEXT_SOURCE_WRITER})
    assert not is_user_edited({"text_source": TEXT_SOURCE_PREPROCESS})
    assert not is_user_edited({})  # legacy / unset is never "user"


def test_effective_source_infers_legacy_data():
    # Legacy segment with text but no tag → preprocess (replaceable).
    assert effective_source({"simplified_text": "hi"}) == TEXT_SOURCE_PREPROCESS
    # Legacy segment with no text → unset.
    assert effective_source({"simplified_text": ""}) == ""
    # Explicit tag always wins.
    assert effective_source(
        {"simplified_text": "hi", "text_source": TEXT_SOURCE_USER}
    ) == TEXT_SOURCE_USER


def test_keeps_existing_text_protects_user_and_writer_only():
    assert keeps_existing_text({"simplified_text": "t", "text_source": TEXT_SOURCE_USER})
    assert keeps_existing_text({"simplified_text": "t", "text_source": TEXT_SOURCE_WRITER})
    # Robotic preprocess text is NOT kept — it gets re-simplified.
    assert not keeps_existing_text({"simplified_text": "t", "text_source": TEXT_SOURCE_PREPROCESS})
    # Legacy text (no tag) is treated as preprocess → re-simplified.
    assert not keeps_existing_text({"simplified_text": "t"})
    # Empty page is always re-simplified.
    assert not keeps_existing_text({"simplified_text": "", "text_source": TEXT_SOURCE_USER})


# --- Writer split -----------------------------------------------------------

def _scenes():
    return [
        {"source_segment_id": 0, "simplified_text": ""},                                   # empty
        {"source_segment_id": 1, "simplified_text": "robot", "text_source": TEXT_SOURCE_PREPROCESS},
        {"source_segment_id": 2, "simplified_text": "nice", "text_source": TEXT_SOURCE_WRITER},
        {"source_segment_id": 3, "simplified_text": "mine", "text_source": TEXT_SOURCE_USER},
        {"source_segment_id": 4, "simplified_text": "legacy"},                             # no tag
    ]


def test_writer_split_force_rewrites_everything():
    from src.agents.adk_pipeline import _writer_split

    to_write, kept = _writer_split(_scenes(), force=True)
    assert [s["source_segment_id"] for s in to_write] == [0, 1, 2, 3, 4]
    assert kept == []


def test_writer_split_nonforce_resimplifies_preprocess_and_empty_only():
    from src.agents.adk_pipeline import _writer_split

    to_write, kept = _writer_split(_scenes(), force=False)
    # Empty (0), robotic preprocess (1), and legacy/untagged (4) get rewritten.
    assert {s["source_segment_id"] for s in to_write} == {0, 1, 4}
    # Writer (2) and user (3) text is kept and surfaced as page_text.
    assert {s["source_segment_id"] for s in kept} == {2, 3}
    by_id = {s["source_segment_id"]: s for s in kept}
    assert by_id[2]["page_text"] == "nice"
    assert by_id[3]["page_text"] == "mine"


# --- editor write paths -----------------------------------------------------

@pytest.fixture()
def store(monkeypatch, tmp_path):
    """Disk-only analysis store over a temp file (no MongoDB)."""
    path = tmp_path / "analysis.json"
    path.write_text(json.dumps({"segments": [
        make_segment(0, simplified_text="robotic", text_source=TEXT_SOURCE_PREPROCESS),
    ]}))

    def load(book_id, filename):
        return json.loads(path.read_text()) if filename == "analysis.json" else {}

    def save(book_id, filename, data):
        path.write_text(json.dumps(data))

    monkeypatch.setattr("src.routes.editor._load_json", load)
    monkeypatch.setattr("src.routes.editor._save_json", save)
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.routes.helpers.GENERATED_DIR", tmp_path)
    return path


def test_put_segment_text_marks_user_owned(client, store):
    """Hand-editing the text claims ownership — a later re-gen must not touch it."""
    resp = client.put("/api/book/somebook/segment/0",
                       json={"simplified_text": "I wrote this myself."})
    assert resp.status_code == 200
    seg = json.loads(store.read_text())["segments"][0]
    assert seg["simplified_text"] == "I wrote this myself."
    assert seg["text_source"] == TEXT_SOURCE_USER


def test_put_segment_nontext_field_does_not_claim_ownership(client, store):
    """Editing only a non-text field leaves the (replaceable) provenance alone."""
    resp = client.put("/api/book/somebook/segment/0", json={"sentiment": "tense"})
    assert resp.status_code == 200
    seg = json.loads(store.read_text())["segments"][0]
    assert seg["text_source"] == TEXT_SOURCE_PREPROCESS  # unchanged


def test_llm_simplify_endpoint_marks_writer_not_user(client, store, monkeypatch):
    """The 'Generate' button is LLM text, not a hand-edit — a re-gen may replace it."""
    monkeypatch.setattr(
        "src.generation.text_simplifier.simplify_text",
        lambda scenes: [{"page_text": "Auto text.", "scene_direction": "a desk"}],
    )
    resp = client.post("/api/book/somebook/segment/0/simplify")
    assert resp.status_code == 200
    seg = json.loads(store.read_text())["segments"][0]
    assert seg["simplified_text"] == "Auto text."
    assert seg["text_source"] == TEXT_SOURCE_WRITER
