"""Sheet history endpoints must tolerate non-numeric history files (editor.py).

Medium-risk review finding: the sheet self-correction writes
*_selfcorrect_prev.png backups into the same history directory; the history
endpoints did float("prev") on them and 500'd permanently for any character
whose sheet ever self-corrected.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def book_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)
    chars = tmp_path / "somebook" / "characters" / "history"
    scenes = tmp_path / "somebook" / "scenes" / "history"
    chars.mkdir(parents=True)
    scenes.mkdir(parents=True)
    return tmp_path / "somebook"


def test_character_history_skips_selfcorrect_backup(client, book_dirs):
    chars = book_dirs / "characters"
    (chars / "someone_sheet.png").write_bytes(b"x")
    (chars / "history" / "someone_sheet_1000.png").write_bytes(b"x")
    (chars / "history" / "someone_sheet_selfcorrect_prev.png").write_bytes(b"x")

    resp = client.get("/api/book/somebook/preprocess/characters/someone/history")
    assert resp.status_code == 200
    versions = [img["version"] for img in resp.json()["images"]]
    assert versions == ["current", "1000"]


def test_scene_history_skips_non_numeric_versions(client, book_dirs):
    scenes = book_dirs / "scenes"
    (scenes / "west_egg_scene.png").write_bytes(b"x")
    (scenes / "history" / "west_egg_scene_1000.png").write_bytes(b"x")
    (scenes / "history" / "west_egg_scene_selfcorrect_prev.png").write_bytes(b"x")

    resp = client.get("/api/book/somebook/preprocess/scenes/west%20egg/history")
    assert resp.status_code == 200
    versions = [img["version"] for img in resp.json()["images"]]
    assert versions == ["current", "1000"]
