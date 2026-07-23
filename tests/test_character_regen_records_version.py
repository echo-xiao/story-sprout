"""Character-sheet regen must record a durable version (spec §11.2 rule 2).

After `_regen_inner` runs inside `regenerate_character_sheet`, the store must
contain a `character:<char_name>` entry in assets.json.  This mirrors the PAGE
regen path (generation.py:376-388) which already does this via
`record_image_version`.

TDD: This test is written BEFORE the implementation so it starts RED and goes
GREEN once record_image_version is called at the correct location in
_regen_inner.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import src.core.store as store
import src.core.storage as storage
from fastapi import BackgroundTasks


BOOK_ID = "testbook_char_ver"
CHAR_NAME = "Happy Prince"
SAFE_NAME = "Happy_Prince"


@pytest.fixture()
def char_regen_env(monkeypatch, tmp_path):
    """Stub out everything that touches Gemini / GCS and seed a fake sheet."""
    # Point GENERATED_DIR at tmp_path so the route uses our controlled dir.
    monkeypatch.setattr("src.routes.generation.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    # Disable real GCS so record_image_version uses local-file fallback.
    monkeypatch.setattr("src.core.storage.GCS_BUCKET", "")

    # Seed the characters directory.
    chars_dir = tmp_path / BOOK_ID / "characters"
    chars_dir.mkdir(parents=True)

    characters = [
        {
            "canonical_name": CHAR_NAME,
            "role": "protagonist",
            "gender": "male",
            "appearance": "a young prince",
            "description": "golden hair",
            "visual_details": {},
        }
    ]
    (tmp_path / BOOK_ID / "characters.json").write_text(
        json.dumps(characters), encoding="utf-8"
    )

    # Stub generate_character_sheets to write a fake PNG sheet instead of
    # calling Gemini.  The stub must write to the correct path so that
    # the loop in _regen_inner (after QA) can find and read the file.
    def _fake_generate_character_sheets(profiles, book_id, **kwargs):
        sheet_path = chars_dir / f"{SAFE_NAME}_sheet.png"
        sheet_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return [{"sheet_path": str(sheet_path)}]

    monkeypatch.setattr(
        "src.generation.character_sheet.generate_character_sheets",
        _fake_generate_character_sheets,
    )

    # Stub _run_character_sheet_quality so it is a no-op (no QA calls Gemini).
    monkeypatch.setattr(
        "src.routes.generation._run_character_sheet_quality",
        lambda *a, **kw: None,
    )

    # Stub load_characters used inside _regen_inner.
    monkeypatch.setattr(
        "src.routes.helpers.load_characters",
        lambda book_id: characters,
    )

    # Stub gemini_backend helpers used by the outer _regen wrapper.
    import src.gemini_backend as gb
    monkeypatch.setattr(gb, "set_user_api_key", lambda key: None)
    monkeypatch.setattr(gb, "reset_user_api_key", lambda token: None)
    monkeypatch.setattr(gb, "set_gen_error_box", lambda box: None)
    monkeypatch.setattr(gb, "reset_gen_error_box", lambda token: None)
    monkeypatch.setattr(gb, "friendly_gen_error", lambda box: "")

    return tmp_path


def test_character_regen_records_version_in_store(monkeypatch, char_regen_env):
    """After sheet regen, assets.json must contain a character:<char_name> entry.

    RED before Task 3 implementation (no record_image_version call in
    _regen_inner); GREEN afterwards.
    """
    import src.routes.generation as gen_mod
    from src.routes.helpers import _active_regens

    _active_regens.discard((BOOK_ID, "character", CHAR_NAME))

    bt = BackgroundTasks()

    # Call the actual route handler to collect the background task.
    asyncio.run(
        gen_mod.regenerate_character_sheet(
            book_id=BOOK_ID,
            char_name=CHAR_NAME,
            background_tasks=bt,
            user_key="",
        )
    )

    # Run the background task synchronously so we can inspect the store.
    asyncio.run(bt())

    assets = store._load_assets(BOOK_ID)
    key = f"character:{CHAR_NAME}"
    assert key in assets, (
        f"Expected '{key}' in assets.json after character-sheet regen; "
        f"got keys: {list(assets.keys())}"
    )
    versions = assets[key].get("versions", [])
    assert len(versions) >= 1, "Expected at least one version recorded"
    assert versions[-1]["url"] is not None
