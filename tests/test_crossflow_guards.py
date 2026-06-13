"""Cross-flow mutual exclusion (round-2 findings).

- Renaming a character/scene while its sheet regen is in flight stranded the
  sheet in history under the old name → the asset ended up with NO current
  sheet. Renames now 409 while the asset (or a chapter run) is busy.
- Segment regen during a chapter-generation subprocess (and vice versa) raced
  two writers on the same page files / chapter_data.json. Both directions 409.
- restore-version during a chapter run was overridden by the subprocess's
  final chapter_data rebuild. 409.
"""

from __future__ import annotations

import json

import pytest

from src.routes.helpers import _active_generations, _active_regens


@pytest.fixture(autouse=True)
def clean_claims():
    _active_regens.clear()
    _active_generations.clear()
    yield
    _active_regens.clear()
    _active_generations.clear()


@pytest.fixture()
def book(monkeypatch, tmp_path):
    """A minimal on-disk book; all helper modules pointed at tmp."""
    for mod in ("src.routes.helpers", "src.routes.editor", "src.routes.generation"):
        monkeypatch.setattr(f"{mod}.GENERATED_DIR", tmp_path)
    # Mongo must not answer — force the file path.
    monkeypatch.setattr("src.core.db.is_available", lambda: False, raising=False)
    pre = tmp_path / "b1" / "preprocess"
    pre.mkdir(parents=True)
    (pre / "llm_characters.json").write_text(json.dumps({
        "characters": [{"canonical_name": "Alice", "aliases": []}],
    }))
    (pre / "llm_locations.json").write_text(json.dumps({
        "locations": [{"name": "Forest", "aliases": []}],
    }))
    (pre / "analysis.json").write_text(json.dumps({
        "segments": [{"id": 0, "chapter_idx": 0, "text": "x " * 20}],
    }))
    return "b1"


def test_character_rename_409_while_sheet_regenerating(client, book):
    _active_regens.add((book, "character", "Alice"))
    r = client.put(f"/api/book/{book}/preprocess/characters/Alice",
                   json={"canonical_name": "Alicia"})
    assert r.status_code == 409


def test_character_rename_409_while_chapter_generating(client, book):
    _active_generations.add((book, 0))
    r = client.put(f"/api/book/{book}/preprocess/characters/Alice",
                   json={"canonical_name": "Alicia"})
    assert r.status_code == 409


def test_character_non_rename_update_allowed_during_regen(client, book):
    _active_regens.add((book, "character", "Alice"))
    r = client.put(f"/api/book/{book}/preprocess/characters/Alice",
                   json={"description": "kind and brave"})
    assert r.status_code == 200


def test_scene_rename_409_while_scene_regenerating(client, book):
    _active_regens.add((book, "scene", "Forest"))
    r = client.put(f"/api/book/{book}/preprocess/scenes/Forest",
                   json={"name": "Dark Forest"})
    assert r.status_code == 409


def test_segment_regen_409_while_chapter_generating(client, book):
    _active_generations.add((book, 0))
    r = client.post(f"/api/book/{book}/segment/0/regenerate")
    assert r.status_code == 409


def test_chapter_generate_409_while_regen_active(client, book):
    _active_regens.add((book, "segment", 0))
    r = client.post(f"/api/book/{book}/chapter/0/generate")
    assert r.status_code == 409


def test_restore_version_409_while_chapter_generating(client, book):
    _active_generations.add((book, 0))
    r = client.post(f"/api/book/{book}/segment/0/restore-version?version=123")
    assert r.status_code == 409


def test_regen_active_endpoint(client, book):
    _active_regens.add((book, "character", "Alice"))
    _active_regens.add((book, "segment", 3))

    def fetch(kind, key):
        return client.get(f"/api/book/{book}/regen-active",
                          params={"kind": kind, "key": key}).json()

    assert fetch("character", "Alice") == {"active": True, "error": None}
    assert fetch("segment", "3") == {"active": True, "error": None}
    assert fetch("scene", "Forest") == {"active": False, "error": None}


def test_regen_active_reports_last_failure(client, book):
    from src.routes.helpers import _last_regen_errors
    claim = (book, "scene", "Forest")
    _last_regen_errors[claim] = "quota exhausted"
    try:
        r = client.get(f"/api/book/{book}/regen-active",
                       params={"kind": "scene", "key": "Forest"}).json()
        assert r == {"active": False, "error": "quota exhausted"}
    finally:
        _last_regen_errors.clear()
