"""Character-sheet generation must NEVER force a character into a human.

The prompt used to hardcode "HUMAN character only. NOT an animal.", so the
Swallow (book data: "A small bird with brown wings") was drawn as a human boy
with wings. The prompt is now species-FAITHFUL: it never forces a species, always
tells the model to render the APPEARANCE truthfully (animals as animals, never
humanized), and phrases human-only instructions (gender, clothing) conditionally.
`_is_non_human` is a confident-when-true hint only — it MUST NOT gate a
"draw as a human" branch, because the book data is inconsistent and it misses
many animals (Nightingale, Reed, ...). The guarantee under test: no profile,
detected-animal or not, ever yields a human-forcing instruction.
"""

from src.generation.character_sheet import _build_sheet_prompt, _is_non_human

SWALLOW = {  # data clearly marks it non-human
    "name": "Swallow", "gender": "unknown",
    "appearance_description": ["A small bird with brown wings."],
    "visual_details": {"skin_tone": "not applicable", "hair": "not applicable",
                       "distinctive": "brown wings", "build": "small"},
}
NIGHTINGALE = {  # an ANIMAL the conservative detector MISSES (empty/other fields)
    "name": "Nightingale", "gender": "female",
    "appearance_description": ["A small brown nightingale bird."],
    "visual_details": {"skin_tone": "", "hair": "", "distinctive": "brown feathers"},
}
HUMAN = {
    "name": "Hugh the Miller", "gender": "male",
    "appearance_description": ["A stout man with a red beard."],
    "visual_details": {"skin_tone": "fair", "hair": "red beard, balding",
                       "clothing": "miller's apron"},
}

_FORCED_HUMAN = ("HUMAN character only", "NOT an animal", "- HUMAN character.")


def test_is_non_human_conservative():
    assert _is_non_human(SWALLOW) is True
    assert _is_non_human(HUMAN) is False
    assert _is_non_human(NIGHTINGALE) is False  # misses it — that's why we never force human
    assert _is_non_human({"visual_details": {}}) is False


def test_no_profile_ever_forces_human():
    """The core guarantee: detected-animal, missed-animal, and human all avoid any
    human-forcing instruction, and all carry the faithful-to-appearance rule."""
    for prof in (SWALLOW, NIGHTINGALE, HUMAN):
        p = _build_sheet_prompt(prof, "paper collage")
        for bad in _FORCED_HUMAN:
            assert bad not in p, f"{prof['name']}: prompt force-humanizes ({bad!r})"
        assert "never humanize" in p.lower()
        assert "APPEARANCE describes" in p
        # gender is only ever applied CONDITIONALLY, never an unconditional order
        assert "If a person, draw as a" in p or "draw as a MAN/BOY" not in p.lower().replace("if a person, draw as a ", "")


def test_detected_animal_gets_extra_emphasis():
    p = _build_sheet_prompt(SWALLOW, "paper collage")
    assert "IS an animal/creature" in p
    assert "small bird" in p.lower() or "brown wings" in p.lower()


def test_human_gets_conditional_gender_not_forced():
    p = _build_sheet_prompt(HUMAN, "paper collage")
    assert "If a person, draw as a MAN/BOY." in p
    assert "IS an animal/creature" not in p
