import json
import pytest
from tests.test_store_primitives import FakeBucket


@pytest.fixture
def wired(monkeypatch, tmp_path):
    import src.core.store as store
    import src.routes.helpers as helpers
    bucket = FakeBucket()
    monkeypatch.setattr(store, "_bucket", lambda: bucket)
    monkeypatch.setattr(helpers, "GENERATED_DIR", tmp_path)
    return helpers, store, tmp_path


def test_load_json_reads_from_store(wired):
    helpers, store, _ = wired
    store.save_preprocess_file("b1", "analysis.json", {"segments": [1, 2]})
    assert helpers._load_json("b1", "analysis.json") == {"segments": [1, 2]}


def test_load_json_falls_back_to_local_file(wired):
    helpers, store, tmp = wired
    p = tmp / "b1" / "preprocess" / "meta.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"title": "Local"}), encoding="utf-8")
    assert helpers._load_json("b1", "meta.json") == {"title": "Local"}


def test_load_json_missing_returns_none(wired):
    helpers, *_ = wired
    assert helpers._load_json("b1", "nope.json") is None


def test_load_json_ignores_prefetched(wired):
    helpers, store, _ = wired
    store.save_preprocess_file("b1", "x.json", {"from": "store"})
    assert helpers._load_json("b1", "x.json", prefetched={"from": "mcp"}) == {"from": "store"}


def test_heal_if_local_fresher_removed():
    import src.routes.helpers as helpers
    assert not hasattr(helpers, "heal_if_local_fresher")


def test_save_json_writes_store_and_local(wired):
    helpers, store, tmp = wired
    helpers._save_json("b1", "meta.json", {"title": "Both"})
    assert store.load_preprocess_file("b1", "meta.json") == {"title": "Both"}
    local = tmp / "b1" / "preprocess" / "meta.json"
    assert json.loads(local.read_text(encoding="utf-8")) == {"title": "Both"}


def test_load_characters_from_store(wired):
    helpers, store, _ = wired
    store.save_characters("b1", [{"canonical_name": "Jay"}])
    assert helpers.load_characters("b1")[0]["canonical_name"] == "Jay"


def test_load_characters_file_fallback(wired):
    helpers, store, _ = wired
    # No characters.json -> store.get_characters returns []; fall back to the
    # llm_characters.json preprocess file (also served through the store here).
    store.save_preprocess_file("b1", "llm_characters.json",
                               {"characters": [{"canonical_name": "Nick"}]})
    assert helpers.load_characters("b1")[0]["canonical_name"] == "Nick"
