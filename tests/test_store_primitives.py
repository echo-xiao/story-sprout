import pytest


class FakeBlob:
    def __init__(self, store, key):
        self._store, self._key = store, key

    def exists(self):
        return self._key in self._store

    def download_as_text(self):
        return self._store[self._key]

    def upload_from_string(self, data, content_type="application/json"):
        self._store[self._key] = data


class FakeBucket:
    def __init__(self):
        self._store = {}

    def blob(self, key):
        return FakeBlob(self._store, key)


@pytest.fixture
def fake_store(monkeypatch):
    bucket = FakeBucket()
    import src.core.store as store
    monkeypatch.setattr(store, "_bucket", lambda: bucket)
    return store


def test_put_then_get_roundtrip(fake_store):
    fake_store.put_json("book1/meta.json", {"title": "Gatsby", "n": 3})
    assert fake_store.get_json("book1/meta.json") == {"title": "Gatsby", "n": 3}


def test_get_missing_returns_none(fake_store):
    assert fake_store.get_json("nope/x.json") is None


def test_put_json_is_utf8_not_ascii_escaped(fake_store):
    fake_store.put_json("b/c.json", {"name": "李雷"})
    raw = fake_store._bucket()._store["b/c.json"]
    assert "李雷" in raw
