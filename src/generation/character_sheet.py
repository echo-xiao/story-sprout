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
from pathlib import Path

from google import genai

from src.config import (
    DEFAULT_STYLE,
    GEMINI_IMAGE_MODEL,
    GENERATED_DIR,
    NEGATIVE_PROMPT,
)
from src.generation.image_utils import _get_client, save_inline_image

logger = logging.getLogger(__name__)


def _is_non_human(profile: dict) -> bool:
    """True when the character is an animal/creature/object rather than a human —
    DERIVED from the book data, not a hardcoded species list.

    A human has a skin tone and hair; the preprocess (auto-fill / LLM) marks a
    non-human's `skin_tone` AND `hair` as "not applicable" (as it did for the
    Swallow: skin/hair "not applicable", ethnicity "swallow"). We require BOTH to
    be explicitly not-applicable (not merely empty — an incomplete HUMAN profile
    has empty fields, so empty must NOT be read as non-human). This keeps the
    detection conservative and data-driven, so the sheet prompt stops forcing
    animals into humans without ever enumerating species.
    """
    vd = profile.get("visual_details", {}) or {}
    na = {"not applicable", "n/a", "none"}
    skin = (vd.get("skin_tone") or "").strip().lower()
    hair = (vd.get("hair") or "").strip().lower()
    return skin in na and hair in na


def _build_sheet_prompt(profile: dict, style: str, all_profiles: list[dict] | None = None) -> str:
    """Build a character sheet prompt that prioritizes concrete physical details."""
    name = profile.get("name", "Character")
    gender = profile.get("gender", "unknown")

    # Extract concrete visual details (highest priority)
    vd = profile.get("visual_details", {})

    # Build the MUST-HAVE physical spec from visual_details
    physical_specs = []
    for key, label in [("hair", "HAIR"), ("eyes", "EYES"), ("skin_tone", "SKIN"),
                       ("age", "AGE"), ("build", "BUILD"), ("clothing", "CLOTHING"),
                       ("accessories", "ACCESSORIES"), ("distinctive", "DISTINCTIVE FEATURE")]:
        val = vd.get(key, "")
        if val and val.lower() not in ("not described", "unknown", ""):
            physical_specs.append(f"  {label}: {val}")

    # The book's own appearance/description — this is where the character's
    # SPECIES/NATURE lives ("A small bird with brown wings"). It must ALWAYS be in
    # the prompt (not just a fallback when visual_details is empty), or the model
    # has nothing telling it the character is an animal and the "be faithful to the
    # appearance" rule has nothing to be faithful to.
    appearance = profile.get("appearance_description", [])
    if isinstance(appearance, str):
        appearance = [appearance] if appearance else []
    appearance_lines = [f"  - {s.strip()}" for s in appearance[:2] if (s or "").strip()]

    blocks = []
    if appearance_lines:
        blocks.append("\n".join(appearance_lines))
    if physical_specs:
        blocks.append("\n".join(physical_specs))
    physical_block = "\n\n".join(blocks) or "  Design a friendly, memorable character."

    # Key features to repeat for emphasis
    hair_desc = vd.get("hair", "")
    eyes_desc = vd.get("eyes", "")
    emphasis = ""
    if hair_desc or eyes_desc:
        parts = []
        if hair_desc:
            parts.append(f"{hair_desc}")
        if eyes_desc:
            parts.append(f"{eyes_desc}")
        emphasis = "\n\nREPEAT — THE MOST IMPORTANT FEATURES TO GET RIGHT:\n  " + ", ".join(parts)

    # Species-faithful, NOT hardcoded-human. The old prompt forced "HUMAN only.
    # NOT an animal", turning the Swallow into a human boy with wings. We NEVER
    # force a species now: the APPEARANCE text is the ground truth and the model
    # must render it faithfully (animals as animals). `_is_non_human` is only a
    # confident-when-true hint for extra emphasis — it misses some animals (the
    # book data is inconsistent), so it must never gate a "draw as human" branch;
    # human-specific instructions (gender, clothing) are phrased CONDITIONALLY so
    # they can't humanize an animal the hint missed.
    non_human = _is_non_human(profile)
    faithful_rule = (
        "- Render the character EXACTLY as the APPEARANCE describes. If it is an\n"
        "  animal, plant or object, draw its TRUE form — never humanize it (no\n"
        "  human body/hands and no clothing on an animal unless the book explicitly\n"
        "  describes them). If it is a person, draw a person."
    )
    if non_human:
        faithful_rule += ("\n- This character IS an animal/creature — its real animal form, "
                          "expressive and full of character, NOT a human.")
    gender_note = ""
    if gender in ("male", "female") and not non_human:
        gender_note = f"If a person, draw as a {'MAN/BOY' if gender == 'male' else 'WOMAN/GIRL'}."

    rules = [
        faithful_rule,
        '- DO NOT add ANY text, labels, or words to the image. No "FRONT", no names, nothing.',
        "- Period-accurate clothing IF the character wears clothes (modern dress is wrong; animals are unclothed unless the book says otherwise).",
        "- Cute children's book style, big expressive eyes.",
    ]
    rules_block = "\n".join(rules)

    prompt = f"""Character Reference Sheet — children's picture book.

CHARACTER: {name} ({gender}). {gender_note}

MANDATORY PHYSICAL APPEARANCE — follow EXACTLY:
{physical_block}
{emphasis}

LAYOUT (clean WHITE background, NO text labels, NO words anywhere):

Row 1: FRONT view (full body) | THREE-QUARTER view | SIDE profile
Row 2: FACE close-up | OUTFIT or KEY-FEATURE close-up | ACCESSORIES or MARKINGS close-up
Row 3: Happy expression | Sad | Surprised | Angry (head only each)
Bottom: 4-5 color swatches (circles showing exact main colors)

RULES:
{rules_block}

Style: {style}
Do NOT include: {NEGATIVE_PROMPT}"""

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


