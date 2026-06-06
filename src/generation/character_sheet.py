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

    gender = profile.get("gender", "unknown")

    prompt = f"""Detailed Character Reference Sheet for children's picture book.

CHARACTER NAME: {name}
GENDER: {gender} — this character MUST look {gender}. {'Draw as a MAN/BOY.' if gender == 'male' else 'Draw as a WOMAN/GIRL.' if gender == 'female' else ''}
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
- CLOTHING MUST match the historical period of the story (e.g., 1780s France = frock coats, bonnets, cravats; NOT modern clothes like hoodies or jeans)
- If book descriptions mention specific clothing or appearance, follow them exactly.

Draw on a clean WHITE background, organized in a clear grid layout:

ROW 1 — FULL BODY VIEWS:
1. FRONT VIEW — full body, arms slightly out, showing complete outfit
2. THREE-QUARTER VIEW — full body, turned right
3. SIDE VIEW — profile facing right

ROW 2 — CLOSE-UP DETAILS:
4. FACE CLOSE-UP — showing hair style, eye color, facial features clearly
5. OUTFIT CLOSE-UP — chest/torso area showing clothing details: buttons, collar, fabric pattern, scarf, tie, brooch, etc.
6. ACCESSORIES CLOSE-UP — hat, glasses, gloves, bag, shoes, or other distinctive items (draw only items this character has)

ROW 3 — EXPRESSIONS:
7. Happy expression (head only)
8. Sad expression (head only)
9. Surprised expression (head only)
10. Angry expression (head only)

BOTTOM — COLOR PALETTE:
Draw 4-5 colored circles showing this character's exact colors (hair, skin, outfit main color, outfit accent color, accessory color)

Style: {style}
Do NOT include: {NEGATIVE_PROMPT}
Label each section with small text (e.g., "FRONT", "SIDE", "OUTFIT", "Happy")."""

    return prompt


# Predefined visual identities — ensures each character looks completely different
# Fallback visual identities — used ONLY when book text has no appearance descriptions.
# Designed to be period-neutral and distinctive from each other.
_VISUAL_IDENTITIES = [
    {"hair": "short straight dark brown hair", "outfit": "a dark coat with brass buttons and a white cravat", "feature": "round spectacles, kind eyes", "colors": "brown, white, brass"},
    {"hair": "long curly golden blonde hair with a ribbon", "outfit": "a soft blue dress with white lace collar", "feature": "rosy cheeks, gentle expression", "colors": "gold, blue, white"},
    {"hair": "short spiky ginger/orange hair", "outfit": "a patched brown waistcoat and rolled-up sleeves", "feature": "freckles across nose, mischievous grin", "colors": "orange, brown, beige"},
    {"hair": "sleek black hair in a neat bun", "outfit": "a dark purple dress with a shawl", "feature": "sharp confident eyes, stern posture", "colors": "purple, black"},
    {"hair": "wavy light brown hair, slightly messy", "outfit": "a navy blue coat with silver buttons and brown boots", "feature": "friendly smile, slightly shy posture", "colors": "blue, silver, brown"},
    {"hair": "long straight silver-white hair tied back", "outfit": "a dark green frock coat with gold trim", "feature": "thin mustache, tall and dignified", "colors": "green, gold, silver"},
    {"hair": "curly dark red hair under a bonnet", "outfit": "a cream-colored dress with floral embroidery", "feature": "big dimples, warm smile", "colors": "cream, red, green"},
    {"hair": "short neat black hair, parted to the side", "outfit": "a grey waistcoat over white shirt, dark trousers", "feature": "wire-rimmed glasses, serious expression", "colors": "grey, white, black"},
    {"hair": "long wavy auburn hair, loose", "outfit": "a teal dress with lace trim and a cameo brooch", "feature": "kind eyes, delicate features", "colors": "teal, cream, auburn"},
    {"hair": "messy dark brown curls under a flat cap", "outfit": "a threadbare brown jacket and muddy boots", "feature": "smudgy face, watchful eyes", "colors": "brown, beige, grey"},
    {"hair": "neat grey hair in a bun", "outfit": "a maroon shawl over a cream blouse", "feature": "wrinkled smile, reading spectacles on chain", "colors": "maroon, cream, grey"},
    {"hair": "thick curly black hair", "outfit": "a dark red vest over a loose white shirt", "feature": "broad shoulders, commanding presence", "colors": "red, white, black"},
    {"hair": "straight platinum blonde hair, shoulder length", "outfit": "a pale blue military-style jacket with epaulettes", "feature": "pale blue eyes, pointed nose", "colors": "blue, white, blonde"},
    {"hair": "wild grey-streaked hair, unkempt", "outfit": "a tattered old coat, bare feet", "feature": "haunted hollow eyes, gaunt face", "colors": "grey, brown, pale"},
    {"hair": "short sandy hair under a tricorn hat", "outfit": "a leather apron over a rough linen shirt", "feature": "strong jaw, calloused hands", "colors": "tan, brown, white"},
]


