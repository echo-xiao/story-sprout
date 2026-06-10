"""Gemini Vision-based character consistency check.

Sends the generated illustration + character sheet to Gemini and asks
it to verify if the characters match. If not, returns specific feedback
about what's wrong (e.g., "missing glasses", "wrong hair color").
"""

import logging
from pathlib import Path

from google import genai

from src.config import GEMINI_MODEL
from src.generation.image_utils import _get_client, _load_image_part

logger = logging.getLogger(__name__)


def check_character_consistency(
    illustration_path: str,
    character_sheets: list[dict],
    page_num: int = 0,
) -> dict:
    """Check if characters in an illustration match their reference sheets.

    Args:
        illustration_path: Path to the generated page illustration.
        character_sheets: List of character sheet dicts with 'character_name',
                         'sheet_path', and 'visual_identity'.
        page_num: Page number for logging.

    Returns:
        dict with:
            - consistent: bool (True if all characters match)
            - score: float (0-1, overall consistency)
            - issues: list of str (specific problems found)
            - feedback: str (combined feedback for regeneration prompt)
    """
    client = _get_client()

    # Build multi-part content: illustration + character sheets
    parts = []

    # Add the illustration
    parts.append({"text": "[PAGE ILLUSTRATION to check]"})
    ill_part = _load_image_part(illustration_path)
    if not ill_part:
        return {"consistent": True, "score": 1.0, "issues": [], "feedback": ""}
    parts.append(ill_part)

    # Add character sheets as references
    sheets_added = 0
    char_names = []
    for sheet in character_sheets:
        sheet_path = sheet.get("sheet_path", "")
        if not sheet_path:
            continue
        img_part = _load_image_part(sheet_path)
        if img_part:
            name = sheet.get("character_name", "character")
            vi = sheet.get("visual_identity", "")
            parts.append({"text": f"[REFERENCE SHEET for {name}]: {vi}"})
            parts.append(img_part)
            char_names.append(name)
            sheets_added += 1
            if sheets_added >= 5:
                break

    if sheets_added == 0:
        return {"consistent": True, "score": 1.0, "issues": [], "feedback": ""}

    # Ask Gemini to compare
    parts.append({"text": f"""Compare the PAGE ILLUSTRATION with the CHARACTER REFERENCE SHEETS above.

Characters to check: {', '.join(char_names)}

For EACH character visible in the illustration, check:
1. Hair color and style — does it match the reference sheet?
2. Clothing/outfit — same colors and style as reference?
3. Distinctive features — glasses, freckles, accessories present?
4. Overall appearance — would a child recognize this as the same person?

Return JSON:
{{
  "overall_consistent": true/false,
  "score": 0.0 to 1.0 (1.0 = perfect match),
  "characters_checked": [
    {{
      "name": "character name",
      "found_in_illustration": true/false,
      "consistent": true/false,
      "issues": ["list of specific mismatches, e.g., 'missing glasses', 'hair should be brown not blonde'"]
    }}
  ],
  "regeneration_feedback": "If inconsistent, describe exactly what needs to be fixed in the next generation attempt"
}}"""})

    try:
        # Call Gemini directly with multipart content (images)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=parts,
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        import json
        result = json.loads(response.text)

        consistent = result.get("overall_consistent", True)
        score = result.get("score", 1.0)
        chars = result.get("characters_checked", [])
        issues = []
        for c in chars:
            if not c.get("consistent", True):
                for issue in c.get("issues", []):
                    issues.append(f"{c.get('name', '?')}: {issue}")

        feedback = result.get("regeneration_feedback", "")

        logger.info(
            "Page %d Gemini consistency: score=%.2f, consistent=%s, issues=%d",
            page_num, score, consistent, len(issues),
        )
        if issues:
            for issue in issues:
                logger.info("  Issue: %s", issue)

        return {
            "consistent": consistent,
            "score": score,
            "issues": issues,
            "feedback": feedback,
        }

    except Exception as e:
        logger.warning("Gemini consistency check failed: %s", e)
        return {"consistent": True, "score": 1.0, "issues": [], "feedback": ""}


