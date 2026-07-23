import pytest
from tests.test_store_primitives import FakeBucket


def test_record_image_version_registers_in_store(monkeypatch, tmp_path):
    """record_image_version returns the version id and registers the version in the store.

    The return value is the version id (a hex string) since Task 5 changed the
    return so callers can immediately attach QA to the recorded version via
    store.set_version_quality(..., vid, qa_result).
    """
    import src.core.store as store
    import src.core.storage as storage
    bucket = FakeBucket()
    monkeypatch.setattr(store, "_bucket", lambda: bucket)
    # Force storage.put_image down its LOCAL path (no real GCS in the test).
    monkeypatch.setattr(storage, "GCS_BUCKET", "")
    monkeypatch.setattr(storage, "GENERATED_DIR", tmp_path)

    vid = storage.record_image_version("b1", "page", "ch0:seg1",
                                       b"\x89PNG\r\n\x1a\n", content_type="image/png")
    # Returns a version id (12-char hex), not a URL.
    assert isinstance(vid, str) and len(vid) == 12
    sel = store.get_selected_version("b1", "page", "ch0:seg1")
    assert sel is not None
    assert sel["storage_key"].startswith("b1/pages/")
    assert sel["url"].startswith("/static/")
    # The returned vid matches the selected version's id.
    assert sel["id"] == vid
