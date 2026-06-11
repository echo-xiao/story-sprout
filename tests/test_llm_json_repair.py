"""generate_json repair chain (src/llm_client.py).

The raw LLM call is stubbed out; only the parse/repair pipeline is under test.
"""

from __future__ import annotations

import pytest

import src.llm_client as llm_client


@pytest.fixture()
def gemini_raw(monkeypatch):
    """Route generate_json to a canned Gemini response."""
    holder = {"raw": "{}"}
    monkeypatch.setattr(llm_client, "TEXT_LLM", "gemini")
    monkeypatch.setattr(llm_client, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(llm_client, "_call_gemini", lambda *a, **k: holder["raw"])
    return holder


def test_clean_json(gemini_raw):
    gemini_raw["raw"] = '{"a": 1, "b": [2, 3]}'
    assert llm_client.generate_json("p") == {"a": 1, "b": [2, 3]}


def test_markdown_fenced_json(gemini_raw):
    gemini_raw["raw"] = 'Here you go:\n```json\n{"pages": [1]}\n```\nDone.'
    assert llm_client.generate_json("p") == {"pages": [1]}


def test_markdown_fenced_nested_braces(gemini_raw):
    # The non-greedy fence regex can't span nested braces; the later greedy
    # {.*} fallback must still recover this.
    gemini_raw["raw"] = '```json\n{"outer": {"inner": 1}}\n```'
    assert llm_client.generate_json("p") == {"outer": {"inner": 1}}


def test_trailing_commas(gemini_raw):
    gemini_raw["raw"] = '{"a": [1, 2,], "b": {"c": 3,},}'
    assert llm_client.generate_json("p") == {"a": [1, 2], "b": {"c": 3}}


def test_prose_around_object(gemini_raw):
    gemini_raw["raw"] = 'Sure! The result is {"ok": true} — let me know.'
    assert llm_client.generate_json("p") == {"ok": True}


def test_prose_plus_trailing_comma(gemini_raw):
    gemini_raw["raw"] = 'Result: {"items": [1, 2,],} thanks'
    assert llm_client.generate_json("p") == {"items": [1, 2]}


def test_garbage_raises_value_error(gemini_raw):
    gemini_raw["raw"] = "I could not produce JSON, sorry."
    with pytest.raises(ValueError):
        llm_client.generate_json("p")


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setattr(llm_client, "TEXT_LLM", "claude")
    with pytest.raises(ValueError, match="Unknown TEXT_LLM"):
        llm_client.generate_json("p")
