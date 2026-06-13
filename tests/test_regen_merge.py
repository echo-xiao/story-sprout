"""Segment-regen write-back must merge, not clobber (routes/generation.py).

Medium-risk review finding: the regen background task saved the whole
analysis.json snapshot taken at request time AFTER its slow LLM call — the
exact lost-update anti-pattern editor.py's LLM endpoints already fixed. A
segment edit landing during the call was silently reverted.
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import make_segment


@pytest.fixture()
def store(monkeypatch, tmp_path):
    """File-backed _load_json/_save_json for the generation module, plus stubs
    for every Gemini-touching dependency the regen task imports."""
    path = tmp_path / "analysis.json"
    path.write_text(json.dumps({"segments": [make_segment(0)]}))

    def load(book_id, filename):
        return json.loads(path.read_text()) if filename == "analysis.json" else {}

    def save(book_id, filename, data):
        assert filename == "analysis.json"
        path.write_text(json.dumps(data))

    monkeypatch.setattr("src.routes.generation._load_json", load)
    monkeypatch.setattr("src.routes.generation._save_json", save)
    monkeypatch.setattr("src.routes.generation.GENERATED_DIR", tmp_path)
    # No illustration/QA/Mongo side effects — this test only cares about text.
    monkeypatch.setattr("src.generation.illustration.generate_illustrations", lambda *a, **k: None)
    monkeypatch.setattr("src.generation.character_sheet.generate_character_sheets", lambda *a, **k: [])
    return {"load": load, "save": save}


def test_concurrent_text_edit_survives_regen(client, monkeypatch, store):
    """An edit to another FIELD that lands during the simplify call must not
    be reverted by the regen's write-back."""

    def slow_simplify(scenes):
        analysis = store["load"]("b", "analysis.json")
        analysis["segments"][0]["text"] = "EDITED DURING LLM CALL"
        store["save"]("b", "analysis.json", analysis)
        return [{"page_text": "Simple text.", "scene_direction": "a desk"}]

    monkeypatch.setattr("src.generation.text_simplifier.simplify_text", slow_simplify)

    resp = client.post("/api/book/somebook/segment/0/regenerate")
    assert resp.status_code == 200

    final = store["load"]("b", "analysis.json")["segments"][0]
    assert final["simplified_text"] == "Simple text."
    assert final["scene_direction"] == "a desk"
    assert final["text"] == "EDITED DURING LLM CALL"


def test_user_text_typed_mid_regen_wins(client, monkeypatch, store):
    """If the user fills in simplified_text while the LLM runs, their version
    must be kept — not overwritten by the LLM output."""

    def slow_simplify(scenes):
        analysis = store["load"]("b", "analysis.json")
        analysis["segments"][0]["simplified_text"] = "USER TYPED THIS"
        store["save"]("b", "analysis.json", analysis)
        return [{"page_text": "LLM text.", "scene_direction": "a desk"}]

    monkeypatch.setattr("src.generation.text_simplifier.simplify_text", slow_simplify)

    resp = client.post("/api/book/somebook/segment/0/regenerate")
    assert resp.status_code == 200

    final = store["load"]("b", "analysis.json")["segments"][0]
    assert final["simplified_text"] == "USER TYPED THIS"


def test_claim_released_after_regen(client, monkeypatch, store):
    """The in-flight claim must be gone once the background task finishes."""
    monkeypatch.setattr(
        "src.generation.text_simplifier.simplify_text",
        lambda scenes, age: [{"page_text": "t", "scene_direction": "d"}],
    )
    from src.routes.helpers import _active_regens
    resp = client.post("/api/book/somebook/segment/0/regenerate")
    assert resp.status_code == 200
    assert ("somebook", "segment", 0) not in _active_regens
