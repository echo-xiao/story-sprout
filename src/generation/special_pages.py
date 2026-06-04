"""Generate special page illustrations using Gemini.

Special pages:
- Book cover: main characters + iconic scene, with title
- Chapter title page: scene representing the chapter's theme
- Chapter ending page: closing mood illustration
- Back cover ("The End"): warm farewell illustration
"""

import logging
import time
from pathlib import Path

from google import genai

from src.config import (
    DEFAULT_STYLE,
    GEMINI_API_KEY,
    GEMINI_IMAGE_MODEL,
    GENERATED_DIR,
    NEGATIVE_PROMPT,
)

logger = logging.getLogger(__name__)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def _generate_image(prompt: str, save_path: Path, max_retries: int = 2) -> str:
    """Generate a single illustration and save to disk. Returns the saved path or empty string."""
    client = _get_client()
    save_path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=GEMINI_IMAGE_MODEL,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                ),
            )
            if not response.candidates:
                continue
            for part in response.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data is not None:
                    mime = part.inline_data.mime_type or "image/png"
                    ext = ".jpg" if "jpeg" in mime or "jpg" in mime else ".png"
                    final_path = save_path.with_suffix(ext)
                    final_path.write_bytes(part.inline_data.data)
                    logger.info("Saved special page to %s", final_path)
                    return str(final_path)
        except Exception as e:
            logger.warning("Special page attempt %d failed: %s", attempt + 1, e)
            if attempt < max_retries - 1:
                time.sleep(2)

    return ""


def generate_book_cover(
    title: str,
    characters: list[dict],
    book_id: str,
    style: str | None = None,
) -> str:
    """Generate an illustrated book cover."""
    active_style = style or DEFAULT_STYLE

    # Build character descriptions
    char_desc = ""
    for c in characters[:5]:
        name = c.get("name", "")
        vi = c.get("visual_identity", "")
        if name and vi:
            char_desc += f"- {name}: {vi}\n"

    prompt = f"""Create a beautiful BOOK COVER illustration for a children's picture book.

TITLE: "{title}"

MAIN CHARACTERS (draw them prominently):
{char_desc or "Draw friendly, memorable children's book characters."}

REQUIREMENTS:
- This is the FRONT COVER of the book
- Draw the title "{title}" in large, playful, hand-drawn lettering at the top
- Add "A Picture Book" as subtitle below the title
- The main characters should be front and center, looking inviting and friendly
- Use a warm, eye-catching color scheme that makes kids want to pick up the book
- Include a hint of the story's setting in the background
- The composition should be balanced and professional, like a real published book cover

Style: {active_style}
Do NOT include: {NEGATIVE_PROMPT}"""

    save_path = GENERATED_DIR / book_id / "special" / "book_cover"
    return _generate_image(prompt, save_path)


def generate_chapter_cover(
    chapter_title: str,
    chapter_num: int,
    chapter_summary: str,
    characters: list[dict],
    book_id: str,
    style: str | None = None,
) -> str:
    """Generate a chapter title page illustration."""
    active_style = style or DEFAULT_STYLE

    char_desc = ""
    for c in characters[:3]:
        name = c.get("name", "")
        vi = c.get("visual_identity", "")
        if name and vi:
            char_desc += f"- {name}: {vi}\n"

    prompt = f"""Create a CHAPTER TITLE PAGE illustration for a children's picture book.

CHAPTER {chapter_num}: "{chapter_title}"
CHAPTER THEME: {chapter_summary[:200]}

CHARACTERS:
{char_desc or "Draw characters that match the chapter's mood."}

REQUIREMENTS:
- Draw the chapter number and title in playful hand-drawn lettering
- The illustration should hint at what this chapter is about
- Use a distinct color palette that represents this chapter's mood
- Leave some breathing room — this is a transition page, not a full scene
- The illustration should make the reader excited to turn the page
- Include decorative elements (vines, stars, swirls) around the title

Style: {active_style}
Do NOT include: {NEGATIVE_PROMPT}"""

    save_path = GENERATED_DIR / book_id / "special" / f"chapter_{chapter_num:02d}_cover"
    return _generate_image(prompt, save_path)


def generate_chapter_ending(
    chapter_title: str,
    chapter_num: int,
    ending_text: str,
    characters: list[dict],
    book_id: str,
    style: str | None = None,
) -> str:
    """Generate a chapter ending illustration."""
    active_style = style or DEFAULT_STYLE

    char_desc = ""
    for c in characters[:3]:
        name = c.get("name", "")
        vi = c.get("visual_identity", "")
        if name and vi:
            char_desc += f"- {name}: {vi}\n"

    prompt = f"""Create a CHAPTER ENDING illustration for a children's picture book.

END OF CHAPTER {chapter_num}: "{chapter_title}"
CLOSING SCENE: {ending_text[:200]}

CHARACTERS:
{char_desc or "Draw characters in a reflective or transitional moment."}

REQUIREMENTS:
- This marks the END of a chapter — create a sense of pause and reflection
- The mood should be contemplative or transitional
- Include a small decorative "End of Chapter {chapter_num}" text element
- Use softer, more muted colors than the main pages
- The composition should feel like a gentle fade-out
- Add small decorative elements (a small ornament, a trailing vine, etc.)

Style: {active_style}
Do NOT include: {NEGATIVE_PROMPT}"""

    save_path = GENERATED_DIR / book_id / "special" / f"chapter_{chapter_num:02d}_ending"
    return _generate_image(prompt, save_path)


def generate_back_cover(
    title: str,
    book_id: str,
    style: str | None = None,
) -> str:
    """Generate an illustrated back cover / 'The End' page."""
    active_style = style or DEFAULT_STYLE

    prompt = f"""Create a beautiful BACK COVER illustration for a children's picture book titled "{title}".

REQUIREMENTS:
- Draw "The End" in large, playful, hand-drawn lettering in the center
- Add "Thank you for reading!" below it in smaller text
- The illustration should feel warm, cozy, and satisfying — like finishing a good bedtime story
- Include small references to the story (tiny versions of characters waving goodbye, key objects from the story)
- Use warm sunset/twilight colors
- Add decorative borders or frames
- The overall feeling should be: "That was a wonderful story!"

Style: {active_style}
Do NOT include: {NEGATIVE_PROMPT}"""

    save_path = GENERATED_DIR / book_id / "special" / "back_cover"
    return _generate_image(prompt, save_path)
