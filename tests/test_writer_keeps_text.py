"""Chapter re-runs must not overwrite existing page text (H1).

High-risk review finding: build_scenes dropped each segment's simplified_text,
so the Writer stage re-simplified EVERY page on a chapter re-run — silently
overwriting user edits, and (because the Artist skips pages whose image is
cached) permanently desyncing the stored text from the text painted into the
image. The pipeline now keeps existing text, matching the single-page regen.
"""

from __future__ import annotations

import asyncio

import pytest

from src.agents.analyzer import AnalyzerAgent
from tests.conftest import make_segment


def test_build_scenes_carries_existing_text():
    analyzer = AnalyzerAgent("somebook")
    seg = make_segment(0, simplified_text="USER EDITED TEXT", scene_direction="a desk at dusk")
    scenes = analyzer.build_scenes([seg], characters=[])
    assert scenes[0]["simplified_text"] == "USER EDITED TEXT"
    assert scenes[0]["scene_direction"] == "a desk at dusk"


def test_build_scenes_empty_text_for_unedited_segments():
    analyzer = AnalyzerAgent("somebook")
    scenes = analyzer.build_scenes([make_segment(0)], characters=[])
    assert scenes[0]["simplified_text"] == ""


@pytest.fixture()
def writer_stage(monkeypatch):
    """A WriterStage over a context with one edited + one unedited scene,
    with the LLM and all side-channels stubbed."""
    from src.agents.adk_pipeline import PipelineContext, WriterStage

    simplify_calls: list[list[dict]] = []

    def fake_simplify(scenes, original_text="", language="en",
                      characters=None, character_sheets=None):
        simplify_calls.append(scenes)
        return [{**s, "page_text": f"LLM TEXT p{s['page_number']}"} for s in scenes]

    monkeypatch.setattr("src.generation.text_simplifier.simplify_text", fake_simplify)
    monkeypatch.setattr("src.agents.adk_pipeline._update_progress", lambda *a, **k: None)
    monkeypatch.setattr("src.agents.agent_log.log_event", lambda *a, **k: None)

    ctx = PipelineContext("somebook", {"analysis": {}, "meta": {}}, 0, None, "4-6", False)
    ctx.scenes = [
        {"page_number": 1, "source_segment_id": 0, "simplified_text": "USER EDITED TEXT",
         "text_source": "user",  # a hand-edit — kept, never re-simplified
         "scene_direction": "kept direction", "key_characters": [], "character_actions": [],
         "scene_summary": "", "scene_background": "", "original_text": "orig 1"},
        {"page_number": 2, "source_segment_id": 1, "simplified_text": "",
         "scene_direction": "", "key_characters": [], "character_actions": [],
         "scene_summary": "", "scene_background": "", "original_text": "orig 2"},
    ]
    stage = WriterStage("writer", ctx)
    return {"ctx": ctx, "stage": stage, "calls": simplify_calls}


def _drive(stage):
    async def run():
        async for _ in stage._run_async_impl(None):
            pass
    asyncio.run(run())


def test_writer_only_simplifies_pages_without_text(writer_stage):
    _drive(writer_stage["stage"])
    sent = [s["page_number"] for batch in writer_stage["calls"] for s in batch]
    assert sent == [2], "the edited page must NOT be sent to the LLM"


def test_writer_writes_text_sync_payload_early_when_deferred(writer_stage, tmp_path):
    """The payload must be written in the Writer stage — BEFORE the slow Artist
    loop — so a timeout/crash mid-illustration can't discard the natural text
    (the prod bug: a 39-page chapter always ran past the subprocess timeout, so
    the end-of-run sync never happened and the text never updated)."""
    import json
    from src.agents.adk_pipeline import TEXT_SYNC_FILENAME

    ctx = writer_stage["ctx"]
    ctx.defer_text_sync = True
    ctx.chapter_dir = tmp_path
    _drive(writer_stage["stage"])

    payload_path = tmp_path / TEXT_SYNC_FILENAME
    assert payload_path.exists(), "payload must exist right after the Writer stage"
    texts = {p["simplified_text"] for p in json.loads(payload_path.read_text())}
    assert "USER EDITED TEXT" in texts  # kept user page
    assert "LLM TEXT p2" in texts       # freshly simplified page


def test_writer_keeps_user_text_and_merges_in_order(writer_stage):
    _drive(writer_stage["stage"])
    simplified = writer_stage["ctx"].simplified
    assert [s["page_number"] for s in simplified] == [1, 2]
    assert simplified[0]["page_text"] == "USER EDITED TEXT"
    assert simplified[1]["page_text"] == "LLM TEXT p2"


def test_force_regen_redraws_images_but_keeps_text(writer_stage, monkeypatch):
    """PBG_FORCE_REGEN means "redraw the images", NOT "recompute the text".
    The Writer is provenance-driven, so a chapter re-gen must keep user/Writer
    text instead of burning an LLM pass on every page each time."""
    monkeypatch.setenv("PBG_FORCE_REGEN", "1")
    _drive(writer_stage["stage"])
    sent = [s["page_number"] for batch in writer_stage["calls"] for s in batch]
    assert sent == [2], "even under force, an existing-text page must NOT be re-simplified"
