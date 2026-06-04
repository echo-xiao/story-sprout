"""NLP analysis pipeline for picture book text.

Provides a unified ``analyze_text`` entry point that orchestrates all
analysis modules: segmentation, character extraction, sentiment analysis,
visual scoring, complexity assessment, key event extraction, and
character persona profiling.
"""

from __future__ import annotations

from src.analysis.chapter_split import split_into_segments
from src.analysis.character_extract import extract_characters
from src.analysis.sentiment_curve import analyze_sentiment
from src.analysis.visual_score import score_visual_concreteness
from src.analysis.complexity import assess_complexity
from src.analysis.key_events import extract_key_events
from src.analysis.character_persona import build_character_profiles


def analyze_text(
    text: str,
    chapters: list[dict] | None = None,
) -> dict:
    """Run the full NLP analysis pipeline on a story text.

    Args:
        text: The full story text to analyze.
        chapters: Optional pre-defined chapter boundaries.

    Returns:
        Combined analysis dict with keys:
        - segments: segmented text with ids and metadata
        - characters: extracted character profiles
        - sentiment: sentiment scores, peaks, valleys, arc
        - visual_scores: segments enriched with visual concreteness data
        - complexity: reading level and word difficulty metrics
        - key_events: important story events ranked by importance
        - character_profiles: detailed persona profiles
    """
    if not text or not text.strip():
        return {
            "segments": [],
            "characters": [],
            "sentiment": {
                "scores": [],
                "peaks": [],
                "valleys": [],
                "overall_arc": "flat",
            },
            "visual_scores": [],
            "complexity": {
                "flesch_kincaid_grade": 0.0,
                "avg_sentence_length": 0.0,
                "avg_word_length": 0.0,
                "rare_word_ratio": 0.0,
                "difficult_segments": [],
            },
            "key_events": [],
            "character_profiles": [],
        }

    # 1. Segment the text
    segments = split_into_segments(text, chapters=chapters)

    # 2. Extract characters
    characters = extract_characters(text, segments)

    # 3. Sentiment analysis
    sentiment = analyze_sentiment(segments)

    # 4. Visual concreteness scoring (enriches segment dicts in-place)
    visual_scores = score_visual_concreteness(segments)

    # 5. Complexity assessment
    complexity = assess_complexity(text)

    # 6. Key event extraction
    key_events = extract_key_events(segments, characters, sentiment)

    # 7. Character persona profiling
    character_profiles = build_character_profiles(text, characters)

    return {
        "segments": segments,
        "characters": characters,
        "sentiment": sentiment,
        "visual_scores": visual_scores,
        "complexity": complexity,
        "key_events": key_events,
        "character_profiles": character_profiles,
    }


__all__ = [
    "analyze_text",
    "split_into_segments",
    "extract_characters",
    "analyze_sentiment",
    "score_visual_concreteness",
    "assess_complexity",
    "extract_key_events",
    "build_character_profiles",
]
