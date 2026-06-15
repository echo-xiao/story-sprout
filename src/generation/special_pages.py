"""Generate special page illustrations using Gemini with reference images.

Special pages:
- Book cover: main characters + iconic scene, with title
- Chapter title page: scene representing the chapter's theme
- Back cover ("The End"): warm farewell illustration

All special pages use character sheets, scene sheets, and the book cover
as visual references to maintain style consistency.
"""

import logging
from pathlib import Path

from google import genai

from src.config import (
    DEFAULT_STYLE,
    GEMINI_IMAGE_MODEL,
    GENERATED_DIR,
    NEGATIVE_PROMPT,
)
from src.generation.image_utils import _get_client, _load_image_part, save_inline_image

logger = logging.getLogger(__name__)


def _build_reference_parts(
    character_sheets: list[dict] | None = None,
    scene_sheet_path: str | None = None,
    style_ref_path: str | None = None,
) -> list[dict]:
    """Build reference image parts for style consistency."""
    parts = []

    # Style reference (book cover) first
    if style_ref_path:
        img = _load_image_part(style_ref_path)
        if img:
            parts.append({"text": "[STYLE REFERENCE — match this art style, color palette, and visual tone EXACTLY]"})
            parts.append(img)

    # Character sheets
    if character_sheets:
        for sheet in character_sheets[:4]:
            name = sheet.get("character_name", "character")
            path = sheet.get("sheet_path", "")
            if not path:
                continue
            img = _load_image_part(path)
            if img:
                parts.append({"text": (
                    f"[CHARACTER REFERENCE: {name}] — COPY this character EXACTLY: same hair color, "
                    f"same hairstyle, same outfit and its EXACT colors, same accessories. Do NOT "
                    f"recolor or restyle the clothing. This sheet is the ground truth for {name}."
                )})
                parts.append(img)

    # Scene sheet
    if scene_sheet_path:
        img = _load_image_part(scene_sheet_path)
        if img:
            parts.append({"text": "[SCENE BACKGROUND REFERENCE — match this setting style]"})
            parts.append(img)

    return parts


def _generate_image_with_refs(
    prompt: str,
    save_path: Path,
    character_sheets: list[dict] | None = None,
    scene_sheet_path: str | None = None,
    style_ref_path: str | None = None,
    max_retries: int = 2,
) -> str:
    """Generate a single illustration with reference images. Returns saved path or empty string."""
    client = _get_client()
    save_path.parent.mkdir(parents=True, exist_ok=True)

    # Build content: references first, then prompt
    parts = _build_reference_parts(character_sheets, scene_sheet_path, style_ref_path)
    parts.append({"text": prompt})

    from src.gemini_backend import call_gemini_with_backoff, note_gen_failure

    def _attempt() -> str:
        response = client.models.generate_content(
            model=GEMINI_IMAGE_MODEL,
            contents=parts,
            config=genai.types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
                image_config=genai.types.ImageConfig(aspect_ratio="1:1"),
            ),
        )
        return save_inline_image(response, save_path)

    try:
        final_path = call_gemini_with_backoff(_attempt, max_retries=max_retries, label=save_path.name)
    except Exception as e:
        logger.warning("Special page generation failed: %s", e)
        note_gen_failure(e)
        return ""
    if final_path:
        logger.info("Saved special page to %s", final_path)
        return final_path
    return ""


def _find_book_cover(book_id: str) -> str | None:
    """Find existing book cover to use as style reference."""
    special_dir = GENERATED_DIR / book_id / "special"
    for ext in (".png", ".jpg"):
        p = special_dir / f"book_cover{ext}"
        if p.exists():
            return str(p)
    return None


def get_style_ref(book_id: str) -> str | None:
    """The book's single style anchor, as a LOCAL path: a user-uploaded style
    reference if one is set, else the book cover. Resolves through durable
    storage (GCS) and materializes the image locally (generators read paths),
    so it works even after a Cloud Run redeploy wiped the local disk.

    Every scene/character/page generation anchors to this — upload one reference
    and the whole book matches it; edit it and the book re-anchors."""
    from src.core import storage

    # 1) User-uploaded style reference wins.
    for ext in ("png", "jpg"):
        key = f"{book_id}/style_reference.{ext}"
        if storage.exists(key):
            local = storage.localize(key)
            if local:
                return local
    # 2) Default: the book cover (via storage so a redeploy can't lose it).
    for ext in ("png", "jpg"):
        key = f"{book_id}/special/book_cover.{ext}"
        if storage.exists(key):
            local = storage.localize(key)
            if local:
                return local
    return _find_book_cover(book_id)


def _background_block(background: str) -> str:
    """Shared SETTING block — editable scene_background reaches every prompt."""
    if not background.strip():
        return ""
    return f"\nSETTING / BACKGROUND:\n{background.strip()[:300]}\n"


