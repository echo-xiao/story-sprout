"""Serverless /tmp regression: dependency sheets must be pulled from durable
storage (GCS) BEFORE the generation path reads them as local paths.

On a cold serverless invocation `/tmp` (= GENERATED_DIR) is empty, but the
durable copy of every character/scene sheet lives in GCS. The sheet-lookup
helpers resolve `<GENERATED_DIR>/<key>` and `.exists()`-probe it, so without a
`storage.localize(key)` immediately before that probe the sheet is invisible
and generation silently draws with no reference.

This locks in that `_sheets_for` (character sheets, feeds special-page drawing)
and `_find_scene_sheet` (scene background sheet, feeds story + special pages)
materialize the durable object to local disk before reading it. The seam: an
EMPTY tmp GENERATED_DIR (cold /tmp) + a stubbed `storage.get_image` standing in
for the GCS object. If the localize call were removed, both helpers would find
nothing on the empty disk and return empty — so these assertions actually bite.
"""

from __future__ import annotations

import json


def test_sheets_for_localizes_missing_character_sheet(monkeypatch, tmp_path):
    from src.core import storage
    from src.routes import generation

    book_id = "b1"
    # Cold /tmp: local disk empty; the sheet exists ONLY in durable storage.
    monkeypatch.setattr(storage, "GENERATED_DIR", tmp_path)
    monkeypatch.setattr(generation, "GENERATED_DIR", tmp_path)

    # "alice" == _safe_filename("Alice"); the durable object under its GCS key.
    durable = {f"{book_id}/characters/alice_sheet.png": b"SHEET-BYTES"}
    monkeypatch.setattr(storage, "get_image", lambda key: durable.get(key))

    # Nothing is on local disk yet.
    assert not (tmp_path / book_id / "characters" / "alice_sheet.png").exists()

    out = generation._sheets_for(book_id, ["Alice"])

    # localize pulled it down: the entry resolves AND the file now exists locally.
    assert out == [{
        "character_name": "Alice",
        "sheet_path": str(tmp_path / book_id / "characters" / "alice_sheet.png"),
    }]
    assert (tmp_path / book_id / "characters" / "alice_sheet.png").read_bytes() == b"SHEET-BYTES"


def test_find_scene_sheet_localizes_missing_scene_sheet(monkeypatch, tmp_path):
    from src.core import storage, store
    from src.generation import illustration

    book_id = "b1"
    monkeypatch.setattr(storage, "GENERATED_DIR", tmp_path)
    monkeypatch.setattr(illustration, "GENERATED_DIR", tmp_path)

    # Seed location metadata into the durable store (GCS-backed) so that the
    # cold-/tmp fix — reading from store instead of a local file — is exercised.
    # Leave the scene IMAGE itself durable-only (storage.get_image) to verify
    # that storage.localize actually pulls it down on a cold /tmp.
    store.save_preprocess_file(book_id, "llm_locations.json", {"locations": [{"name": "Garden", "aliases": []}]})

    durable = {f"{book_id}/scenes/garden_scene.png": b"SCENE-BYTES"}
    monkeypatch.setattr(storage, "get_image", lambda key: durable.get(key))

    scenes_dir = tmp_path / book_id / "scenes"
    assert not scenes_dir.exists() or not (scenes_dir / "garden_scene.png").exists()

    found = illustration._find_scene_sheet(book_id, "a walk in the Garden")

    assert found == str(scenes_dir / "garden_scene.png")
    assert (scenes_dir / "garden_scene.png").read_bytes() == b"SCENE-BYTES"