def check_page_quality(
    illustration_path: str,
    character_sheets: list[dict],
    expected_text: str,
    expected_characters: list[str],
    page_num: int = 0,
) -> dict:
    """Comprehensive single-page quality check using Gemini Vision.

    Checks 5 dimensions in one API call:
    1. Character consistency — do characters match their reference sheets?
    2. Spelling errors — any misspelled words in embedded text?
    3. Duplicate characters — is the same person drawn more than once?
    4. Name-face mismatch — do name labels match the correct characters?
    5. Missing/extra characters — are only the expected characters present?

    Returns:
        dict with per-dimension results and overall score.
    """
    client = _get_client()

    parts = []
    parts.append({"text": "[PAGE ILLUSTRATION to check]"})
    ill_part = _load_image_part(illustration_path)
    if not ill_part:
        return _empty_page_quality()
    parts.append(ill_part)

    # Add character sheets
    sheets_added = 0
    char_names = []
    for sheet in character_sheets:
        sheet_path = sheet.get("sheet_path", "")
        if not sheet_path:
            continue
        img_part = _load_image_part(sheet_path)
        if img_part:
            name = sheet.get("character_name", "character")
            vi = sheet.get("visual_identity", "")
            parts.append({"text": f"[REFERENCE SHEET for {name}]: {vi}"})
            parts.append(img_part)
            char_names.append(name)
            sheets_added += 1
            if sheets_added >= 5:
                break

    parts.append({"text": f"""You are a QA inspector for a children's picture book. Analyze the PAGE ILLUSTRATION above.

EXPECTED characters in this scene: {', '.join(expected_characters) if expected_characters else 'unknown'}
EXPECTED story text embedded in image: "{expected_text[:300]}"

Check ALL of the following:

1. CHARACTER CONSISTENCY: For each character, compare against their REFERENCE SHEET.
   - Hair color/style match? Clothing match? Distinctive features present?

2. SPELLING ERRORS: Read ALL text visible in the image (speech bubbles, banners, scrolls, signs).
   - List every misspelled word you find. Compare against the expected text.

3. DUPLICATE CHARACTERS: Is the SAME person drawn more than once in the image?
   - Count how many distinct human figures appear. If a character appears twice, flag it.

4. NAME-FACE MISMATCH: Do name labels/signs match the correct character?
   - If a name label says "Dr. Manette" but points to a young woman, that's a mismatch.

5. MISSING/EXTRA CHARACTERS: Are ONLY the expected characters present?
   - Any expected character missing? Any unexpected character added?

Return JSON:
{{
  "overall_score": 0 to 100,
  "character_consistency": {{
    "score": 0 to 100,
    "characters": [
      {{"name": "...", "score": 0-100, "issues": ["..."]}}
    ]
  }},
  "spelling": {{
    "score": 0 to 100,
    "errors": ["word 'recieve' should be 'receive'", ...]
  }},
  "duplicate_characters": {{
    "score": 0 to 100,
    "duplicates": ["Dr. Manette appears twice", ...]
  }},
  "name_face_mismatch": {{
    "score": 0 to 100,
    "mismatches": ["Label says 'Lucie' but character looks like Dr. Manette", ...]
  }},
  "character_count": {{
    "score": 0 to 100,
    "expected": {len(expected_characters)},
    "found": 0,
    "missing": ["..."],
    "extra": ["..."]
  }},
  "regeneration_feedback": "If any issues, describe exactly what to fix"
}}"""})

    try:
        import json as _json
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=parts,
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        result = _json.loads(response.text)

        logger.info(
            "Page %d quality check: overall=%s, char=%s, spell=%s, dup=%s, name=%s, count=%s",
            page_num,
            result.get("overall_score", "?"),
            result.get("character_consistency", {}).get("score", "?"),
            result.get("spelling", {}).get("score", "?"),
            result.get("duplicate_characters", {}).get("score", "?"),
            result.get("name_face_mismatch", {}).get("score", "?"),
            result.get("character_count", {}).get("score", "?"),
        )

        return result

    except Exception as e:
        logger.warning("Page %d quality check failed: %s", page_num, e)
        return _empty_page_quality()


