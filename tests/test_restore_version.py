"""restore-version file-shuffle hardening (routes/editor.py).

Low-risk review findings: the endpoint used to rename the current image into
history BEFORE copying the restored version back — a failed copy (disk full)
left the page with no image at all; and restoring a version whose timestamp
collided with "now" overwrote the very history file being restored.
"""

from __future__ import annotations

import json
import time

import pytest

from tests.conftest import make_segment


@pytest.fixture()
def book(monkeypatch, tmp_path):
    """A one-segment book with a current page image and one history version."""
    analysis = {"segments": [make_segment(0)]}
    monkeypatch.setattr(
        "src.routes.editor._load_json",
        lambda book_id, filename: analysis if filename == "analysis.json" else {},
    )
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)
    # update_chapter_data_page (helpers) now UPSERTS — without this patch
    # it bootstraps chapter_data.json under the REAL data/generated dir.
    monkeypatch.setattr("src.routes.helpers.GENERATED_DIR", tmp_path)

    ch_base = tmp_path / "somebook" / "chapters" / "ch00"
    pages = ch_base / "pages"
    history = ch_base / "history"
    quality = ch_base / "quality"
    for d in (pages, history, quality):
        d.mkdir(parents=True)

    (pages / "page_001.png").write_bytes(b"CURRENT")
    (quality / "page_001_quality.json").write_text(json.dumps({"overall_score": 50}))
    (history / "page_001_1000.jpg").write_bytes(b"OLD-VERSION")
    (history / "page_001_1000_quality.json").write_text(json.dumps({"overall_score": 90}))
    return ch_base


def test_restore_swaps_current_and_archives_old(client, book):
    resp = client.post("/api/book/somebook/segment/0/restore-version?version=1000")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "restored"
    # URL now carries a ?v=<mtime> cache-buster — compare the path only.
    assert body["illustration_url"].split("?")[0].endswith("page_001.jpg")

    pages, history, quality = book / "pages", book / "history", book / "quality"
    # Restored version became current (extension follows the restored file).
    assert (pages / "page_001.jpg").read_bytes() == b"OLD-VERSION"
    assert not (pages / "page_001.png").exists()
    # The restored source stays listed in history (copy, not move).
    assert (history / "page_001_1000.jpg").read_bytes() == b"OLD-VERSION"
    # The previous current image + quality were archived, not lost.
    archived = [f for f in history.glob("page_001_*.png")]
    assert len(archived) == 1 and archived[0].read_bytes() == b"CURRENT"
    # The restored version's quality verdict came back with it.
    assert json.loads((quality / "page_001_quality.json").read_text())["overall_score"] == 90
    # No temp file left behind.
    assert not list(pages.glob(".restore_tmp_*"))


def test_failed_copy_keeps_current_image(client, book, monkeypatch):
    def boom(src, dst, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("shutil.copy2", boom)
    resp = client.post("/api/book/somebook/segment/0/restore-version?version=1000")
    assert resp.status_code == 500
    # The current image must be untouched — the old code had already renamed
    # it into history at this point, leaving the page with no image.
    assert (book / "pages" / "page_001.png").read_bytes() == b"CURRENT"


def test_same_second_restore_does_not_clobber_history_source(client, book, monkeypatch):
    """Restoring a version stamped with the current epoch second must not
    archive the current image over the file being restored."""
    frozen = 1_000_000
    monkeypatch.setattr(time, "time", lambda: float(frozen))
    history = book / "history"
    src = history / f"page_001_{frozen}.png"
    src.write_bytes(b"COLLIDING-VERSION")

    resp = client.post(f"/api/book/somebook/segment/0/restore-version?version={frozen}")
    assert resp.status_code == 200
    # The restored content made it to current, and the source survived.
    assert (book / "pages" / "page_001.png").read_bytes() == b"COLLIDING-VERSION"
    assert src.read_bytes() == b"COLLIDING-VERSION"
    # The old current was archived under a bumped, non-colliding timestamp.
    archived = [f for f in history.glob("page_001_*.png")
                if f != src and f.read_bytes() == b"CURRENT"]
    assert len(archived) == 1
