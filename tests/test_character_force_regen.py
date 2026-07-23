"""Character regen must FORCE regeneration, not reuse a stale /tmp image.

Root cause (serverless): `_generate_portrait` / `generate_character_sheets` skip
generation when the image file already exists locally (character_sheet.py:182,
294). On Vercel each instance's /tmp starts empty, but the display/read paths
call `storage.localize` which re-materializes the CURRENT image from GCS into
/tmp — so a user-initiated "regenerate" finds the file "exists" and reuses the
stale image. Result: no new image, QA scores the same image, record_image_version
dedups identical bytes -> no new version (the "no history" bug).

The page path already solves this with a force-regen flag (illustration.py:332,
`PBG_FORCE_REGEN`). This threads an explicit `force` param through the character
path so a regen always generates fresh, robust to the localize race.

TDD: RED before the `force` param exists, GREEN after.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import src.generation.character_sheet as cs


class _FakeModels:
    def __init__(self) -> None:
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        return object()  # sentinel — save_inline_image is stubbed


class _FakeClient:
    def __init__(self) -> None:
        self.models = _FakeModels()


def _profile() -> dict:
    return {"name": "Swallow", "gender": "female",
            "appearance_description": ["a small bird"]}


@pytest.fixture()
def cs_env(monkeypatch):
    """Stub save_inline_image (writes a fresh marker) + cover style-ref."""
    def _fake_save(response, save_path: Path) -> str:
        final = save_path.with_suffix(".png")
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(b"\x89PNG\r\n\x1a\nNEW")
        return str(final)

    monkeypatch.setattr(cs, "save_inline_image", _fake_save)
    monkeypatch.setattr("src.generation.special_pages.get_style_ref",
                        lambda book_id: None)


def test_portrait_reuses_existing_when_not_forced(cs_env, tmp_path):
    """Default behaviour: an existing local portrait is reused (the batch-resume
    optimisation). Documents the skip so the force case is a clear contrast."""
    out = tmp_path / "characters"
    out.mkdir(parents=True)
    safe = cs._safe_filename("Swallow")
    (out / f"{safe}_portrait.png").write_bytes(b"OLD")

    client = _FakeClient()
    cs._generate_portrait(client, _profile(), out, "storybook", force=False)

    assert client.models.calls == 0, "unforced should reuse the existing file"
    assert (out / f"{safe}_portrait.png").read_bytes() == b"OLD"


def test_portrait_force_regenerates_over_stale_local_file(cs_env, tmp_path):
    """THE FIX: force must regenerate even when a stale local file exists
    (as localize leaves one on a warm serverless instance)."""
    out = tmp_path / "characters"
    out.mkdir(parents=True)
    safe = cs._safe_filename("Swallow")
    (out / f"{safe}_portrait.png").write_bytes(b"OLD")

    client = _FakeClient()
    cs._generate_portrait(client, _profile(), out, "storybook", force=True)

    assert client.models.calls == 1, "force must call the model, not skip"
    assert (out / f"{safe}_portrait.png").read_bytes() != b"OLD", "must overwrite stale image"


def test_generate_character_sheets_force_regenerates_sheet(cs_env, tmp_path, monkeypatch):
    """End-to-end: force regenerates BOTH portrait and sheet even when both
    already exist locally, and threads force down to the portrait step."""
    monkeypatch.setattr(cs, "GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.record_image_version", lambda *a, **k: "url")
    client = _FakeClient()
    monkeypatch.setattr(cs, "_get_client", lambda: client)

    out = tmp_path / "bookX" / "characters"
    out.mkdir(parents=True)
    safe = cs._safe_filename("Swallow")
    (out / f"{safe}_portrait.png").write_bytes(b"OLDPORTRAIT")
    (out / f"{safe}_sheet.png").write_bytes(b"OLDSHEET")

    cs.generate_character_sheets([_profile()], "bookX", force=True)

    # portrait + sheet both regenerated => >=2 model calls, both files overwritten
    assert client.models.calls >= 2, "force must regenerate portrait AND sheet"
    assert (out / f"{safe}_sheet.png").read_bytes() != b"OLDSHEET"
    assert (out / f"{safe}_portrait.png").read_bytes() != b"OLDPORTRAIT"


def test_regen_endpoint_threads_force_true(monkeypatch, tmp_path):
    """The regenerate_character_sheet route must call generate_character_sheets
    with force=True — otherwise the localize race reuses the stale sheet."""
    import src.routes.generation as gen_mod
    from fastapi import BackgroundTasks
    from src.routes.helpers import _active_regens

    monkeypatch.setattr("src.routes.generation.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GCS_BUCKET", "")

    book_id, char = "bookF", "Swallow"
    chars_dir = tmp_path / book_id / "characters"
    chars_dir.mkdir(parents=True)
    characters = [{"canonical_name": char, "role": "supporting",
                   "gender": "female", "appearance": "a bird", "description": "",
                   "visual_details": {}}]
    monkeypatch.setattr("src.routes.helpers.load_characters", lambda b: characters)

    captured: dict = {}

    def _fake_gcs(profiles, book_id, **kwargs):
        captured.update(kwargs)
        (chars_dir / f"{cs._safe_filename(char)}_sheet.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        return [{"sheet_path": str(chars_dir / f"{cs._safe_filename(char)}_sheet.png")}]

    monkeypatch.setattr("src.generation.character_sheet.generate_character_sheets", _fake_gcs)
    monkeypatch.setattr("src.routes.generation._run_character_sheet_quality", lambda *a, **k: None)
    import src.gemini_backend as gb
    for fn in ("set_user_api_key", "reset_user_api_key", "set_gen_error_box",
               "reset_gen_error_box"):
        monkeypatch.setattr(gb, fn, lambda *a, **k: None)
    monkeypatch.setattr(gb, "friendly_gen_error", lambda box: "")

    _active_regens.discard((book_id, "character", char))
    bt = BackgroundTasks()
    asyncio.run(gen_mod.regenerate_character_sheet(
        book_id=book_id, char_name=char, background_tasks=bt, user_key=""))
    asyncio.run(bt())

    assert captured.get("force") is True, (
        f"regen must pass force=True; got kwargs {captured}")
