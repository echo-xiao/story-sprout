"""Scene selection using NLP scoring — no LLM.

Selects the best k segments from the analyzed text to form a picture book.
Uses a weighted importance scoring algorithm based on:
  - Sentiment peak proximity (emotional arc coverage)
  - Visual concreteness (how drawable the scene is)
  - Entity/character density (scenes with main characters rank higher)
  - Causal chain weight (events referenced by other events)
  - Story template alignment (maps to intro/problem/climax/resolution)
"""

import logging
import math
from typing import Any

from src.config import AGE_PRESETS, STORY_TEMPLATES

logger = logging.getLogger(__name__)

# Action words that indicate something visual/drawable is happening
_ACTION_WORDS = {
    "ran", "walked", "jumped", "fell", "cried", "laughed", "shouted",
    "whispered", "looked", "saw", "found", "opened", "closed", "held",
    "took", "gave", "threw", "caught", "pulled", "pushed", "climbed",
    "swam", "flew", "drove", "rode", "danced", "fought", "kissed",
    "hugged", "smiled", "waved", "pointed", "grabbed", "dropped",
    "stood", "sat", "lay", "knelt", "turned", "stopped", "started",
    "picked", "carried", "followed", "chased", "escaped", "arrived",
    "left", "entered", "crossed", "reached", "touched", "knocked",
}


def _is_metadata(text: str) -> bool:
    """Check if a segment is metadata rather than narrative."""
    text_lower = text.lower().strip()
    if any(kw in text_lower for kw in [
        "table of contents", "copyright", "all rights reserved",
        "published by", "isbn", "printed in",
    ]):
        return True
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if all(len(l) < 10 for l in lines):
        return True
    return False


# Topics not suitable for young children
_UNSAFE_KEYWORDS = {
    "race", "racial", "coloured", "negro", "white race", "supremacy",
    "killed", "murder", "blood", "death", "dead body", "corpse",
    "sex", "sexual", "naked", "drunk", "alcohol", "whiskey",
    "damn", "damned", "hell", "bastard", "suicide",
    "gun", "shot", "pistol", "rifle", "weapon",
}


def _is_child_unsafe(text: str) -> bool:
    """Check if segment contains content inappropriate for children."""
    text_lower = text.lower()
    matches = sum(1 for kw in _UNSAFE_KEYWORDS if kw in text_lower)
    return matches >= 2  # Multiple unsafe keywords = skip


def select_scenes(
    analysis: dict[str, Any],
    num_pages: int,
    age_group: str,
    template: str = "classic",
) -> list[dict]:
    """Select the best scenes from analyzed text using NLP scoring.

    No LLM is used. Scene selection is purely algorithmic.
    """
    segments = analysis.get("segments", [])
    characters = analysis.get("characters", [])
    sentiment = analysis.get("sentiment", {})
    key_events = analysis.get("key_events", [])
    visual_scores_list = analysis.get("visual_scores", [])

    if not segments:
        logger.warning("No segments to select from")
        return []

    template_config = STORY_TEMPLATES.get(template, STORY_TEMPLATES["classic"])
    structure = template_config["structure"]
    effective_pages = max(num_pages, template_config.get("min_pages", 5))

    # --- Step 1: Score each segment ---
    sentiment_scores = sentiment.get("scores", [])
    sentiment_peaks = set(sentiment.get("peaks", []))
    sentiment_valleys = set(sentiment.get("valleys", []))

    # Build character name set for main characters
    main_chars = {
        c["name"].lower()
        for c in characters
        if c.get("role") in ("main", "supporting")
    }

    # Build event segment mapping
    event_segments = {e.get("segment_id", -1) for e in key_events}

    scored_segments: list[dict] = []

    for i, seg in enumerate(segments):
        seg_text = seg.get("text", "")
        seg_id = seg.get("id", i)
        words = seg_text.split()

        # Skip very short segments
        if len(words) < 15:
            continue

        # Skip segments that look like metadata
        if _is_metadata(seg_text):
            continue

        # Skip content not suitable for children
        if _is_child_unsafe(seg_text):
            continue

        # --- Dialogue/action bonus ---
        dialogue_count = seg_text.count('"') + seg_text.count('\u201c') + seg_text.count('\u201d')
        has_dialogue = dialogue_count >= 2
        action_verbs = sum(1 for w in words if w.lower() in _ACTION_WORDS)
        dialogue_action_bonus = 0.15 * min(dialogue_count / 4, 1.0) + 0.1 * min(action_verbs / 3, 1.0)

        # --- Sentiment score ---
        # Peaks and valleys are important narrative moments
        sentiment_val = sentiment_scores[i] if i < len(sentiment_scores) else 0.0
        sentiment_importance = abs(sentiment_val)  # high magnitude = important
        if i in sentiment_peaks:
            sentiment_importance += 0.5  # boost peaks
        if i in sentiment_valleys:
            sentiment_importance += 0.3  # valleys also matter

        # --- Visual concreteness ---
        visual_score = 0.5  # default
        if i < len(visual_scores_list):
            vs = visual_scores_list[i]
            if isinstance(vs, dict):
                visual_score = vs.get("visual_score", 0.5)
            elif isinstance(vs, (int, float)):
                visual_score = float(vs)

        # --- Character density ---
        text_lower = seg_text.lower()
        char_mentions = sum(1 for name in main_chars if name in text_lower)
        char_density = min(char_mentions / max(len(main_chars), 1), 1.0)

        # --- Key event presence ---
        event_score = 1.0 if seg_id in event_segments else 0.0

        # --- Position weight (intro and ending get slight boost) ---
        n = len(segments)
        if n > 1:
            pos_ratio = i / (n - 1)
            # Boost beginning and end
            position_weight = 0.3 * (1.0 if pos_ratio < 0.1 or pos_ratio > 0.9 else 0.0)
            # Boost middle (climax zone)
            if 0.5 < pos_ratio < 0.75:
                position_weight += 0.2
        else:
            position_weight = 0.5

        # --- Composite importance score ---
        importance = (
            0.20 * sentiment_importance
            + 0.20 * visual_score
            + 0.20 * char_density
            + 0.15 * event_score
            + 0.10 * position_weight
            + 0.15 * dialogue_action_bonus
        )

        scored_segments.append({
            "segment_id": seg_id,
            "index": i,
            "text": seg_text,
            "title": seg.get("title"),
            "importance": importance,
            "sentiment_value": sentiment_val,
            "visual_score": visual_score,
            "char_density": char_density,
            "has_key_event": bool(event_score),
        })

    if not scored_segments:
        logger.warning("No scorable segments")
        return []

    # --- Step 2: Select top-k with spacing ---
    selected = _select_with_spacing(scored_segments, effective_pages)

    # --- Step 3: Map to story template ---
    scenes = _map_to_template(selected, structure, characters)

    logger.info(
        "Selected %d scenes from %d segments (template: %s)",
        len(scenes), len(segments), template,
    )
    return scenes


