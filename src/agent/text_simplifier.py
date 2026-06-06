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
from src.llm_client import generate_json

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """\
You are an expert children's picture book author. You craft stories that \
children BEG to hear again and again, and that parents love reading aloud.

## Your Writing Style
- Warm, playful, musical — every sentence should SING when read aloud
- SHOW, don't tell. Characters learn through experience, not lectures.
- Strong rhythm and natural meter (da-DUM da-DUM patterns feel good)
- Mix narration with lively character dialogue
- Use sensory language: sounds, textures, colors, smells
- Each page = one clear visual moment with a page-turn hook

## Engagement Techniques
- REPETITION: Repeated phrases children can anticipate and say along
- ONOMATOPOEIA: Splash! Whoosh! Creak! Tap-tap-tap!
- SURPRISE & DELIGHT: Unexpected twists that make kids giggle or gasp
- EMOTIONAL CONNECTION: Characters kids care about and root for
- CUMULATIVE patterns: Building layers (like "The House That Jack Built")

## Age-Specific Guidelines
- Ages 2-4: 50-100 unique words, strong rhythm, repetitive refrains
- Ages 4-6: 200-400 words, simple sentences, predictable story patterns
- Ages 6-8: 400-800 words, longer sentences, character development, cause/effect

## Visual Storytelling
- Text and illustrations work together — don't describe what the picture shows
- Clear page-turn moments that create suspense or surprise
- Leave room for the illustration to tell part of the story

NEVER write:
- "And the lesson is..." / "This teaches us..." / "Remember, children..."
- Forced rhymes that sacrifice natural language
- Abstract concepts without concrete images
"""

REWRITE_PROMPT = """\
Rewrite this text into {num_pages} page(s) of a children's picture book for ages {age_group}.

## Writing Rules
- Language: {language_instruction}
- Each page: 1-3 sentences (max {max_words} words per page)
- Write with RHYTHM — sentences should feel musical when read aloud
- Use repetition children can join in: "Up, up, up they went. Higher and higher and higher."
- Add sound words: Creak! Whoosh! Tap-tap-tap! Splat!
- Character dialogue should sound natural and expressive
- End each page with a hook that makes kids want to turn the page
- NEVER be preachy. Show emotions through actions, not explanations.

## Faithfulness Rules
- ONLY adapt the "original_text" below. Do NOT invent new scenes or events.
- Describe EXACTLY what the original text describes.
- If a scene has "famous_quotes", preserve the EXACT original quote, then add a simple child-friendly line.
- If a scene has "previous_page_text", continue naturally from it.
- You MUST return EXACTLY {num_pages} pages.

## Text to Adapt
{scenes_json}

## Characters
{characters_info}

## Output
Return JSON: {{"pages": [
  {{"page_number": 1, "page_text": "...", "scene_direction": "describe the visual scene. For each character, include their EXACT visual description from Characters above."}},
  ...
]}}

scene_direction tips:
- Describe ONLY what happens in the original_text
- For every character, copy their VISUAL description into scene_direction
- Include character actions and expressions
- Describe the environment/setting
"""


def simplify_text(
    scenes: list[dict],
    age_group: str,
    original_text: str = "",
    language: str = "en",
    characters: list[dict] | None = None,
    character_sheets: list[dict] | None = None,
) -> list[dict]:
    """Rewrite scenes into picture book text using LLM.

    Each scene's original_text is passed directly — no separate book context needed.
    character_sheets provides visual identity info for scene_direction.
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

    # Process ONE scene at a time, passing previous page's text as context
    if len(scenes) > 1:
        all_results = []
        prev_text = ""
        for i, scene in enumerate(scenes):
            logger.info("Simplifying page %d/%d", i + 1, len(scenes))
            if prev_text:
                scene = {**scene, "previous_page_text": prev_text}
            result = simplify_text([scene], age_group, original_text, language, characters)
            if result:
                result[0]["page_number"] = i + 1
                all_results.append(result[0])
                prev_text = result[0].get("page_text", "")
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

    # Characters info — include visual identity from character sheets
    chars_info = "No specific character info."
    # Build visual identity lookup from character sheets
    visual_map = {}
    if character_sheets:
        for cs in character_sheets:
            cs_name = cs.get("character_name", "")
            vi = cs.get("visual_identity", "")
            if cs_name and vi:
                visual_map[cs_name.lower()] = vi

    if characters:
        chars = []
        for c in characters[:8]:
            name = c.get("name", "Unknown")
            role = c.get("role", "")
            traits = c.get("personality_traits", [])
            vi = visual_map.get(name.lower(), "")
            line = f"- {name} ({role}): {', '.join(traits[:3])}"
            if vi:
                line += f"\n  VISUAL: {vi}"
            chars.append(line)
        chars_info = "\n".join(chars)

    # Pass ONLY the original text + minimal context — no book/chapter names
    scenes_data = []
    for s in scenes:
        full_scene_text = s.get("original_text", s.get("text", s.get("scene_summary", "")))
        entry = {
            "page": s.get("page_number"),
            "original_text": full_scene_text,
            "characters_present": s.get("key_characters", []),
        }
        if s.get("previous_page_text"):
            entry["previous_page_text"] = s["previous_page_text"]
        if s.get("famous_quotes"):
            entry["famous_quotes"] = s["famous_quotes"]
        scenes_data.append(entry)

    prompt = REWRITE_PROMPT.format(
        num_pages=len(scenes),
        age_group=age_group,
        language_instruction=language_instruction,
        max_words=preset["max_words_per_page"],
        scenes_json=json.dumps(scenes_data, indent=2, default=str, ensure_ascii=False),
        characters_info=chars_info,
    )

    result = generate_json(prompt, system=SYSTEM_INSTRUCTION)
    pages = result.get("pages", [])

    # Safety: enforce page count matches input scenes
    if len(pages) > len(scenes):
        logger.warning("LLM returned %d pages but only %d scenes — truncating", len(pages), len(scenes))
        pages = pages[:len(scenes)]

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
