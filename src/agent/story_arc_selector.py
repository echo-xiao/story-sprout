"""Story arc selection: LLM-powered scene selection for narrative coherence.

The NLP scene_selector picks "important" segments, but doesn't guarantee
a coherent story. This module uses Gemini to:
1. Identify the main storyline from the full text
2. Select scenes that form a clear narrative arc (beginning -> conflict -> climax -> resolution)
3. Ensure a reader can understand "what happened" after reading the picture book

This replaces the pure NLP scoring for the final scene selection, while still
using NLP scores as input signals.
"""

import json
import logging
from typing import Any

from src.agent.gemini_client import generate_json
from src.config import STORY_TEMPLATES

logger = logging.getLogger(__name__)

STORY_ARC_SYSTEM = """\
You are a story analyst specializing in adapting complex novels into children's picture books.
Your job is to identify the MAIN STORYLINE and select scenes that form a clear, coherent narrative.

Key principles:
- A picture book tells ONE clear story, not a summary of everything
- Every page should advance the plot — no filler, no tangents
- The reader must understand what happened after reading all pages
- Focus on the EMOTIONAL journey of the main character(s)
- Simplify subplots: keep only what serves the main story
- The story needs: setup -> problem/conflict -> rising action -> climax -> resolution
"""

STORY_ARC_PROMPT = """\
Analyze this book and select exactly {num_pages} scenes that form a COHERENT STORY for a children's picture book.

## The Book
Title: {title}
Characters: {characters}
Number of chapters: {num_chapters}

## Available Segments (with NLP importance scores)
{segments_summary}

## Story Template: {template}
Expected beats: {beats}

## Rules
1. Select exactly {num_pages} segments by their IDs
2. The selected scenes MUST tell a complete story with beginning, middle, and end
3. Each scene must ADVANCE the plot — no repetition, no filler
4. A child (age {age_group}) should understand the story after hearing all pages
5. Focus on ONE main storyline — drop subplots that don't serve it
6. Prefer scenes with action, dialogue, and emotional moments
7. The scenes should flow naturally: each leads to the next
8. If the book has multiple chapters, try to cover the FULL arc across chapters, not just one chapter

## Output
Return JSON:
{{
  "main_storyline": "One sentence describing the story arc you chose",
  "selected_segment_ids": [list of segment IDs in narrative order],
  "per_scene_role": [
    {{"segment_id": id, "role": "setup/conflict/rising/climax/falling/resolution", "why": "brief reason"}}
  ]
}}
"""