def _safe_filename(name: str) -> str:
    """Convert a character name to a safe filename."""
    safe = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', name)
    safe = re.sub(r'\s+', '_', safe.strip()).lower()
    return safe[:50] or "character"


def _generate_portrait(client, profile: dict, output_dir: Path, style: str,
                       force: bool = False) -> str:
    """Generate a simple front-facing portrait (head + upper body).

    This is generated FIRST, then used as reference for the full character sheet.
    Returns the path to the saved portrait image.
    """
    name = profile.get("name", "Character")
    safe_name = _safe_filename(name)
    portrait_path = output_dir / f"{safe_name}_portrait"

    # Reuse an existing portrait ONLY when not force-regenerating. On serverless,
    # storage.localize re-materialises the current image into /tmp, so a
    # user-initiated regen would otherwise "find" it and skip — reusing stale art.
    if not force:
        for ext in (".png", ".jpg"):
            if portrait_path.with_suffix(ext).exists():
                logger.info("Portrait for '%s' already exists, skipping", name)
                return str(portrait_path.with_suffix(ext))

    gender = profile.get("gender", "unknown")
    visual_identity = profile.get("visual_identity", "")
    appearance = profile.get("appearance_description", [])
    if isinstance(appearance, str):
        appearance = [appearance]
    book_desc = "\n".join(f"  - {s}" for s in appearance[:3]) if appearance else "Design a friendly, memorable character."

    # Species-faithful, same rule as the sheet prompt: never force human. The
    # APPEARANCE is the ground truth; the _is_non_human hint only adds emphasis.
    faithful = ("- Render EXACTLY as the APPEARANCE describes — animals/plants/objects "
                "as their TRUE form, never humanized; a person as a person.")
    if _is_non_human(profile):
        faithful += (" This character IS an animal/creature — its real animal form, "
                     "expressive, NOT a human (no human body/clothing unless the book says so).")
    rules_block = "\n".join([
        "- ONLY this one character, nothing else.",
        "- Front-facing, looking at the viewer.",
        "- Friendly, expressive face with big eyes.",
        faithful,
        "- Period-accurate clothing IF the character is clothed (NOT modern; animals unclothed unless the book says otherwise).",
        "- Clean, simple composition.",
        "- Do NOT add any text, labels, or names to the image.",
    ])

    prompt = f"""Children's picture book character portrait.

Draw a SINGLE character: {name} ({gender}).
Front-facing, head and upper body, centered in the image.
Clean white background, no other characters or objects.

APPEARANCE:
{book_desc}

{f"VISUAL IDENTITY: {visual_identity}" if visual_identity else ""}

RULES:
{rules_block}

Style: {style}
Do NOT include: {NEGATIVE_PROMPT}"""

    from src.gemini_backend import call_gemini_with_backoff, note_gen_failure

    def _attempt() -> str:
        response = client.models.generate_content(
            model=GEMINI_IMAGE_MODEL,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
                image_config=genai.types.ImageConfig(aspect_ratio="1:1"),
            ),
        )
        return save_inline_image(response, portrait_path)

    try:
        final = call_gemini_with_backoff(_attempt, max_retries=2, label=name)
    except Exception as e:
        logger.warning("Portrait generation for '%s' failed: %s", name, e)
        note_gen_failure(e)
        return ""
    if final:
        logger.info("Portrait for '%s' saved to %s", name, final)
        return final
    return ""