def _select_with_spacing(
    scored: list[dict], k: int
) -> list[dict]:
    """Select top-k segments ensuring reasonable spacing (not all from same region)."""
    if len(scored) <= k:
        return sorted(scored, key=lambda s: s["index"])

    n = len(scored)
    # Divide text into k zones, pick best from each zone
    zone_size = max(1, n // k)

    selected = []
    for zone_idx in range(k):
        zone_start = zone_idx * zone_size
        zone_end = min(zone_start + zone_size, n)
        if zone_idx == k - 1:
            zone_end = n  # last zone takes the rest

        zone = scored[zone_start:zone_end]
        if zone:
            best = max(zone, key=lambda s: s["importance"])
            selected.append(best)

    # If we got fewer than k (shouldn't happen), fill from top-scoring unused
    if len(selected) < k:
        selected_ids = {s["segment_id"] for s in selected}
        remaining = sorted(
            [s for s in scored if s["segment_id"] not in selected_ids],
            key=lambda s: s["importance"],
            reverse=True,
        )
        for s in remaining:
            if len(selected) >= k:
                break
            selected.append(s)

    return sorted(selected, key=lambda s: s["index"])


def _map_to_template(
    selected: list[dict],
    structure: list[str],
    characters: list[dict],
) -> list[dict]:
    """Map selected segments to story template beats."""
    scenes = []

    # Determine emotional tone from sentiment value
    def _tone(val: float) -> str:
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

    # Extract character names from segment text
    char_names = [c["name"] for c in characters[:10]]

    for i, seg in enumerate(selected):
        beat = structure[i] if i < len(structure) else f"scene_{i + 1}"

        # Find which characters appear in this segment
        text_lower = seg["text"].lower()
        present_chars = [
            name for name in char_names
            if name.lower() in text_lower
        ]

        # Extract a summary: first 2 sentences
        sentences = [s.strip() for s in seg["text"].replace("\n", " ").split(".") if s.strip()]
        summary = ". ".join(sentences[:2]) + "." if sentences else seg["text"][:200]

        scenes.append({
            "page_number": i + 1,
            "source_segment_id": seg["segment_id"],
            "template_beat": beat,
            "scene_summary": summary[:300],
            "emotional_tone": _tone(seg.get("sentiment_value", 0)),
            "key_characters": present_chars[:5],
            "setting": "",  # Could be extracted with NER location detection
            "importance_score": seg["importance"],
            "visual_score": seg.get("visual_score", 0.5),
            "original_text": seg["text"],
        })

    return scenes
