"""Generate character reference sheets using Gemini image generation.

Each main character gets a detailed reference sheet showing:
- Front view (full body)
- 3/4 view
- Side view (profile)
- Key expressions (happy, sad, surprised, determined)
- Color palette and distinctive features labeled

The sheet is used as a visual reference for all subsequent page illustrations
to maintain character consistency.
"""

import logging
import re
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


def _build_sheet_prompt(profile: dict, style: str, all_profiles: list[dict] | None = None) -> str:
    """Build a detailed character sheet prompt with strong visual identity."""
    name = profile.get("name", "Character")
    appearance_desc_sentences = profile.get("appearance_description", [])
    traits = profile.get("personality_traits", [])
    role = profile.get("role", "")
    visual_identity = profile.get("visual_identity", "")

    # Book descriptions
    if appearance_desc_sentences:
        book_desc = "\n".join(f"  - {s}" for s in appearance_desc_sentences[:3])
    else:
        book_desc = "  - Design a friendly, memorable HUMAN character."

    # List other characters so this one looks DIFFERENT
    other_chars = ""
    if all_profiles:
        others = [p for p in all_profiles if p.get("name") != name]
        if others:
            other_desc = ", ".join(
                f"{p.get('name')} ({p.get('visual_identity', 'unknown look')})"
                for p in others[:4]
            )
            other_chars = f"\nOTHER CHARACTERS (this character must look DIFFERENT from them): {other_desc}"

    prompt = f"""Character Reference Sheet for children's picture book.

CHARACTER NAME: {name}
ROLE: {role}

BOOK DESCRIPTION:
{book_desc}

ASSIGNED VISUAL IDENTITY: {visual_identity}
{other_chars}

CRITICAL RULES:
- This character MUST be a HUMAN child or adult. NOT an animal, NOT a creature.
- The visual identity above is MANDATORY — use exactly those colors and features.
- The character must be INSTANTLY recognizable and DIFFERENT from other characters.
- Draw in children's picture book style: cute, big eyes, expressive face.

Draw on a clean WHITE background:
1. FRONT VIEW — full body, arms slightly out
2. THREE-QUARTER VIEW — full body, turned right
3. SIDE VIEW — profile facing right
4. FOUR EXPRESSIONS — head only: Happy, Sad, Surprised, Angry

Style: {style}
Do NOT include: {NEGATIVE_PROMPT}
Do NOT include any text or labels."""

    return prompt


# Predefined visual identities — ensures each character looks completely different
_VISUAL_IDENTITIES = [
    {
        "hair": "short straight dark brown hair",
        "outfit": "bright red sweater and blue jeans",
        "feature": "round glasses",
        "colors": "red, blue, brown",
    },
    {
        "hair": "long curly golden blonde hair with a big pink bow",
        "outfit": "yellow polka-dot dress with white collar",
        "feature": "rosy cheeks and a tiny mole near her mouth",
        "colors": "yellow, pink, white",
    },
    {
        "hair": "short spiky ginger/orange hair",
        "outfit": "green polo shirt and khaki shorts",
        "feature": "freckles across nose and cheeks",
        "colors": "green, orange, khaki",
    },
    {
        "hair": "sleek black bob haircut",
        "outfit": "purple turtleneck and dark pants",
        "feature": "sharp confident eyes, athletic posture",
        "colors": "purple, black",
    },
    {
        "hair": "wavy light brown hair, slightly messy",
        "outfit": "blue blazer with white shirt and brown boots",
        "feature": "friendly smile, slightly shy posture",
        "colors": "blue, white, brown",
    },
]


def _assign_visual_identities(profiles: list[dict]) -> list[dict]:
    """Assign distinct visual identities to each character."""
    for i, profile in enumerate(profiles):
        vi = _VISUAL_IDENTITIES[i % len(_VISUAL_IDENTITIES)]
        identity = (
            f"{vi['hair']}, wearing {vi['outfit']}, "
            f"distinctive feature: {vi['feature']}"
        )
        profile["visual_identity"] = identity
        profile["visual_colors"] = vi["colors"]
    return profiles


