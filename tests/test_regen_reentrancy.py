"""In-flight regen claims (routes/generation.py, books.py, editor.py).

Medium-risk review finding: none of the single-asset regen endpoints (or the
preprocess kickoff) refused a second concurrent run — two clicks meant two
Gemini generations racing on the same files. restore-version could also
interleave with a running regen and leave both a .png and .jpg current image.
"""

from __future__ import annotations

import asyncio

import pytest

from src.routes.helpers import _active_regens


@pytest.fixture(autouse=True)
def clean_claims():
    _active_regens.clear()
    yield
    _active_regens.clear()


@pytest.mark.parametrize("claim,path", [
    (("somebook", "segment", 0), "/api/book/somebook/segment/0/regenerate"),
    (("somebook", "character", "Jay Gatsby"), "/api/book/somebook/characters/Jay%20Gatsby/regenerate"),
    (("somebook", "scene", "West Egg"), "/api/book/somebook/scenes/West%20Egg/regenerate"),
    (("somebook", "special", "book_cover:0"), "/api/book/somebook/special/book_cover/regenerate"),
])
def test_second_regen_is_refused(client, claim, path):
    _active_regens.add(claim)
    resp = client.post(path)
    assert resp.status_code == 409
    assert "regenerating" in resp.json()["detail"]


def test_restore_version_refused_while_regen_in_flight(client, monkeypatch, tmp_path):
    from tests.conftest import make_segment
    analysis = {"segments": [make_segment(0)]}
    monkeypatch.setattr(
        "src.routes.editor._load_json",
        lambda book_id, filename: analysis if filename == "analysis.json" else {},
    )
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)
    _active_regens.add(("somebook", "segment", 0))

    resp = client.post("/api/book/somebook/segment/0/restore-version?version=1000")
    assert resp.status_code == 409


def test_second_preprocess_kickoff_is_refused(client):
    from src.routes import books
    books._active_preprocesses.add("somebook")
    try:
        resp = client.post("/api/generate", json={"source_text": "somebook\nhello world"})
        assert resp.status_code == 409
        assert "already preprocessing" in resp.json()["detail"]
    finally:
        books._active_preprocesses.discard("somebook")


def test_preprocess_claim_released_even_on_crash(monkeypatch, tmp_path):
    """_run_preprocess must release the claim in its finally — including the
    spawn-crash path."""
    monkeypatch.setattr("src.routes.books.GENERATED_DIR", tmp_path)

    async def boom(*args, **kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr("asyncio.create_subprocess_exec", boom)

    from src.routes import books
    books._active_preprocesses.add("somebook")
    asyncio.run(books._run_preprocess("somebook", tmp_path / "in.txt"))
    assert "somebook" not in books._active_preprocesses
