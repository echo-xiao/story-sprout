"""_backfill_versions must import PAGE history from the chapter-level history dir
(<book>/chapters/chNN/history/), not the pages/ subdir — otherwise unifying the
page carousel onto the version store drops an existing book's historical images
(they'd show 1 entry instead of N, and old versions couldn't be restored)."""

import src.core.store as store
import src.routes.editor as editor


def test_backfill_imports_page_history_from_chapter_level(monkeypatch, tmp_path):
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)
    b = "bfb"
    pages = tmp_path / b / "chapters" / "ch00" / "pages"
    pages.mkdir(parents=True)
    (pages / "page_001.png").write_bytes(b"CUR")  # current image
    hist = tmp_path / b / "chapters" / "ch00" / "history"
    hist.mkdir(parents=True)
    (hist / "page_001_1000.png").write_bytes(b"H1")  # two archived versions
    (hist / "page_001_2000.png").write_bytes(b"H2")

    editor._backfill_versions(b, "page", "ch00:p001")

    versions = store.list_asset_versions(b, "page", "ch00:p001")["versions"]
    assert len(versions) == 3, f"expected 2 history + 1 current; got {len(versions)}"
