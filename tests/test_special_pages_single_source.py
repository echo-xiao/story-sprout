"""Root cause A2: whole-chapter generation reads special-page content from the
record (the single source the editor + web regen use), not a raw re-derivation.

ensure_special_pages fed the chapter cover `segments[0].text[:200]` — the first
200 chars of the first segment's RAW adult-novel prose, truncated mid-sentence.
The editor and web regen instead use the record's scene_summary (the LLM
chapter summary built at preprocess). So the cover drawn by whole-chapter
generation ≠ the cover the editor draws — the same root-cause-A duplicate
source, for special pages.

Fix: ensure_special_pages resolves content through load_special_records.
"""

from __future__ import annotations

import src.agents.artist as artist_mod
from src.agents.artist import ArtistAgent


def test_chapter_cover_uses_record_summary_not_raw_truncation(monkeypatch, tmp_path):
    monkeypatch.setattr(artist_mod, "GENERATED_DIR", tmp_path)
    # Hub characters (root cause A path).
    monkeypatch.setattr("src.routes.helpers.load_character_profiles",
                        lambda bid: [{"name": "Alice", "role": "main"}])
    # The record carries the LLM chapter summary — the single source.
    monkeypatch.setattr("src.routes.editor.load_special_records",
                        lambda bid: {
                            "book_cover": {"scene_summary": "cover", "characters_in_scene": ["Alice"]},
                            "chapter_cover:0": {"scene_summary": "LLM CHAPTER SUMMARY",
                                                "characters_in_scene": ["Alice"]},
                        })

    captured = {}
    monkeypatch.setattr(ArtistAgent, "generate_book_cover", lambda self, *a, **k: "")
    monkeypatch.setattr(ArtistAgent, "generate_chapter_cover",
                        lambda self, ch_title, ch_num, summary, profiles: captured.update(summary=summary) or "")

    agent = ArtistAgent("b1")
    segments = [{"text": "RAW ADULT NOVEL PROSE that should never reach the cover " * 10}]
    agent.ensure_special_pages({"meta": {"title": "Book"}}, 0, segments)

    assert captured.get("summary") == "LLM CHAPTER SUMMARY"
    assert "RAW ADULT NOVEL PROSE" not in (captured.get("summary") or "")
