"""Deferred text sync: subprocess payload + parent merge (cross-process lock fix).

The chapter subprocess used to write analysis.json directly at the end of a
run; the editor's per-book asyncio lock lives in the parent process, so a
segment edit saved in that window was clobbered (GCS fuse offers no
cross-process file lock). The web flow now passes --defer-text-sync: the
subprocess leaves a text_sync.json payload and ONLY the parent writes
analysis.json, under the same lock as every editor write.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from tests.conftest import make_segment


def test_payload_writer_dumps_pages_with_text(tmp_path):
    from src.agents.adk_pipeline import IllustrateQAStage, TEXT_SYNC_FILENAME

    ctx = SimpleNamespace(chapter_dir=tmp_path, simplified=[
        {"source_segment_id": 0, "page_text": "LLM TEXT", "scene_direction": "a desk"},
        {"source_segment_id": 1, "page_text": "", "scene_direction": ""},  # no text → excluded
    ])
    IllustrateQAStage._write_text_sync_payload(ctx)

    payload = json.loads((tmp_path / TEXT_SYNC_FILENAME).read_text())
    assert payload == [
        {"segment_id": 0, "simplified_text": "LLM TEXT", "scene_direction": "a desk",
         "text_source": "writer"},
    ]


@pytest.fixture()
def sync_env(monkeypatch, tmp_path):
    """File-backed analysis store + a payload file for _apply_deferred_text_sync."""
    store_path = tmp_path / "analysis.json"
    store_path.write_text(json.dumps({"segments": [
        make_segment(0),                                   # no text yet
        make_segment(1, simplified_text="USER TYPED THIS",  # user owns it
                     text_source="user"),
        make_segment(2, simplified_text="Robotic. Robotic. Robotic.",  # preprocess → replaceable
                     text_source="preprocess"),
    ]}))

    def load(book_id, filename):
        return json.loads(store_path.read_text()) if filename == "analysis.json" else {}

    def save(book_id, filename, data):
        store_path.write_text(json.dumps(data))

    monkeypatch.setattr("src.routes.generation._load_json", load)
    monkeypatch.setattr("src.routes.generation._save_json", save)
    monkeypatch.setattr("src.routes.generation.GENERATED_DIR", tmp_path)
    # _apply_deferred_text_sync also writes chapter_data via helpers — without
    # this the user-won writeback lands in the REAL data/generated dir.
    monkeypatch.setattr("src.routes.helpers.GENERATED_DIR", tmp_path)

    ch = tmp_path / "somebook" / "chapters" / "ch00"
    ch.mkdir(parents=True)
    payload_path = ch / "text_sync.json"
    payload_path.write_text(json.dumps([
        {"segment_id": 0, "simplified_text": "PIPELINE TEXT 0", "scene_direction": "dir 0",
         "text_source": "writer"},
        {"segment_id": 1, "simplified_text": "PIPELINE TEXT 1", "scene_direction": "dir 1",
         "text_source": "writer"},
        {"segment_id": 2, "simplified_text": "Natural Writer rewrite.", "scene_direction": "dir 2",
         "text_source": "writer"},
        {"segment_id": 99, "simplified_text": "ORPHAN", "scene_direction": ""},
    ]))
    return {"store": store_path, "payload": payload_path}


def _apply():
    from src.routes.generation import _apply_deferred_text_sync
    asyncio.run(_apply_deferred_text_sync("somebook", 0))


def test_parent_merge_fills_empty_and_keeps_user_text(sync_env):
    _apply()
    segs = json.loads(sync_env["store"].read_text())["segments"]
    # Empty page filled, and tagged as Writer-produced.
    assert segs[0]["simplified_text"] == "PIPELINE TEXT 0"
    assert segs[0]["scene_direction"] == "dir 0"
    assert segs[0]["text_source"] == "writer"
    # The user's text — typed before or during the run — must win.
    assert segs[1]["simplified_text"] == "USER TYPED THIS"
    assert segs[1]["text_source"] == "user"
    # The robotic preprocess text MUST be replaced (the bug: it used to freeze
    # because "non-empty" was mistaken for a user edit).
    assert segs[2]["simplified_text"] == "Natural Writer rewrite."
    assert segs[2]["text_source"] == "writer"
    assert not sync_env["payload"].exists(), "payload must be consumed"


def test_apply_is_noop_without_payload(sync_env):
    sync_env["payload"].unlink()
    _apply()  # must not raise
    segs = json.loads(sync_env["store"].read_text())["segments"]
    assert segs[0].get("simplified_text", "") == ""


def test_corrupt_payload_is_discarded_not_fatal(sync_env):
    sync_env["payload"].write_text("{not json")
    _apply()  # must not raise
    assert not sync_env["payload"].exists()
    segs = json.loads(sync_env["store"].read_text())["segments"]
    assert segs[0].get("simplified_text", "") == ""
