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

## Target Audience (ages 4-6)
- 200-400 words per chapter, simple sentences, predictable story patterns

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
Rewrite this text into {num_pages} page(s) of a children's picture book for ages 4-6.

## Writing Rules
- Language: {language_instruction}
- Each page: 1-3 sentences (max 50 words per page)
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
    original_text: str = "",
    language: str = "en",
    characters: list[dict] | None = None,
    character_sheets: list[dict] | None = None,
) -> list[dict]:
    """Rewrite scenes into picture book text (target: ages 4-6) using LLM.

    Each scene's original_text is passed directly — no separate book context needed.
    character_sheets provides visual identity info for scene_direction.
    """
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
            # Pass character_sheets through — omitting it dropped every VISUAL:
            # line from the per-page prompt in real (multi-page) generation.
            # ISOLATE per-page failures: the LLM occasionally returns invalid
            # JSON (markdown wrapper, truncation, refusal). Before this, that
            # single raise propagated all the way up and crashed the ENTIRE
            # chapter at the Writer stage — so the text never updated. Retry the
            # page once, then fall back to a summary (marked simplify_failed so a
            # later re-gen can retry it) and KEEP GOING.
            result = None
            for attempt in range(2):
                try:
                    result = simplify_text([scene], original_text, language, characters, character_sheets)
                    break
                except Exception as e:
                    logger.warning("Page %d simplify attempt %d failed: %s", i + 1, attempt + 1, e)
            if not result:
                fallback_text = (scene.get("scene_summary")
                                 or scene.get("original_text", scene.get("text", "")))[:300]
                result = [{**scene, "page_text": fallback_text,
                           "scene_direction": scene.get("scene_summary", ""),
                           "simplify_failed": True}]
            # Keep the scene's real page number — sequential renumbering
            # breaks partial runs (e.g. --pages 13,29 became pages 4,5)
            result[0]["page_number"] = scene.get("page_number", i + 1)
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
        language_instruction=language_instruction,
        scenes_json=json.dumps(scenes_data, indent=2, default=str, ensure_ascii=False),
        characters_info=chars_info,
    )

    # Imported here (not at module load) so a test patching
    # src.llm_client.generate_json reaches it — matching pipeline.py's pattern.
    from src.llm_client import generate_json
    result = generate_json(prompt, system=SYSTEM_INSTRUCTION)
    pages = result.get("pages", [])

    # Safety: enforce page count matches input scenes
    if len(pages) > len(scenes):
        logger.warning("LLM returned %d pages but only %d scenes — truncating", len(pages), len(scenes))
        pages = pages[:len(scenes)]

    # Merge back into scene dicts
    page_map = {p.get("page_number", i + 1): p for i, p in enumerate(pages)}
    output = []
    failed = 0
    for i, scene in enumerate(scenes):
        page_data = page_map.get(i + 1, page_map.get(scene.get("page_number", -1), {}))
        llm_text = page_data.get("page_text") if isinstance(page_data, dict) else None
        # No LLM page for this scene → the fallback is the UNSIMPLIFIED summary.
        # Mark it failed (same discipline as Layer-6 annotation) instead of
        # passing an adult-prose page off as a clean kids' page.
        simplified = bool(llm_text)
        page_text = llm_text or scene.get("scene_summary", "")
        scene_direction = page_data.get("scene_direction") or scene.get("scene_summary", "")

        entry = {
            **scene,
            # Keep the scene's real page number — `i + 1` is always 1 on the
            # single-scene path and clobbered partial runs (e.g. --pages 13).
            "page_number": scene.get("page_number", i + 1),
            "page_text": page_text,
            "scene_direction": scene_direction,
            "word_count": len(page_text.split()),
        }
        if not simplified:
            entry["simplify_failed"] = True
            failed += 1
        output.append(entry)

    if failed:
        logger.warning("Rewrote %d pages — %d had NO LLM simplification (fell back to summary)",
                       len(output), failed)
    else:
        logger.info("Rewrote %d pages", len(output))
    return output
