from tests.test_store_primitives import FakeBucket
import pytest


@pytest.fixture
def store(monkeypatch):
    bucket = FakeBucket()
    import src.core.store as store
    monkeypatch.setattr(store, "_bucket", lambda: bucket)
    return store


def test_add_selects_latest(store):
    v1 = store.add_asset_version("b", "page", "ch0:seg1", "url1", image_hash="h1", storage_key="k1")
    v2 = store.add_asset_version("b", "page", "ch0:seg1", "url2", image_hash="h2", storage_key="k2")
    assert v1 != v2
    assert store.get_selected_version("b", "page", "ch0:seg1")["url"] == "url2"


def test_dedupe_by_hash_reselects(store):
    v1 = store.add_asset_version("b", "page", "ch0:seg1", "url1", image_hash="h1")
    store.add_asset_version("b", "page", "ch0:seg1", "url2", image_hash="h2")
    again = store.add_asset_version("b", "page", "ch0:seg1", "url1b", image_hash="h1")
    assert again == v1  # same hash -> reuse the existing version id
    assert store.get_selected_version("b", "page", "ch0:seg1")["id"] == v1
    assert len(store.list_asset_versions("b", "page", "ch0:seg1")["versions"]) == 2


def test_set_selected_and_missing(store):
    v1 = store.add_asset_version("b", "page", "ch0:seg1", "url1", image_hash="h1")
    store.add_asset_version("b", "page", "ch0:seg1", "url2", image_hash="h2")
    assert store.set_selected_version("b", "page", "ch0:seg1", v1) is True
    assert store.get_selected_version("b", "page", "ch0:seg1")["id"] == v1
    assert store.set_selected_version("b", "page", "ch0:seg1", "nope") is False


def test_cap_keeps_last_12(store):
    for i in range(15):
        store.add_asset_version("b", "page", "k", f"u{i}", image_hash=f"h{i}")
    vs = store.list_asset_versions("b", "page", "k")["versions"]
    assert len(vs) == 12
    assert vs[-1]["url"] == "u14"


def test_delete_asset_versions(store):
    store.add_asset_version("b", "page", "k", "u", image_hash="h")
    store.delete_asset_versions("b")
    assert store.list_asset_versions("b", "page", "k") == {"versions": [], "selected_version_id": None}
