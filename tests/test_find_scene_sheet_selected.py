import src.core.store as store
import src.generation.illustration as illus
from pathlib import Path


def test_find_scene_sheet_uses_selected(monkeypatch, tmp_path):
    monkeypatch.setattr("src.generation.illustration.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.store.load_preprocess_file",
                        lambda book_id, fn: {"locations": [{"name": "The Garden", "aliases": []}]})
    b = "scb"
    vkey = f"{b}/scenes/The_Garden_9f9f9f9f9f9f.png"
    (tmp_path / vkey).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / vkey).write_bytes(b"SELECTED-SCENE")
    store.add_asset_version(b, "scene", "The Garden", "url", image_hash="9f9f9f9f9f9f", storage_key=vkey)
    p = illus._find_scene_sheet(b, "a page set in The Garden at dusk")
    assert Path(p).read_bytes() == b"SELECTED-SCENE"