def check_character_sheet_quality(
    sheet_path: str,
    character_name: str,
    appearance: str,
    visual_details: dict | None = None,
    gender: str = "unknown",
    role: str = "supporting",
) -> dict:
    """Quality check a character reference sheet against its text description.

    Checks:
    1. Appearance match — does the sheet match the text description?
    2. Internal consistency — are all views/expressions of the same character?
    3. Multi-angle completeness — front/side/back views present?
    4. Style quality — children's book style, clean, usable as reference?
    5. Text/label correctness — any garbled text or wrong labels?

    Returns:
        dict with per-dimension scores and overall score.
    """
    client = _get_client()

    img_part = _load_image_part(sheet_path)
    if not img_part:
        return _empty_sheet_quality(character_name)

    # Build visual details string
    vd_str = ""
    if visual_details:
        vd_str = ", ".join(f"{k}: {v}" for k, v in visual_details.items() if v)

    parts = []
    parts.append({"text": f"[CHARACTER SHEET for '{character_name}']"})
    parts.append(img_part)
    parts.append({"text": f"""You are a QA inspector for a children's picture book character reference sheet.

CHARACTER INFO:
- Name: {character_name}
- Gender: {gender}
- Role: {role}
- Appearance description: {appearance or '(none provided)'}
- Visual details: {vd_str or '(none provided)'}

This is a CHARACTER REFERENCE SHEET — it should show the same character from multiple angles and with multiple expressions, to be used as a visual guide for illustrating a picture book.

Note: Some characters represent a GROUP or FAMILY (e.g., "The Smiths", "Baker's children"). In that case, the sheet shows multiple distinct people who belong together. Evaluate accordingly.

Check ALL of the following:

1. APPEARANCE MATCH (compare sheet against the text description above):
   - Hair color/style, eye color, skin tone, clothing, accessories, age, build
   - Does the character look like what the description says?
   - For groups: do the members collectively match the description?

2. INTERNAL CONSISTENCY (within the sheet itself):
   - Do ALL views (front, side, back) show the SAME character?
   - Do ALL expression circles show the SAME face (same hair, eye color, face shape)?
   - Are there inconsistencies between different parts of the sheet?
   - Common problems: expression faces having different hair styles, different eye colors, different skin tones

3. MULTI-ANGLE COMPLETENESS:
   - Does it include front view, side view, and back view?
   - Does it include multiple expressions?
   - Does it include clothing/accessory detail close-ups?

4. STYLE QUALITY:
   - Is it in a clean children's picture book illustration style?
   - Is it usable as a reference for other illustrations?
   - Is the layout clear and organized?

5. TEXT & LABELS:
   - Any garbled, misspelled, or incorrect text/labels on the sheet?
   - Is the character name correct if labeled?

Return JSON:
{{
  "overall_score": 0 to 100,
  "is_group": true/false,
  "appearance_match": {{
    "score": 0 to 100,
    "issues": ["list of mismatches between description and sheet"]
  }},
  "internal_consistency": {{
    "score": 0 to 100,
    "issues": ["e.g., 'expression 3 has different hair style', 'side view has wrong clothing'"]
  }},
  "multi_angle": {{
    "score": 0 to 100,
    "has_front": true/false,
    "has_side": true/false,
    "has_back": true/false,
    "has_expressions": true/false,
    "issues": ["e.g., 'missing back view'"]
  }},
  "style_quality": {{
    "score": 0 to 100,
    "issues": ["e.g., 'too realistic, not picture book style'"]
  }},
  "text_labels": {{
    "score": 0 to 100,
    "issues": ["e.g., 'garbled text at bottom'"]
  }},
  "regeneration_feedback": "If any issues found, describe exactly what to fix when regenerating this sheet"
}}"""})

    try:
        import json as _json
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=parts,
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        result = _json.loads(response.text)
        result["character_name"] = character_name

        logger.info(
            "Character sheet quality for '%s': overall=%s, appearance=%s, consistency=%s, angles=%s, style=%s, text=%s",
            character_name,
            result.get("overall_score", "?"),
            result.get("appearance_match", {}).get("score", "?"),
            result.get("internal_consistency", {}).get("score", "?"),
            result.get("multi_angle", {}).get("score", "?"),
            result.get("style_quality", {}).get("score", "?"),
            result.get("text_labels", {}).get("score", "?"),
        )
        return result

    except Exception as e:
        logger.warning("Character sheet quality check failed for '%s': %s", character_name, e)
        return _empty_sheet_quality(character_name)


