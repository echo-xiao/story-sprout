import src.core.store as store
import src.core.storage as storage
from pathlib import Path


def test_returns_localized_selected_version(monkeypatch, tmp_path):
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    b = "selimg"
    key = f"{b}/characters/X_deadbeef1234.png"
    (tmp_path / key).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / key).write_bytes(b"\x89PNG\r\n\x1a\nSEL")
    store.add_asset_version(b, "character", "X", "url", image_hash="deadbeef1234", storage_key=key)
    path = storage.selected_version_image(b, "character", "X")
    assert path and path.endswith("X_deadbeef1234.png")
    assert Path(path).read_bytes() == b"\x89PNG\r\n\x1a\nSEL"


def test_none_when_no_version():
    assert storage.selected_version_image("nobook", "character", "Nobody") is None