def _assign_visual_identities(profiles: list[dict]) -> list[dict]:
    """Assign distinct visual identities to each character.

    Uses book descriptions (appearance_description) when available,
    falls back to predefined identities when the book doesn't describe the character.
    """
    used_fallback_indices = set()

    for i, profile in enumerate(profiles):
        # Check if we have appearance descriptions from the book text
        book_desc = profile.get("appearance_description", [])
        if isinstance(book_desc, str):
            book_desc = [book_desc] if book_desc else []

        if book_desc and any(len(d) > 10 for d in book_desc):
            # Use book description as the primary visual identity
            desc_text = "; ".join(d for d in book_desc[:3] if len(d) > 5)
            # Still assign a fallback for colors/outfit structure
            fallback_idx = i % len(_VISUAL_IDENTITIES)
            vi = _VISUAL_IDENTITIES[fallback_idx]
            identity = (
                f"Based on book description: {desc_text}. "
                f"If not described in book, use: {vi['hair']}, wearing {vi['outfit']}, "
                f"distinctive feature: {vi['feature']}"
            )
            profile["visual_identity"] = identity
            profile["visual_colors"] = vi["colors"]
        else:
            # No book description — use predefined fallback
            fallback_idx = i % len(_VISUAL_IDENTITIES)
            while fallback_idx in used_fallback_indices and len(used_fallback_indices) < len(_VISUAL_IDENTITIES):
                fallback_idx = (fallback_idx + 1) % len(_VISUAL_IDENTITIES)
            used_fallback_indices.add(fallback_idx)
            vi = _VISUAL_IDENTITIES[fallback_idx]
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
    max_characters: int = 0,
) -> list[dict]:
    """Generate character reference sheets for ALL main/supporting characters.

    Args:
        character_profiles: Character profile dicts from NLP analysis.
        book_id: Book identifier for file storage.
        style: Optional style override.
        max_characters: Max characters (0 = no limit, generate all main+supporting).

    Returns:
        List of dicts with character_name, sheet_path, description, background, etc.
    """
    client = _get_client()
    active_style = style or DEFAULT_STYLE
    output_dir = GENERATED_DIR / book_id / "characters"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Filter to real characters: main/supporting with enough mentions
    MIN_MENTIONS = 5  # Must appear at least 5 times to get a sheet
    main_chars = [
        p for p in character_profiles
        if p.get("role") in ("main", "supporting")
        and p.get("mention_count", 0) >= MIN_MENTIONS
    ]

    if not main_chars:
        # Fallback: top characters by mention count
        main_chars = sorted(character_profiles, key=lambda p: p.get("mention_count", 0), reverse=True)[:10]

    if max_characters > 0:
        main_chars = main_chars[:max_characters]

    logger.info("Generating sheets for %d characters (filtered from %d profiles, min %d mentions)",
                len(main_chars), len(character_profiles), MIN_MENTIONS)

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
                        image_config=genai.types.ImageConfig(
                            aspect_ratio="1:1",
                        ),
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

        # Build background from available profile data
        role = profile.get("role", "unknown")
        traits = profile.get("personality_traits", [])
        co_chars = profile.get("co_occurring_characters", {})
        top_relations = sorted(co_chars.items(), key=lambda x: x[1], reverse=True)[:3] if co_chars else []
        relations_str = ", ".join(f"{k} ({v} scenes together)" for k, v in top_relations)

        background = (
            f"{name} is a {role} character. "
            f"Personality: {', '.join(traits[:4]) if traits else 'not specified'}. "
            f"Often appears with: {relations_str or 'various characters'}."
        )

        results.append({
            "character_name": name,
            "sheet_path": actual_path,
            "description": description or f"Character sheet for {name}",
            "background": background,
            "role": role,
            "prompt_used": prompt[:500],
            "appearance": profile.get("appearance", []),
            "traits": traits,
            "visual_identity": profile.get("visual_identity", ""),
            "visual_colors": profile.get("visual_colors", ""),
        })

    return results