def generate_character_sheets(
    character_profiles: list[dict],
    book_id: str,
    style: str | None = None,
    max_characters: int = 0,
    correction_feedback: str = "",
    force: bool = False,
) -> list[dict]:
    """Generate character portraits + reference sheets.

    Two-step process:
    1. Generate portrait (simple front-facing head shot) — used as avatar
    2. Generate full character sheet (multi-angle, expressions) — used as reference for illustrations

    The portrait is passed as a visual reference to the sheet generation for consistency.
    """
    client = _get_client()
    active_style = style or DEFAULT_STYLE
    output_dir = GENERATED_DIR / book_id / "characters"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Filter to real characters. (No caller ever sets mention_count, so the old
    # `mention_count >= 5` condition always failed and silently capped at 10.)
    main_chars = [
        p for p in character_profiles
        if p.get("role") in ("main", "supporting")
    ]
    if not main_chars:
        main_chars = list(character_profiles)
    if max_characters:
        main_chars = main_chars[:max_characters]

    logger.info("Generating portraits + sheets for %d characters", len(main_chars))
    if not main_chars:
        return []

    main_chars = _assign_visual_identities(main_chars)
    results: list[dict] = []

    for profile in main_chars:
        name = profile.get("name", "Character")
        safe_name = _safe_filename(name)

        # Step 1: Generate portrait
        portrait_path = _generate_portrait(client, profile, output_dir, active_style, force=force)

        # Step 2: Generate full sheet (with portrait as reference)
        save_path = output_dir / f"{safe_name}_sheet"
        sheet_path = ""

        # Reuse an existing sheet ONLY when neither force-regenerating nor
        # running a quality-feedback retry (both must redraw over the existing
        # sheet). force guards the serverless localize race (see _generate_portrait).
        if not correction_feedback and not force:
            for ext in (".png", ".jpg"):
                if save_path.with_suffix(ext).exists():
                    sheet_path = str(save_path.with_suffix(ext))
                    break

        if not sheet_path:
            prompt = _build_sheet_prompt(profile, active_style, all_profiles=main_chars)
            if correction_feedback:
                prompt += (
                    "\n\nIMPORTANT — a previous version of this sheet failed quality review. "
                    f"Fix these specific issues while keeping the character's identity:\n{correction_feedback}"
                )
            import base64
            contents: list = []
            # Anchor the sheet's art style to the book cover — the single
            # book-wide style reference scenes and pages also use — so every
            # character matches the cover's look instead of drifting per regen.
            from src.generation.special_pages import get_style_ref
            cover_path = get_style_ref(book_id)
            if cover_path:
                try:
                    _cdata = Path(cover_path).read_bytes()
                    _cmime = "image/png" if str(cover_path).endswith(".png") else "image/jpeg"
                    contents.append({"text": "[STYLE REFERENCE — match this art style, colors, line quality, and overall look]"})
                    contents.append({"inline_data": {"mime_type": _cmime, "data": base64.b64encode(_cdata).decode()}})
                except Exception:
                    pass
            if portrait_path:
                try:
                    img_data = Path(portrait_path).read_bytes()
                    mime = "image/png" if portrait_path.endswith(".png") else "image/jpeg"
                    contents.append({"text": f"[REFERENCE PORTRAIT of {name}] — Match this character's face, hair, outfit EXACTLY in all views below."})
                    contents.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(img_data).decode()}})
                except Exception:
                    pass
            contents.append({"text": prompt})

            from src.gemini_backend import call_gemini_with_backoff, note_gen_failure

            def _attempt() -> str:
                response = client.models.generate_content(
                    model=GEMINI_IMAGE_MODEL,
                    contents=contents,
                    config=genai.types.GenerateContentConfig(
                        response_modalities=["TEXT", "IMAGE"],
                        image_config=genai.types.ImageConfig(aspect_ratio="1:1"),
                    ),
                )
                return save_inline_image(response, save_path)

            try:
                # Shared backoff: fails fast on a free-tier/zero-quota key
                # instead of the old blind sleep(2).
                sheet_path = call_gemini_with_backoff(_attempt, max_retries=2, label=name) or sheet_path
            except Exception as e:
                logger.warning("Sheet generation for '%s' failed: %s", name, e)
                note_gen_failure(e)

            if sheet_path:
                # Durable storage + register as a pickable version.
                try:
                    from src.core.storage import record_image_version
                    _sb = Path(sheet_path).read_bytes()
                    _sct = "image/png" if str(sheet_path).endswith(".png") else "image/jpeg"
                    record_image_version(book_id, "character", name, _sb, content_type=_sct)
                except Exception as _e:
                    logger.warning("character version record failed: %s", _e)

        role = profile.get("role", "unknown")
        results.append({
            "character_name": name,
            "sheet_path": sheet_path,
            "portrait_path": portrait_path,
            "role": role,
            "visual_identity": profile.get("visual_identity", ""),
            "visual_colors": profile.get("visual_colors", ""),
        })

    return results
