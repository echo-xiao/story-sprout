"""load_characters reads the consistency hub first (src/routes/helpers.py).

The editor AND every generation path (sheet regen, segment QA, special pages)
now resolve character profiles through load_characters, so they share one
canonical source: the `characters` collection (the MCP consistency hub), with
llm_characters.json as fallback only. Without this, generation read the file
directly while the editor read the collection — a rename/appearance edit that
reached only one store left generation drawing the stale look.
"""

from __future__ import annotations

import src.routes.helpers as helpers


def test_hub_collection_wins_over_file(monkeypatch):
    monkeypatch.setattr("src.core.db.get_characters",
                        lambda bid: [{"canonical_name": "Alice", "appearance": "from hub"}])
    # File must NOT be consulted when the hub answers.
    monkeypatch.setattr(helpers, "_load_json",
                        lambda *a, **k: {"characters": [{"canonical_name": "Alice", "appearance": "stale file"}]})
    chars = helpers.load_characters("b")
    assert chars == [{"canonical_name": "Alice", "appearance": "from hub"}]


def test_falls_back_to_file_when_hub_empty(monkeypatch):
    monkeypatch.setattr("src.core.db.get_characters", lambda bid: [])
    monkeypatch.setattr(helpers, "_load_json",
                        lambda *a, **k: {"characters": [{"canonical_name": "Bob", "appearance": "filed"}]})
    chars = helpers.load_characters("b")
    assert chars == [{"canonical_name": "Bob", "appearance": "filed"}]


def test_falls_back_to_file_when_hub_unavailable(monkeypatch):
    def boom(_):
        raise RuntimeError("mongo down")
    monkeypatch.setattr("src.core.db.get_characters", boom)
    monkeypatch.setattr(helpers, "_load_json", lambda *a, **k: {"characters": [{"canonical_name": "Cara"}]})
    assert helpers.load_characters("b") == [{"canonical_name": "Cara"}]


def test_empty_everywhere_returns_empty_list(monkeypatch):
    monkeypatch.setattr("src.core.db.get_characters", lambda bid: [])
    monkeypatch.setattr(helpers, "_load_json", lambda *a, **k: None)
    assert helpers.load_characters("b") == []
