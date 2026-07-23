import src.core.store as store
import src.routes.generation as gen
from pathlib import Path


def test_sheets_for_uses_selected_version(monkeypatch, tmp_path):
    monkeypatch.setattr("src.routes.generation.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    b = "shb"
    chars = tmp_path / b / "characters"; chars.mkdir(parents=True)
    (chars / f"{gen._safe_filename('Swallow')}_sheet.png").write_bytes(b"OLD-CURRENT")
    vkey = f"{b}/characters/Swallow_abc123abc123.png"
    (tmp_path / vkey).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / vkey).write_bytes(b"SELECTED")
    store.add_asset_version(b, "character", "Swallow", "url", image_hash="abc123abc123", storage_key=vkey)
    out = gen._sheets_for(b, ["Swallow"])
    assert Path(out[0]["sheet_path"]).read_bytes() == b"SELECTED"
