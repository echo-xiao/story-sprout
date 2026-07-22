import asyncio


def test_delete_book_deletes_prefix_and_reports_existed(monkeypatch, tmp_path):
    import src.core.pipeline as pipeline
    import src.core.storage as storage
    import src.core.store as store
    calls = {}
    monkeypatch.setattr(storage, "delete_prefix", lambda prefix: calls.setdefault("prefix", prefix))
    monkeypatch.setattr(store, "get_book", lambda bid: None)
    monkeypatch.setattr(pipeline, "GENERATED_DIR", tmp_path)
    (tmp_path / "b1").mkdir()

    result = asyncio.run(pipeline.delete_book("b1"))
    assert result is True  # local dir existed
    assert calls["prefix"] == "b1/"


def test_delete_book_unknown_returns_false(monkeypatch, tmp_path):
    import src.core.pipeline as pipeline
    import src.core.storage as storage
    import src.core.store as store
    monkeypatch.setattr(storage, "delete_prefix", lambda prefix: None)
    monkeypatch.setattr(store, "get_book", lambda bid: None)
    monkeypatch.setattr(pipeline, "GENERATED_DIR", tmp_path)

    result = asyncio.run(pipeline.delete_book("nope"))
    assert result is False