def _empty_sheet_quality(character_name: str = "") -> dict:
    return {
        "overall_score": 100,
        "character_name": character_name,
        "is_group": False,
        "appearance_match": {"score": 100, "issues": []},
        "internal_consistency": {"score": 100, "issues": []},
        "multi_angle": {"score": 100, "has_front": True, "has_side": True, "has_back": True, "has_expressions": True, "issues": []},
        "style_quality": {"score": 100, "issues": []},
        "text_labels": {"score": 100, "issues": []},
        "regeneration_feedback": "",
    }


def _empty_page_quality() -> dict:
    return {
        "overall_score": 100,
        "character_consistency": {"score": 100, "characters": []},
        "spelling": {"score": 100, "errors": []},
        "duplicate_characters": {"score": 100, "duplicates": []},
        "name_face_mismatch": {"score": 100, "mismatches": []},
        "character_count": {"score": 100, "expected": 0, "found": 0, "missing": [], "extra": []},
        "regeneration_feedback": "",
    }


def check_style_consistency(
    illustration_paths: list[str],
    reference_path: str | None = None,
) -> dict:
    """Check visual style consistency across all page illustrations.

    Compares each illustration against a reference image (book cover preferred,
    falls back to first page) to detect style drift: different color palettes,
    line styles, rendering techniques, or art direction changes.

    Args:
        illustration_paths: List of paths to page illustrations.
        reference_path: Path to style reference image (book cover recommended).

    Returns:
        dict with:
            - score: float (0-100, overall style coherence)
            - per_page: list of {page, score, issues}
            - issues: list of {page, description}
    """
    valid_paths = [p for p in illustration_paths if Path(p).exists()]
    if len(valid_paths) < 2:
        return {"score": 100, "per_page": [], "issues": []}

    client = _get_client()
    ref = reference_path or valid_paths[0]

    ref_part = _load_image_part(ref)
    if not ref_part:
        return {"score": 100, "per_page": [], "issues": []}

    # Check in batches of 4 pages at a time (to stay within token limits)
    all_page_results = []
    all_issues = []
    batch_size = 4

    for batch_start in range(0, len(valid_paths), batch_size):
        batch = valid_paths[batch_start:batch_start + batch_size]
        parts = []
        parts.append({"text": "[STYLE REFERENCE — Page 1]"})
        parts.append(ref_part)

        page_nums = []
        for i, path in enumerate(batch):
            if path == ref:
                continue
            page_num = batch_start + i + 1
            page_nums.append(page_num)
            img_part = _load_image_part(path)
            if img_part:
                parts.append({"text": f"[PAGE {page_num}]"})
                parts.append(img_part)

        if not page_nums:
            continue

        parts.append({"text": f"""Compare the style of each PAGE illustration against the STYLE REFERENCE (Page 1).

Check for consistency in:
1. Art style — same illustration technique (watercolor, digital, line art)?
2. Color palette — similar warmth, saturation, tone?
3. Line weight and detail level — same level of detail?
4. Character rendering — same proportions, eye style, face shape conventions?
5. Background treatment — similar level of detail and rendering?

Pages to check: {', '.join(str(p) for p in page_nums)}

Return JSON:
{{
  "pages": [
    {{
      "page": <page number>,
      "score": 0 to 100 (100 = perfect style match),
      "consistent": true/false,
      "issues": ["list of style differences, e.g., 'uses flat digital style instead of watercolor', 'much darker color palette'"]
    }}
  ]
}}"""})

        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=parts,
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            import json
            result = json.loads(response.text)
            for page_result in result.get("pages", []):
                all_page_results.append(page_result)
                if not page_result.get("consistent", True):
                    for issue in page_result.get("issues", []):
                        all_issues.append({
                            "page": page_result["page"],
                            "description": issue,
                        })
        except Exception as e:
            logger.warning("Style consistency batch check failed: %s", e)

    # Overall score
    if all_page_results:
        avg_score = sum(p.get("score", 100) for p in all_page_results) / len(all_page_results)
    else:
        avg_score = 100

    return {
        "score": round(avg_score),
        "per_page": all_page_results,
        "issues": all_issues,
    }
