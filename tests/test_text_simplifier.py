"""simplify_text page-number plumbing (generation/text_simplifier.py).

The LLM is stubbed; under test is the merge logic that maps LLM output pages
back onto input scenes — including partial runs with non-sequential page
numbers (a regression noted in the code: --pages 13,29 once became 4,5).
"""

from __future__ import annotations

import pytest

import src.generation.text_simplifier as ts


@pytest.fixture()
def llm(monkeypatch):
    """Stub generate_json; records prompts, returns one page per call."""
    state = {"calls": [], "response": None}

    def fake(prompt, system=""):
        state["calls"].append(prompt)
        if callable(state["response"]):
            return state["response"](prompt)
        return state["response"]

    monkeypatch.setattr(ts, "generate_json", fake)
    return state


def scene(page, text="Some original scene text that is long enough."):
    return {"page_number": page, "original_text": text, "key_characters": []}


def test_single_scene_keeps_renumbered_llm_page(llm):
    # LLM is asked for 1 page and answers "page_number": 1 even when the real
    # page is 13 — the merge must still attach the text to the scene.
    llm["response"] = {"pages": [{"page_number": 1, "page_text": "Simple!", "scene_direction": "desk"}]}
    out = ts.simplify_text([scene(13)], "4-6")
    assert len(out) == 1
    assert out[0]["page_text"] == "Simple!"


def test_multi_scene_partial_run_preserves_real_page_numbers(llm):
    llm["response"] = {"pages": [{"page_number": 1, "page_text": "Text.", "scene_direction": "d"}]}
    out = ts.simplify_text([scene(13), scene(29)], "4-6")
    assert [p["page_number"] for p in out] == [13, 29]


def test_multi_scene_passes_previous_page_text_as_context(llm):
    pages = iter(["First page text.", "Second page text."])

    def respond(prompt):
        return {"pages": [{"page_number": 1, "page_text": next(pages), "scene_direction": "d"}]}

    llm["response"] = respond
    ts.simplify_text([scene(1), scene(2)], "4-6")
    assert "First page text." in llm["calls"][1]


def test_extra_llm_pages_are_truncated(llm):
    llm["response"] = {
        "pages": [
            {"page_number": 1, "page_text": "One.", "scene_direction": "d"},
            {"page_number": 2, "page_text": "Hallucinated.", "scene_direction": "d"},
        ]
    }
    out = ts.simplify_text([scene(1)], "4-6")
    assert len(out) == 1
    assert out[0]["page_text"] == "One."


def test_invalid_age_group_falls_back(llm):
    llm["response"] = {"pages": [{"page_number": 1, "page_text": "T.", "scene_direction": "d"}]}
    out = ts.simplify_text([scene(1)], "not-an-age")
    assert len(out) == 1


def test_empty_scenes_returns_empty(llm):
    assert ts.simplify_text([], "4-6") == []
