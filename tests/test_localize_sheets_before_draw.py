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
    from src.core import storage
    from src.generation import illustration

    book_id = "b1"
    monkeypatch.setattr(storage, "GENERATED_DIR", tmp_path)
    monkeypatch.setattr(illustration, "GENERATED_DIR", tmp_path)

    # The dir-exists / llm_locations guards short-circuit BEFORE the localize on a
    # fully-cold /tmp, so seed only those two metadata prerequisites locally and
    # leave the scene IMAGE itself durable-only — exactly the case localize fixes.
    scenes_dir = tmp_path / book_id / "scenes"
    scenes_dir.mkdir(parents=True)
    locs_path = tmp_path / book_id / "preprocess" / "llm_locations.json"
    locs_path.parent.mkdir(parents=True)
    locs_path.write_text(
        json.dumps({"locations": [{"name": "Garden", "aliases": []}]}),
        encoding="utf-8",
    )

    durable = {f"{book_id}/scenes/garden_scene.png": b"SCENE-BYTES"}
    monkeypatch.setattr(storage, "get_image", lambda key: durable.get(key))

    assert not (scenes_dir / "garden_scene.png").exists()

    found = illustration._find_scene_sheet(book_id, "a walk in the Garden")

    assert found == str(scenes_dir / "garden_scene.png")
    assert (scenes_dir / "garden_scene.png").read_bytes() == b"SCENE-BYTES"
