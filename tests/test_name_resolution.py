"""Scene/segment character names must resolve to the canonical character
record even when they use a short form the canonical name stores WITH a
leading article.

Root cause of "rocket 去哪里了": the cover's characters_in_scene lists
"Remarkable Rocket", but the character's canonical_name — and thus its sheet
file (the_remarkable_rocket_sheet.png), version asset-key and record — is
"the Remarkable Rocket", and its aliases are ["the Rocket", "Rocket"] (NOT
"Remarkable Rocket"). Exact/case-insensitive matching failed, so the editor
panel showed "?/?"+"No sheet yet" and generation fed the model no reference
sheet — the character was silently dropped from the image.
"""

from __future__ import annotations

from src.routes.helpers import make_character_name_resolver, _normalize_character_name


CHARS = [
    {"canonical_name": "the Remarkable Rocket", "aliases": ["the Rocket", "Rocket"]},
    {"canonical_name": "Swallow", "aliases": []},
    {"canonical_name": "Hugh the Miller", "aliases": []},
    {"canonical_name": "the Happy Prince", "aliases": ["Happy Prince"]},
]


def test_resolves_the_stripped_short_form_to_canonical():
    resolve = make_character_name_resolver(CHARS)
    # the bug case: short form -> canonical WITH "the"
    assert resolve("Remarkable Rocket") == "the Remarkable Rocket"


def test_resolves_by_exact_alias():
    resolve = make_character_name_resolver(CHARS)
    assert resolve("Rocket") == "the Remarkable Rocket"
    assert resolve("Happy Prince") == "the Happy Prince"  # alias present


def test_exact_and_caseinsensitive_canonical_pass_through():
    resolve = make_character_name_resolver(CHARS)
    assert resolve("Swallow") == "Swallow"
    assert resolve("swallow") == "Swallow"
    assert resolve("Hugh the Miller") == "Hugh the Miller"


def test_unknown_name_falls_back_unchanged():
    resolve = make_character_name_resolver(CHARS)
    assert resolve("Nobody") == "Nobody"
    assert resolve("") == ""


def test_normalize_drops_leading_article_and_collapses_space():
    assert _normalize_character_name("The  Remarkable   Rocket") == "remarkable rocket"
    assert _normalize_character_name("Remarkable Rocket") == "remarkable rocket"
    assert _normalize_character_name("A Swallow") == "swallow"


def test_own_canonical_wins_over_another_characters_alias():
    # "Happy Prince" is an alias of "the Happy Prince" but also a normalized
    # form; a hypothetical char literally named "Happy Prince" should win.
    chars = [
        {"canonical_name": "the Happy Prince", "aliases": ["Happy Prince"]},
        {"canonical_name": "Happy Prince", "aliases": []},
    ]
    resolve = make_character_name_resolver(chars)
    assert resolve("Happy Prince") == "Happy Prince"  # exact canonical beats alias


def test_sheets_for_resolves_short_name_to_the_canonical_sheet(monkeypatch, tmp_path):
    """_sheets_for('Remarkable Rocket') must find the_remarkable_rocket_sheet.png
    (canonical), not look for remarkable_rocket_sheet.png (which never exists)."""
    import src.routes.generation as gen

    monkeypatch.setattr(gen, "GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.selected_version_image", lambda *a, **k: None)
    monkeypatch.setattr("src.core.storage.localize", lambda *a, **k: None)
    monkeypatch.setattr(gen, "load_characters", lambda book_id: CHARS)

    book = "bk"
    chars_dir = tmp_path / book / "characters"
    chars_dir.mkdir(parents=True)
    (chars_dir / "the_remarkable_rocket_sheet.png").write_bytes(b"SHEET")

    out = gen._sheets_for(book, ["Remarkable Rocket"])
    assert len(out) == 1, f"expected the rocket sheet to resolve; got {out}"
    assert out[0]["sheet_path"].endswith("the_remarkable_rocket_sheet.png")
    # character_name stays the scene name so downstream scene-based matching holds
    assert out[0]["character_name"] == "Remarkable Rocket"