def select_story_arc(
    analysis: dict[str, Any],
    num_pages: int,
    age_group: str = "4-6",
    template: str = "classic",
    title: str = "Untitled",
) -> list[dict]:
    """Select scenes using LLM to ensure narrative coherence.

    Uses NLP analysis data as input but lets the LLM choose which scenes
    form the best story arc.

    Args:
        analysis: Full analysis dict from analyze_text.
        num_pages: Number of pages to select.
        age_group: Target age group.
        template: Story template (classic/journey/simple).
        title: Book title for context.

    Returns:
        List of scene dicts, one per page, in narrative order.
    """
    segments = analysis.get("segments", [])
    characters = analysis.get("characters", [])
    key_events = analysis.get("key_events", [])
    sentiment = analysis.get("sentiment", {})

    if not segments:
        logger.warning("No segments for story arc selection")
        return []

    # Build a compact summary of each segment for the LLM
    segments_summary = _build_segments_summary(segments, characters, key_events, sentiment)

    # Character info
    char_info = ", ".join(
        f"{c['name']} ({c.get('role', 'unknown')})"
        for c in characters[:8]
    )

    template_config = STORY_TEMPLATES.get(template, STORY_TEMPLATES["classic"])
    beats = ", ".join(template_config["structure"])

    # Count chapters from segment titles or other hints
    chapter_titles = set()
    for seg in segments:
        t = seg.get("title", "")
        if t and ("chapter" in t.lower() or len(t) < 60):
            chapter_titles.add(t)
    num_chapters = max(len(chapter_titles), 1)

    prompt = STORY_ARC_PROMPT.format(
        num_pages=num_pages,
        title=title,
        characters=char_info or "Unknown characters",
        segments_summary=segments_summary,
        template=template,
        beats=beats,
        age_group=age_group,
        num_chapters=num_chapters,
    )

    result = generate_json(prompt, system_instruction=STORY_ARC_SYSTEM)

    selected_ids = result.get("selected_segment_ids", [])
    main_storyline = result.get("main_storyline", "")
    per_scene_roles = result.get("per_scene_role", [])

    logger.info("Story arc: %s", main_storyline)
    logger.info("Selected %d segments", len(selected_ids))

    # Build scene dicts from selected segment IDs
    segment_map = {seg.get("id", i): seg for i, seg in enumerate(segments)}
    role_map = {r["segment_id"]: r for r in per_scene_roles if isinstance(r, dict)}

    scenes = []
    for page_num, seg_id in enumerate(selected_ids, 1):
        seg = segment_map.get(seg_id)
        if seg is None:
            # Try integer conversion
            try:
                seg = segment_map.get(int(seg_id))
            except (ValueError, TypeError):
                pass
        if seg is None:
            logger.warning("Segment ID %s not found, skipping", seg_id)
            continue

        role_info = role_map.get(seg_id, {})

        # Extract characters present in this segment
        text_lower = seg.get("text", "").lower()
        present_chars = [
            c["name"] for c in characters[:10]
            if c["name"].lower() in text_lower
        ]

        # Scene summary: first 2 sentences
        sentences = [s.strip() for s in seg.get("text", "").replace("\n", " ").split(".") if s.strip()]
        summary = ". ".join(sentences[:2]) + "." if sentences else seg.get("text", "")[:200]

        scenes.append({
            "page_number": page_num,
            "source_segment_id": seg_id,
            "template_beat": role_info.get("role", f"scene_{page_num}"),
            "narrative_role": role_info.get("why", ""),
            "scene_summary": summary[:300],
            "emotional_tone": _infer_tone(seg, sentiment),
            "key_characters": present_chars[:5],
            "main_storyline": main_storyline,
            "original_text": seg.get("text", ""),
        })

    # If LLM returned fewer than requested, that's OK - don't pad with filler
    if len(scenes) < num_pages:
        logger.warning("Story arc selected %d/%d scenes", len(scenes), num_pages)

    return scenes


def _build_segments_summary(
    segments: list[dict],
    characters: list[dict],
    key_events: list[dict],
    sentiment: dict,
) -> str:
    """Build a compact segment summary for the LLM prompt."""
    char_names = [c["name"].lower() for c in characters[:10]]
    event_seg_ids = {e.get("segment_id", -1) for e in key_events}
    sentiment_scores = sentiment.get("scores", [])

    lines = []
    for i, seg in enumerate(segments):
        seg_id = seg.get("id", i)
        text = seg.get("text", "")
        words = text.split()

        if len(words) < 15:
            continue

        # First sentence as preview
        first_sentence = text.replace("\n", " ").split(".")[0][:150]

        # Characters present
        text_lower = text.lower()
        chars_here = [n for n in char_names if n in text_lower]

        # Sentiment
        sent_val = sentiment_scores[i] if i < len(sentiment_scores) else 0.0

        # Key event marker
        is_key = "KEY_EVENT" if seg_id in event_seg_ids else ""

        line = (
            f"[ID:{seg_id}] {first_sentence}... "
            f"| chars: {','.join(chars_here[:3]) or 'none'} "
            f"| sentiment: {sent_val:+.2f} "
            f"{is_key}"
        )
        lines.append(line)

    return "\n".join(lines)


def _infer_tone(seg: dict, sentiment: dict) -> str:
    """Infer emotional tone from segment position and sentiment."""
    scores = sentiment.get("scores", [])
    seg_id = seg.get("id", 0)
    val = scores[seg_id] if seg_id < len(scores) else 0.0

    if val > 0.3:
        return "joyful"
    elif val > 0.1:
        return "hopeful"
    elif val > -0.1:
        return "neutral"
    elif val > -0.3:
        return "worried"
    else:
        return "sad"
