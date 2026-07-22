import pytest
from tests.test_store_primitives import FakeBucket


def test_record_image_version_registers_in_store(monkeypatch, tmp_path):
    import src.core.store as store
    import src.core.storage as storage
    bucket = FakeBucket()
    monkeypatch.setattr(store, "_bucket", lambda: bucket)
    # Force storage.put_image down its LOCAL path (no real GCS in the test).
    monkeypatch.setattr(storage, "GCS_BUCKET", "")
    monkeypatch.setattr(storage, "GENERATED_DIR", tmp_path)

    url = storage.record_image_version("b1", "page", "ch0:seg1",
                                       b"\x89PNG\r\n\x1a\n", content_type="image/png")
    assert url.startswith("/static/")
    sel = store.get_selected_version("b1", "page", "ch0:seg1")
    assert sel is not None
    assert sel["storage_key"].startswith("b1/pages/")
    assert sel["url"] == url
