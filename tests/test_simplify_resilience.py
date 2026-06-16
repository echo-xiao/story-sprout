"""One page's bad LLM response must not crash the whole chapter.

Prod root cause of "Gen chapter never updates the text": generate_json raises
ValueError("LLM returned invalid JSON") on a malformed/ truncated/ markdown-
wrapped model response, and nothing above it caught the raise — so a SINGLE bad
page crashed the entire chapter at the Writer stage and the text never synced.
simplify_text now isolates per-page failures (retry once, then fall back and
keep going), and the Writer stage never lets a total failure abort the chapter.
"""

from __future__ import annotations

import pytest


def test_one_bad_page_does_not_crash_the_batch(monkeypatch):
    import src.generation.text_simplifier as ts

    calls = {"n": 0}

    def flaky(prompt, system=""):
        # Page 2's single-scene call always returns invalid JSON; others are fine.
        calls["n"] += 1
        if "PAGE2" in prompt:
            raise ValueError("LLM returned invalid JSON")
        return {"pages": [{"page_number": 1, "page_text": "Nice kid text.",
                           "scene_direction": "a desk"}]}

    monkeypatch.setattr("src.llm_client.generate_json", flaky)

    scenes = [
        {"page_number": 1, "original_text": "PAGE1 prose", "scene_summary": "p1 summary"},
        {"page_number": 2, "original_text": "PAGE2 prose", "scene_summary": "p2 summary"},
        {"page_number": 3, "original_text": "PAGE3 prose", "scene_summary": "p3 summary"},
    ]
    out = ts.simplify_text(scenes)  # must NOT raise

    assert [p["page_number"] for p in out] == [1, 2, 3]
    # Good pages got real text; the bad page fell back to its summary, flagged.
    assert out[0]["page_text"] == "Nice kid text."
    assert out[1].get("simplify_failed") is True
    assert out[1]["page_text"] == "p2 summary"
    assert out[2]["page_text"] == "Nice kid text."


def test_bad_page_is_retried_once_before_fallback(monkeypatch):
    import src.generation.text_simplifier as ts

    attempts = {"p2": 0}

    def flaky(prompt, system=""):
        if "PAGE2" in prompt:
            attempts["p2"] += 1
            raise ValueError("invalid JSON")
        return {"pages": [{"page_number": 1, "page_text": "ok", "scene_direction": "d"}]}

    monkeypatch.setattr("src.llm_client.generate_json", flaky)
    ts.simplify_text([
        {"page_number": 1, "original_text": "PAGE1"},
        {"page_number": 2, "original_text": "PAGE2", "scene_summary": "fallback"},
    ])
    assert attempts["p2"] == 2, "a failing page must be retried once before falling back"


def test_writer_stage_never_crashes_on_total_simplify_failure(monkeypatch):
    """Even if simplify blows up entirely, the chapter keeps going with existing text."""
    import asyncio
    from src.agents.adk_pipeline import PipelineContext, WriterStage

    monkeypatch.setattr("src.agents.adk_pipeline._update_progress", lambda *a, **k: None)
    monkeypatch.setattr("src.agents.agent_log.log_event", lambda *a, **k: None)

    def boom(scenes, **k):
        raise RuntimeError("LLM totally down")

    monkeypatch.setattr("src.generation.text_simplifier.simplify_text", boom)

    ctx = PipelineContext("somebook", {"analysis": {}, "meta": {}}, 0, None, "4-6", False)
    ctx.scenes = [{"page_number": 1, "source_segment_id": 0, "simplified_text": "",
                   "scene_direction": "", "key_characters": [], "character_actions": [],
                   "scene_summary": "a summary", "scene_background": "", "original_text": "orig"}]

    async def run():
        async for _ in WriterStage("writer", ctx)._run_async_impl(None):
            pass
    asyncio.run(run())  # must NOT raise

    # The page kept usable text and is marked replaceable for a later retry.
    assert ctx.simplified[0]["page_text"] == "a summary"
    assert ctx.simplified[0]["text_source"] == "preprocess"