def generate_book_cover(
    title: str,
    characters: list[dict],
    book_id: str,
    character_sheets: list[dict] | None = None,
    scene_sheet_path: str | None = None,
    style: str | None = None,
    subtitle: str = "A Picture Book",
    background: str = "",
) -> str:
    """Generate an illustrated book cover. This is the style anchor for the whole book."""
    active_style = style or DEFAULT_STYLE

    char_desc = ""
    for c in characters[:5]:
        name = c.get("name", "")
        vi = c.get("visual_identity", "")
        if name and vi:
            char_desc += f"- {name}: {vi}\n"

    title_spelled = "-".join(title.upper())

    prompt = f"""Create a beautiful BOOK COVER illustration for a children's picture book.

TITLE: "{title}"

MAIN CHARACTERS (draw them prominently):
{char_desc or "Draw friendly, memorable children's book characters."}
{_background_block(background)}
REQUIREMENTS:
- This is the FRONT COVER of the book
- Draw the title "{title}" in large, playful, hand-drawn lettering at the top
- SPELL THE TITLE EXACTLY letter by letter: {title_spelled}
- Add "{subtitle}" as subtitle below the title
- The main characters should be front and center, looking inviting and friendly
- Use a warm, eye-catching color scheme that makes kids want to pick up the book
- Include a hint of the story's setting in the background
- The composition should be balanced and professional, like a real published book cover

CHARACTER FIDELITY (critical — match the reference sheets above):
- Draw each character with the EXACT hair color, outfit, and OUTFIT COLORS shown on their reference sheet.
- Do NOT recolor or restyle their clothes. Purple dress stays purple; cream suit stays cream.
- Keep each character's distinctive accessories (hats, glasses, etc.) from their sheet.

STRICT TEXT RULES:
- The ONLY text in this image should be the title "{title}" and subtitle "{subtitle}".
- Do NOT add any other text, labels, credits, or metadata.
- Do NOT misspell the title. Copy it letter by letter: {title_spelled}

Style: {active_style}
Do NOT include: {NEGATIVE_PROMPT}"""

    save_path = GENERATED_DIR / book_id / "special" / "book_cover"
    return _generate_image_with_refs(prompt, save_path, character_sheets, scene_sheet_path)


def generate_chapter_cover(
    chapter_title: str,
    chapter_num: int,
    chapter_summary: str,
    characters: list[dict],
    book_id: str,
    character_sheets: list[dict] | None = None,
    scene_sheet_path: str | None = None,
    style: str | None = None,
    background: str = "",
) -> str:
    """Generate a chapter title page illustration, referencing book cover for style."""
    active_style = style or DEFAULT_STYLE
    style_ref = _find_book_cover(book_id)

    char_desc = ""
    for c in characters[:3]:
        name = c.get("name", "")
        vi = c.get("visual_identity", "")
        if name and vi:
            char_desc += f"- {name}: {vi}\n"

    chapter_title_spelled = "-".join(chapter_title.upper())

    prompt = f"""Create a CHAPTER TITLE PAGE illustration for a children's picture book.

CHAPTER {chapter_num}: "{chapter_title}"
CHAPTER THEME: {chapter_summary[:200]}

CHARACTERS:
{char_desc or "Draw characters that match the chapter's mood."}
{_background_block(background)}
REQUIREMENTS:
- Draw "Chapter {chapter_num}" and "{chapter_title}" in playful hand-drawn lettering
- Spell the chapter title exactly: {chapter_title_spelled}
- The illustration should hint at what this chapter is about
- MATCH THE STYLE of the book cover reference image exactly (same color palette, line quality, texture)
- Leave some breathing room — this is a transition page, not a full scene
- Include decorative elements (vines, stars, swirls) around the title

STRICT TEXT RULES:
- The ONLY text should be "Chapter {chapter_num}" and "{chapter_title}".
- Do NOT add any other text, page numbers, or metadata.

Style: {active_style}
Do NOT include: {NEGATIVE_PROMPT}"""

    save_path = GENERATED_DIR / book_id / "special" / f"chapter_{chapter_num:02d}_cover"
    return _generate_image_with_refs(prompt, save_path, character_sheets, scene_sheet_path, style_ref)


def generate_back_cover(
    title: str,
    book_id: str,
    character_sheets: list[dict] | None = None,
    scene_sheet_path: str | None = None,
    style: str | None = None,
    title_text: str = "The End",
    subtitle_text: str = "Thank you for reading!",
    background: str = "",
) -> str:
    """Generate an illustrated back cover, referencing book cover for style."""
    active_style = style or DEFAULT_STYLE
    style_ref = _find_book_cover(book_id)

    title_spelled = "-".join(title_text.upper())

    prompt = f"""Create a beautiful BACK COVER illustration for a children's picture book.
{_background_block(background)}
REQUIREMENTS:
- Draw "{title_text}" in large, playful, hand-drawn lettering in the center. Spell exactly: {title_spelled}
- Add "{subtitle_text}" below it in smaller text
- MATCH THE STYLE of the book cover reference image exactly (same color palette, line quality, texture)
- The illustration should feel warm, cozy, and satisfying — like finishing a good bedtime story
- Include small references to the story (tiny versions of characters waving goodbye, key objects from the story)
- Use warm sunset/twilight colors
- Add decorative borders or frames

STRICT TEXT RULES:
- The ONLY text allowed is "{title_text}" and "{subtitle_text}"
- Do NOT add the book title, credits, page numbers, or any other text.

Style: {active_style}
Do NOT include: {NEGATIVE_PROMPT}"""

    save_path = GENERATED_DIR / book_id / "special" / "back_cover"
    return _generate_image_with_refs(prompt, save_path, character_sheets, scene_sheet_path, style_ref)
