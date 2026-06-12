"""MCP consistency-hub sync helper (preprocessing/pipeline.py).

Medium-risk review finding: the sheet→characters-collection sync ran BEFORE
save_preprocess, where it either matched zero docs (first run) or was wiped
by save_characters' delete+insert right after — the "consistency hub" fields
never survived. The sync now runs from main() after save_preprocess via
_sync_sheet_hub_to_mongo; these tests pin that helper's behavior.
"""

from __future__ import annotations

import json

import pytest

import src.preprocessing.pipeline as pipeline


def test_sync_reads_sheets_file_and_pushes_items(monkeypatch, tmp_path):
    captured = {}

    def fake_update(book_id, items):
        captured["book_id"] = book_id
        captured["items"] = items
        return len(items)

    monkeypatch.setattr("src.core.mcp_client.update_characters_via_mcp", fake_update)
    (tmp_path / "character_sheets.json").write_text(json.dumps([
        {"character_name": "Jay Gatsby", "sheet_path": "/x/jay_gatsby_sheet.png",
         "visual_identity": "blond hair, pink suit", "visual_colors": "pink, gold"},
        {"sheet_path": "/x/nameless.png"},  # no character_name → skipped
    ]))

    pipeline._sync_sheet_hub_to_mongo("somebook", tmp_path)

    assert captured["book_id"] == "somebook"
    assert len(captured["items"]) == 1
    name, updates = captured["items"][0]
    assert name == "Jay Gatsby"
    assert updates["sheet_path"] == "/x/jay_gatsby_sheet.png"
    assert updates["visual_identity"] == "blond hair, pink suit"


def test_sync_noop_without_sheets_file(monkeypatch, tmp_path):
    def fail(*a, **k):
        raise AssertionError("must not be called without character_sheets.json")

    monkeypatch.setattr("src.core.mcp_client.update_characters_via_mcp", fail)
    pipeline._sync_sheet_hub_to_mongo("somebook", tmp_path)  # no raise


def test_sync_swallows_mcp_errors(monkeypatch, tmp_path):
    """Best-effort: an MCP failure must never fail the preprocess run."""
    (tmp_path / "character_sheets.json").write_text(json.dumps([
        {"character_name": "Jay Gatsby", "sheet_path": "/x/s.png"},
    ]))

    def boom(*a, **k):
        raise RuntimeError("mcp server unreachable")

    monkeypatch.setattr("src.core.mcp_client.update_characters_via_mcp", boom)
    pipeline._sync_sheet_hub_to_mongo("somebook", tmp_path)  # no raise
