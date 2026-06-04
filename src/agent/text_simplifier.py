"""Text simplification: LLM-powered rewriting into children's picture book narration.

NLP handles analysis and scene selection. This module uses LLM (Gemini) to
creatively rewrite selected scenes into picture book format with:
- Warm narrator voice (not lecturing, not preachy)
- Natural character dialogue
- Simple but not dumbed-down language
- Each page: 1-2 short sentences + optional dialogue
- Story flows naturally across pages
"""

import json
import logging

from src.config import AGE_PRESETS
from src.agent.gemini_client import generate_json

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """\
You are a talented children's picture book author. You write stories that \
children LOVE to hear read aloud, and that parents enjoy reading.

Your writing style:
- Warm and playful, NEVER preachy or lecturing
- You SHOW, don't tell. No moral lessons shoved in the reader's face.
- Short sentences with rhythm and flow
- Mix narration with character dialogue
- Use sounds, emotions, and actions — not explanations
- Each page should feel like a little moment, not a summary
- The story should make kids laugh, gasp, or feel something

NEVER write things like:
- "And the lesson is..."
- "This teaches us that..."
- "Remember, children..."
- "The moral of the story..."
"""

REWRITE_PROMPT = """\
Rewrite these story scenes into a {num_pages}-page children's picture book \
for ages {age_group}.

## Rules
- Language: {language_instruction}
- Each page: 1-3 short sentences (max {max_words} words total per page)
- Mix narrator voice and character dialogue naturally
- Use onomatopoeia and exclamations where fun ("Splash!", "Oh no!")
- Make it FEEL like a story, not a summary of a book
- Do NOT be preachy. No lessons, no morals stated explicitly.
- The story should flow — each page leads naturally to the next
- Give characters personality through HOW they talk, not what they say about themselves
- CRITICAL: Stay faithful to the original text. Do NOT invent new events, objects, \
or characters that are not in the original. Adapt what's there, don't make things up.
- If the original scene has dialogue, try to keep the essence of that dialogue.

## Original Story Context (adapt freely, don't copy)
{original_text}

## Selected Scenes to Adapt
{scenes_json}

## Characters
{characters_info}

## Output
Return JSON: {{"pages": [
  {{"page_number": 1, "page_text": "...", "scene_direction": "brief description of what to illustrate"}},
  ...
]}}

Each page_text should be the COMPLETE text for that page — narration + dialogue mixed together.
Each scene_direction should describe the visual scene (characters, setting, action, emotion) for the illustrator.
"""


def simplify_text(
    scenes: list[dict],
    age_group: str,
    original_text: str,
    language: str = "en",
    characters: list[dict] | None = None,
) -> list[dict]:
    """Rewrite scenes into picture book text using LLM.

    Uses Gemini to creatively adapt the selected scenes into
    narrator voice + dialogue format suitable for children.
    """
    if age_group not in AGE_PRESETS:
        age_group = "4-6"
    preset = AGE_PRESETS[age_group]

    # Normalize scenes
    normalized = []
    for s in scenes:
        if isinstance(s, str):
            s = {"page_number": len(normalized) + 1, "scene_summary": s}
        if isinstance(s, dict):
            if "page_number" not in s:
                s["page_number"] = len(normalized) + 1
            normalized.append(s)
    scenes = normalized

    if not scenes:
        return []

    # If too many scenes, process in batches
    BATCH_SIZE = 10
    if len(scenes) > BATCH_SIZE:
        all_results = []
        for batch_start in range(0, len(scenes), BATCH_SIZE):
            batch = scenes[batch_start:batch_start + BATCH_SIZE]
            logger.info("Processing batch %d-%d of %d scenes",
                       batch_start + 1, batch_start + len(batch), len(scenes))
            batch_result = simplify_text(batch, age_group, original_text, language, characters)
            all_results.extend(batch_result)
        # Re-number pages
        for i, r in enumerate(all_results):
            r["page_number"] = i + 1
        return all_results

    # Language instruction
    lang_map = {"zh": "Chinese (简体中文)", "en": "English", "ja": "Japanese", "ko": "Korean"}
    lang_name = lang_map.get(language, language)
    if language == "en":
        language_instruction = "Write in English."
    else:
        language_instruction = (
            f"Write ALL text in {lang_name}. "
            f"The entire story must be in {lang_name}."
        )

    # Characters info
    chars_info = "No specific character info."
    if characters:
        chars = []
        for c in characters[:5]:
            name = c.get("name", "Unknown")
            role = c.get("role", "")
            traits = c.get("personality_traits", [])
            chars.append(f"- {name} ({role}): {', '.join(traits[:3])}")
        chars_info = "\n".join(chars)

    # Pass full original text of each selected scene, not just the summary
    scenes_data = []
    for s in scenes:
        full_scene_text = s.get("original_text", s.get("text", s.get("scene_summary", "")))
        scenes_data.append({
            "page": s.get("page_number"),
            "original_text": full_scene_text[:800],  # enough context, not just 1 sentence
            "scene_summary": s.get("scene_summary", "")[:200],
            "characters_present": s.get("key_characters", []),
            "emotional_tone": s.get("emotional_tone", ""),
        })

    prompt = REWRITE_PROMPT.format(
        num_pages=len(scenes),
        age_group=age_group,
        language_instruction=language_instruction,
        max_words=preset["max_words_per_page"],
        original_text=original_text[:3000],
        scenes_json=json.dumps(scenes_data, indent=2, default=str, ensure_ascii=False),
        characters_info=chars_info,
    )

    result = generate_json(prompt, system_instruction=SYSTEM_INSTRUCTION)
    pages = result.get("pages", [])

    # Merge back into scene dicts
    page_map = {p.get("page_number", i + 1): p for i, p in enumerate(pages)}
    output = []
    for i, scene in enumerate(scenes):
        page_data = page_map.get(i + 1, page_map.get(scene.get("page_number", -1), {}))
        page_text = page_data.get("page_text", scene.get("scene_summary", ""))
        scene_direction = page_data.get("scene_direction", scene.get("scene_summary", ""))

        output.append({
            **scene,
            "page_number": i + 1,
            "page_text": page_text,
            "scene_direction": scene_direction,
            "word_count": len(page_text.split()),
        })

    logger.info("Rewrote %d pages for age group %s", len(output), age_group)
    return output