def _add_labels_to_sheet(image_path: str, name: str, profile: dict) -> str:
    """Add clear labels to a character sheet image using Pillow."""
    from PIL import Image, ImageDraw, ImageFont

    try:
        img = Image.open(image_path)
        w, h = img.size
        draw = ImageDraw.Draw(img)

        # Find a font
        font_paths = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Helvetica.ttc",
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        title_font = None
        detail_font = None
        for fp in font_paths:
            if Path(fp).exists():
                try:
                    title_font = ImageFont.truetype(fp, 28)
                    detail_font = ImageFont.truetype(fp, 16)
                    break
                except Exception:
                    continue
        if not title_font:
            title_font = ImageFont.load_default()
            detail_font = ImageFont.load_default()

        # Add a banner at the bottom with name + visual identity
        banner_h = 80
        # Semi-transparent white banner
        banner = Image.new("RGBA", (w, banner_h), (255, 255, 255, 220))
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        img.paste(banner, (0, h - banner_h), banner)

        draw = ImageDraw.Draw(img)

        # Character name (bold, centered)
        vi = profile.get("visual_identity", "")
        traits = profile.get("personality_traits", [])
        trait_text = ", ".join(traits[:4]) if traits else ""

        # Title line
        try:
            tw = draw.textlength(name, font=title_font)
        except Exception:
            tw = len(name) * 16
        draw.text(((w - tw) // 2, h - banner_h + 8), name, fill=(40, 40, 40), font=title_font)

        # Detail line (visual identity summary)
        detail = vi[:80] if vi else trait_text[:80]
        if detail:
            try:
                dw = draw.textlength(detail, font=detail_font)
            except Exception:
                dw = len(detail) * 9
            draw.text(((w - dw) // 2, h - banner_h + 42), detail, fill=(100, 90, 80), font=detail_font)

        img = img.convert("RGB")
        img.save(image_path, quality=95)
        return image_path

    except Exception as e:
        logger.warning("Failed to add labels to %s: %s", image_path, e)
        return image_path


def _safe_filename(name: str) -> str:
    """Convert a character name to a safe filename."""
    safe = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', name)
    safe = re.sub(r'\s+', '_', safe.strip()).lower()
    return safe[:50] or "character"


def generate_character_sheets(
    character_profiles: list[dict],
    book_id: str,
    style: str | None = None,
    max_characters: int = 5,
) -> list[dict]:
    """Generate character reference sheets for main characters.

    Args:
        character_profiles: Character profile dicts from NLP analysis,
            each with name, appearance, personality_traits, role, etc.
        book_id: Book identifier for file storage.
        style: Optional style override.
        max_characters: Max number of characters to generate sheets for.

    Returns:
        List of dicts: {character_name, sheet_path, description, prompt_used}
    """
    client = _get_client()
    active_style = style or DEFAULT_STYLE
    output_dir = GENERATED_DIR / book_id / "characters"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Filter to main/supporting characters only, limit count
    main_chars = [
        p for p in character_profiles
        if p.get("role") in ("main", "supporting")
    ][:max_characters]

    if not main_chars:
        main_chars = character_profiles[:max_characters]

    if not main_chars:
        logger.warning("No character profiles provided for sheet generation")
        return []

    # Assign distinct visual identities
    main_chars = _assign_visual_identities(main_chars)

    results: list[dict] = []

    for profile in main_chars:
        name = profile.get("name", "Character")
        safe_name = _safe_filename(name)
        save_path = output_dir / f"{safe_name}_sheet"

        prompt = _build_sheet_prompt(profile, active_style, all_profiles=main_chars)
        logger.info("Generating character sheet for '%s'...", name)

        success = False
        description = ""

        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=GEMINI_IMAGE_MODEL,
                    contents=prompt,
                    config=genai.types.GenerateContentConfig(
                        response_modalities=["TEXT", "IMAGE"],
                    ),
                )

                if not response.candidates:
                    logger.warning("No candidates for '%s' attempt %d", name, attempt + 1)
                    continue

                for part in response.candidates[0].content.parts:
                    if hasattr(part, "text") and part.text:
                        description += part.text
                    if hasattr(part, "inline_data") and part.inline_data is not None:
                        mime = part.inline_data.mime_type or "image/png"
                        ext = ".jpg" if "jpeg" in mime or "jpg" in mime else ".png"
                        final_path = save_path.with_suffix(ext)
                        final_path.write_bytes(part.inline_data.data)
                        logger.info("Character sheet for '%s' saved to %s", name, final_path)
                        success = True
                        break

                if success:
                    break

            except Exception as e:
                logger.warning("Character sheet attempt %d for '%s' failed: %s", attempt + 1, name, e)
                if attempt == 0:
                    time.sleep(2)

        # Resolve actual path
        actual_path = ""
        if success:
            for ext in (".png", ".jpg"):
                candidate = save_path.with_suffix(ext)
                if candidate.exists():
                    actual_path = str(candidate)
                    break

        # Add labels with Pillow (name, views, emotions)
        if actual_path:
            actual_path = _add_labels_to_sheet(actual_path, name, profile)

        results.append({
            "character_name": name,
            "sheet_path": actual_path,
            "description": description or f"Character sheet for {name}",
            "prompt_used": prompt[:500],
            "appearance": profile.get("appearance", []),
            "traits": profile.get("personality_traits", []),
            "visual_identity": profile.get("visual_identity", ""),
            "visual_colors": profile.get("visual_colors", ""),
        })

    return results
