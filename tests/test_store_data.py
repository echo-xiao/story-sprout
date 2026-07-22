from tests.test_store_primitives import FakeBucket  # reuse the in-memory fake
import pytest


@pytest.fixture
def store(monkeypatch):
    bucket = FakeBucket()
    import src.core.store as store
    monkeypatch.setattr(store, "_bucket", lambda: bucket)

    def list_prefix(suffix=""):
        return [k for k in bucket._store if k.endswith(suffix)]

    monkeypatch.setattr(store, "_list_keys", list_prefix, raising=False)
    return store


def test_book_roundtrip(store):
    store.save_book("b1", "Gatsby", 3, alias_map={"J": "Jay"})
    doc = store.get_book("b1")
    assert doc["title"] == "Gatsby" and doc["num_chapters"] == 3
    assert doc["alias_map"] == {"J": "Jay"}


def test_characters_and_update(store):
    store.save_characters("b1", [{"canonical_name": "Jay", "appearance": "suit"}])
    assert store.get_characters("b1")[0]["canonical_name"] == "Jay"
    assert store.update_character("b1", "Jay", {"appearance": "pink suit"}) is True
    assert store.get_characters("b1")[0]["appearance"] == "pink suit"
    assert store.update_character("b1", "Nobody", {"x": 1}) is False


def test_get_characters_empty_is_list(store):
    assert store.get_characters("missing") == []


def test_chapter_roundtrip(store):
    store.save_chapter("b1", 0, {"title": "Ch1", "pages": [{"page": 1}]})
    assert store.get_chapter("b1", 0)["pages"] == [{"page": 1}]


def test_preprocess_file_roundtrip(store):
    store.save_preprocess_file("b1", "analysis.json", {"segments": []})
    assert store.load_preprocess_file("b1", "analysis.json") == {"segments": []}


def test_list_books(store):
    store.save_book("b1", "A", 1)
    store.save_book("b2", "B", 2)
    titles = sorted(b["title"] for b in store.list_books())
    assert titles == ["A", "B"]
